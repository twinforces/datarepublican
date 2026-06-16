#!/usr/bin/env python3
"""
geolocate1_processor.py - Lightweight "Phase 1" geolocation step.

Purpose:
- Runs after the full "geolocate" step (colocators must exist first).
- Loads the pre-computed geocode archive cache (fast colocator wins for many addresses).
- Ensures lat/lon columns exist on owner tables.
- Computes loose_colocator (0.5° grid, "same town") on Grants, Charities, and other entities.
- Populates enough data for high-quality Splink runs and early name rule seed generation
  without requiring the full live Census API geocoding pass.

This merges the archive cache loading logic (previously only inside GeocodingAPIProcessor)
with the loose_colocator population logic (previously only inside GrantMatchProcessor).

Intended to be runnable as the "geolocate1" step in the main pipeline.
"""

import os
import gzip
import csv
from typing import List, Tuple, Dict, Any

from database_operations import DatabaseOperations
from logging_utils import log_info, log_error, log_warning
from config import global_config


class Geolocate1Processor:
    """Lightweight processor for early colocator + loose_colocator population."""

    def __init__(self, db_ops: DatabaseOperations):
        self.db_ops = db_ops

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------
    def run(self, max_files: int | None = None) -> int:
        """
        Execute the full geolocate1 sequence:
        1. Ensure Zips table is present (for PO: handling).
        2. Apply geocode archive cache (colocators + some lat/lon on Geocoding).
        3. Populate lat/lon on owner tables from colocators.
        4. Compute loose_colocator (0.5° grid) on Grants, Charities, etc.
        5. Add useful indexes.
        """
        log_info("=== Starting geolocate1 (cache + loose colocator) ===")

        self._ensure_zips_table()

        updated = self.apply_geocode_archive_cache()
        log_info(f"Archive cache applied: {updated} records touched")

        self.populate_lat_lon_and_loose_colocators()

        log_info("=== geolocate1 complete ===")
        return updated

    # ------------------------------------------------------------------
    # Zips table (needed for PO: zip-based lat/lon)
    # ------------------------------------------------------------------
    def _ensure_zips_table(self):
        """Build the Zips lookup table if it doesn't exist (idempotent)."""
        try:
            count = self.db_ops.execute_query("SELECT COUNT(*) FROM Zips").fetchone()[0]
            if count > 0:
                log_info(f"Zips table already has {count:,} rows")
                return
        except Exception:
            pass  # Table doesn't exist yet

        log_info("Building Zips table from US_zips.txt.gz (required for PO: handling)...")
        zip_file_path = os.path.join(os.getcwd(), "US_zips.txt.gz")

        self.db_ops.execute_query("DROP TABLE IF EXISTS Zips;")

        self.db_ops.execute_query("""
        CREATE TABLE Zips (
            country_code   VARCHAR,
            zip            VARCHAR PRIMARY KEY,
            place_name     VARCHAR,
            state_name     VARCHAR,
            state_code     VARCHAR,
            region_name    VARCHAR,
            region_code    VARCHAR,
            extra1         VARCHAR,
            extra2         VARCHAR,
            extra3         VARCHAR,
            lat            DOUBLE,
            lon            DOUBLE,
            accuracy       INTEGER
        );
        """)

        try:
            self.db_ops.execute_query(f"""
            COPY Zips FROM '{zip_file_path}' (
                FORMAT CSV,
                DELIMITER '\t',
                HEADER FALSE,
                COMPRESSION 'gzip'
            );
            """)
            count = self.db_ops.execute_query("SELECT COUNT(*) FROM Zips").fetchone()[0]
            log_info(f"Zips table built with {count:,} rows")
        except Exception as e:
            log_warning(f"Could not load Zips table: {e}. PO: handling may be limited.")
            # Create empty table so later steps don't explode
            self.db_ops.execute_query("CREATE TABLE IF NOT EXISTS Zips (zip VARCHAR PRIMARY KEY, lat DOUBLE, lon DOUBLE);")

        self.db_ops.execute_query("CREATE INDEX IF NOT EXISTS idx_zips_zip ON Zips(zip);")

    # ------------------------------------------------------------------
    # Archive cache loading (extracted + adapted from geocoding_api_processor)
    # ------------------------------------------------------------------
    def apply_geocode_archive_cache(self, cache_file: str = "geocode_archive_distinct.tsv.gz") -> int:
        """Load the pre-geocoded TSV.gz archive and propagate colocators to owners.

        Returns number of Geocoding records updated from archive.
        """
        actual_cache_file = os.path.join(global_config.final_dir, cache_file)

        if not os.path.exists(actual_cache_file):
            log_warning(f"Geocode archive not found at {actual_cache_file} — skipping cache load")
            return 0

        with self.db_ops.acquire_write_conn() as conn:
            already_done = conn.execute("""
                SELECT COUNT(*) FROM Geocoding 
                WHERE geocoding_status = 'Match:Archive' LIMIT 1
            """).fetchone()[0]

            if already_done > 0:
                log_info("Archive cache already applied (found 'Match:Archive' status). Skipping.")
                return 0

            try:
                log_info(f"Streaming geocode cache from {actual_cache_file}...")
                chunk_size = 100000
                total_geocoding = 0
                total_addresses = 0
                chunk_num = 0

                with gzip.open(actual_cache_file, 'rt', encoding='utf-8', errors='replace') as f:
                    conn.execute("BEGIN TRANSACTION")
                    reader = csv.DictReader(f, delimiter='\t')
                    chunk: List[Tuple[str, str]] = []
                    for row in reader:
                        if 'canonical_address' not in row or 'colocator' not in row:
                            continue
                        chunk.append((row['canonical_address'], row['colocator']))
                        if len(chunk) >= chunk_size:
                            chunk_num += 1
                            g, a = self._apply_archive_chunk(conn, chunk, chunk_num)
                            total_geocoding += g
                            total_addresses += a
                            chunk = []
                    if chunk:
                        chunk_num += 1
                        g, a = self._apply_archive_chunk(conn, chunk, chunk_num)
                        total_geocoding += g
                        total_addresses += a
                    conn.commit()

                # Final analyze on affected tables
                for table in ['Geocoding', 'Addresses', 'Charities', 'Grants']:
                    conn.execute(f"VACUUM ANALYZE {table};")
                conn.commit()

                log_info(f"Archive cache load complete: {total_geocoding:,} Geocoding, {total_addresses:,} Addresses updated")
                return total_geocoding

            except Exception as e:
                conn.rollback()
                log_error(f"Archive cache load failed: {e}", exc_info=True)
                raise

    def _apply_archive_chunk(self, conn, chunk: List[Tuple[str, str]], chunk_num: int) -> Tuple[int, int]:
        """Internal chunk processor for the archive (same spirit as the original)."""
        if not chunk:
            return 0, 0

        log_info(f"[geolocate1] Processing archive chunk {chunk_num} ({len(chunk):,} rows)...")

        conn.execute("""
            CREATE OR REPLACE TEMP TABLE chunk_cache (
                canonical_address VARCHAR,
                colocator VARCHAR
            )
        """)

        safe_chunk = [(str(ca or "").replace("'", "''"), str(co or "").replace("'", "''")) for ca, co in chunk]
        batch_size = 2000
        for i in range(0, len(safe_chunk), batch_size):
            sub = safe_chunk[i:i + batch_size]
            values = ", ".join(f"('{ca}', '{co}')" for ca, co in sub)
            conn.execute(f"INSERT INTO chunk_cache VALUES {values}")

        conn.execute("""
            CREATE OR REPLACE TEMP TABLE update_map AS
            SELECT 
                g.geocoding_id,
                c.colocator,
                CASE WHEN c.colocator LIKE 'LL:%' THEN CAST(split_part(c.colocator, ':', 2) AS DOUBLE) ELSE NULL END AS parsed_lat,
                CASE WHEN c.colocator LIKE 'LL:%' THEN CAST(split_part(c.colocator, ':', 3) AS DOUBLE) ELSE NULL END AS parsed_lon
            FROM Geocoding g
            INNER JOIN chunk_cache c ON g.canonical_address = c.canonical_address
            WHERE g.geocoding_status IN (NULL, 'pending', 'owners', 'No_Match')
        """)

        map_count = conn.execute("SELECT COUNT(*) FROM update_map").fetchone()[0]
        if map_count == 0:
            conn.execute("DROP TABLE IF EXISTS chunk_cache; DROP TABLE IF EXISTS update_map;")
            return 0, 0

        conn.execute("""
            UPDATE Geocoding g
            SET geocoding_status = 'Match:Archive',
                last_attempt = CURRENT_TIMESTAMP,
                attempt_count = COALESCE(g.attempt_count, 0) + 1,
                matched_address = 'Loaded from geocode_archive (geolocate1)',
                latitude = m.parsed_lat,
                longitude = m.parsed_lon
            FROM update_map m
            WHERE g.geocoding_id = m.geocoding_id
        """)

        conn.execute("""
            UPDATE Addresses a
            SET colocator = m.colocator
            FROM update_map m
            WHERE a.geocoding_id = m.geocoding_id
        """)

        # Propagate colocator to main owner tables (tight colocator)
        for table, id_col, addr_type in [
            ("Charities", "charity_id", "charity"),
            ("Grants", "grant_id", "grant"),
            ("Officers", "officer_id", "officer"),
            ("Contractors", "contractor_id", "contractor"),
            ("PoliticalContributions", "political_id", "politicalcontribution"),
        ]:
            try:
                conn.execute(f"""
                    UPDATE {table} t
                    SET colocator = m.colocator
                    FROM (
                        SELECT DISTINCT a.owner_id, ANY_VALUE(m.colocator) AS colocator
                        FROM update_map m
                        JOIN Addresses a ON a.geocoding_id = m.geocoding_id
                        WHERE a.owner_id IS NOT NULL AND a.address_type = '{addr_type}'
                        GROUP BY a.owner_id
                    ) m
                    WHERE t.{id_col} = m.owner_id
                """)
            except Exception as ex:
                log_warning(f"Could not propagate to {table}: {ex}")

        conn.execute("DROP TABLE IF EXISTS chunk_cache; DROP TABLE IF EXISTS update_map;")
        return map_count, map_count  # rough counts

    # ------------------------------------------------------------------
    # Lat/lon + loose_colocator population (merged from grant_match_processor)
    # ------------------------------------------------------------------
    def populate_lat_lon_and_loose_colocators(self):
        """Ensure lat/lon exist on key tables and compute loose_colocator (0.5° grid)."""
        log_info("Populating lat/lon (from colocator) and loose_colocator (0.5° grid)...")

        # Add columns where needed (idempotent)
        for table in ["Grants", "Charities", "Officers", "Contractors", "PoliticalContributions"]:
            self.db_ops.execute_query(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS lat DOUBLE;")
            self.db_ops.execute_query(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS lon DOUBLE;")

        # Backfill lat/lon from colocator for each table (PO: via Zips, LL: parse)
        self._backfill_lat_lon_for_table("Grants")
        self._backfill_lat_lon_for_table("Charities")
        self._backfill_lat_lon_for_table("Officers")
        self._backfill_lat_lon_for_table("Contractors")
        self._backfill_lat_lon_for_table("PoliticalContributions")

        # Compute loose_colocator (same 0.5° rounding logic)
        loose_sql = """
            UPDATE {table}
            SET loose_colocator = 'LL:' || ROUND(lat / 0.5) * 0.5 || ':' || ROUND(lon / 0.5) * 0.5
            WHERE loose_colocator IS NULL AND lat IS NOT NULL
        """

        for table in ["Grants", "Charities", "Officers", "Contractors", "PoliticalContributions"]:
            self.db_ops.execute_query(loose_sql.format(table=table))

        # Helpful indexes for Splink + matching
        self.db_ops.execute_query("CREATE INDEX IF NOT EXISTS idx_grants_loose_colocator ON Grants(loose_colocator);")
        self.db_ops.execute_query("CREATE INDEX IF NOT EXISTS idx_charities_loose_colocator ON Charities(loose_colocator);")
        self.db_ops.execute_query("CREATE INDEX IF NOT EXISTS idx_grants_lat_lon ON Grants(lat, lon);")
        self.db_ops.execute_query("CREATE INDEX IF NOT EXISTS idx_charities_lat_lon ON Charities(lat, lon);")

        log_info("lat/lon + loose_colocator population complete for geolocate1")

    def _backfill_lat_lon_for_table(self, table: str):
        id_col = {
            "Grants": "grant_id",
            "Charities": "charity_id",
            "Officers": "officer_id",
            "Contractors": "contractor_id",
            "PoliticalContributions": "political_id",
        }.get(table, "id")

        # PO: via Zips
        self.db_ops.execute_query(f"""
            UPDATE {table} t
            SET lat = z.lat, lon = z.lon
            FROM Zips z
            WHERE t.colocator LIKE 'PO:%'
              AND z.zip = split_part(t.colocator, ':', 3)
              AND t.lat IS NULL
        """)

        # LL: direct parse
        self.db_ops.execute_query(f"""
            UPDATE {table} t
            SET lat = split_part(t.colocator, ':', 2)::DOUBLE,
                lon = split_part(t.colocator, ':', 3)::DOUBLE
            WHERE t.colocator LIKE 'LL:%' AND t.lat IS NULL
        """)