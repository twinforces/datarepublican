#!/usr/bin/env python3
"""
geolocate_prev_processor.py - Fast geolocation from prior-run archive cache.

Bookend for geolocate_archive. Runs first in the geolocate trilogy
(before geolocate_new / geolocate_grok / geolocate_archive):

0. Delete empty address shells (no street/city/zip, no geocoding_id, no colocator).
   XML often emits officer/contractor Address rows with no USAddress content;
   address-dedup never creates a Geocoding row for blank canonical_address, so
   they never collapse to one master geocode and only waste space / pollute stats.
1. Load geocode_archive_distinct.tsv.gz → Geocoding (colocator + geocoding_status)
   Columns: canonical_address, colocator, [geocoding_status]
   Older 2-column archives (canonical + colocator only) still work.
2. Propagate colocators to owner tables via Addresses join
3. Backfill lat/lon from colocator (PO: / LL:)
4. Compute loose_colocator (0.5° grid) later on Addresses + owners —
   loose_colocator is NOT stored on Geocoding and is not part of the archive.
"""

from __future__ import annotations

import csv
import gzip
import os
from typing import List, Optional, Tuple

from database_operations import DatabaseOperations
from logging_utils import log_info, log_error, log_warning
from config import global_config

# (canonical_address, colocator, geocoding_status)
ArchiveChunkRow = Tuple[str, str, str]

# Address rows that carry no location signal and never entered Geocoding.
# Keep FA:/PO:/LL: colocators even if street fields are blank (foreign / PO).
EMPTY_ADDRESS_WHERE = """
    geocoding_id IS NULL
    AND COALESCE(TRIM(address_line1), '') = ''
    AND COALESCE(TRIM(address_line2), '') = ''
    AND COALESCE(TRIM(city), '') = ''
    AND COALESCE(TRIM(zip_code), '') = ''
    AND COALESCE(TRIM(po_box), '') = ''
    AND (colocator IS NULL OR TRIM(colocator) = '')
"""


class GeolocatePrevProcessor:
    """Archive-cache geolocation + later loose_colocator population on Addresses/owners."""

    def __init__(self, db_ops: DatabaseOperations):
        self.db_ops = db_ops

    def run(self, max_files: int | None = None) -> int:
        log_info("=== Starting geolocate_prev (archive cache + loose colocator) ===")

        deleted = self.delete_empty_address_shells()
        log_info(f"Empty address shells removed: {deleted:,}")

        self._ensure_zips_table()
        self.db_ops.execute_query("ALTER TABLE Geocoding ADD COLUMN IF NOT EXISTS colocator VARCHAR;")

        updated = self.apply_geocode_archive_cache()
        log_info(f"Archive cache applied: {updated:,} Geocoding records touched")

        self.populate_lat_lon_and_loose_colocators()

        log_info("=== geolocate_prev complete ===")
        return updated

    def delete_empty_address_shells(self) -> int:
        """
        Remove Addresses with no geocodable content.

        Why they exist: officer/contractor build_address() always creates a row;
        XML often omits USAddress. Address dedup only groups non-empty
        canonical_address, so blanks never share one Geocoding record — each
        stays a self-master orphan with null geocoding_id.

        Safe: requires null geocoding_id and no colocator (preserves FA:/PO: shells).
        """
        log_info("Deleting empty address shells (no fields, no geocoding_id, no colocator)...")
        print("[geolocate_prev] Counting empty address shells...", flush=True)

        with self.db_ops.acquire_write_conn() as conn:
            # Breakdown for the log before delete
            try:
                by_type = conn.execute(f"""
                    SELECT COALESCE(address_type, '(null)'), COUNT(*)::BIGINT
                    FROM Addresses
                    WHERE {EMPTY_ADDRESS_WHERE}
                    GROUP BY 1
                    ORDER BY 2 DESC
                """).fetchall()
                for at, c in by_type:
                    log_info(f"  empty shell {at}: {c:,}")
                    print(f"[geolocate_prev]   empty shell {at}: {c:,}", flush=True)
            except Exception as e:
                log_warning(f"Could not pre-count empty shells by type: {e}")

            before = conn.execute(f"""
                SELECT COUNT(*) FROM Addresses WHERE {EMPTY_ADDRESS_WHERE}
            """).fetchone()[0]
            if before == 0:
                log_info("No empty address shells to delete")
                print("[geolocate_prev] No empty address shells", flush=True)
                return 0

            print(f"[geolocate_prev] Deleting {before:,} empty address shells...", flush=True)
            conn.execute("BEGIN TRANSACTION")
            try:
                # DuckDB DELETE returns affected row count via fetchone on some versions
                result = conn.execute(f"""
                    DELETE FROM Addresses
                    WHERE {EMPTY_ADDRESS_WHERE}
                """)
                deleted = before
                try:
                    row = result.fetchone() if result is not None else None
                    if row and row[0] is not None:
                        deleted = int(row[0])
                except Exception:
                    pass
                # Verify
                remaining = conn.execute(f"""
                    SELECT COUNT(*) FROM Addresses WHERE {EMPTY_ADDRESS_WHERE}
                """).fetchone()[0]
                if remaining:
                    # If DELETE count unreliable, use before - remaining
                    deleted = before - remaining
                conn.commit()
            except Exception:
                try:
                    conn.rollback()
                except Exception:
                    pass
                raise

            log_info(f"Deleted {deleted:,} empty address shells ({remaining:,} remain)")
            print(
                f"[geolocate_prev] Deleted {deleted:,} empty address shells "
                f"({remaining:,} remain)",
                flush=True,
            )
            return int(deleted)

    def _ensure_zips_table(self):
        """Build the Zips lookup table if it doesn't exist (idempotent)."""
        try:
            count = self.db_ops.execute_query("SELECT COUNT(*) FROM Zips").fetchone()[0]
            if count > 0:
                log_info(f"Zips table already has {count:,} rows")
                return
        except Exception:
            pass

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
            self.db_ops.execute_query(
                "CREATE TABLE IF NOT EXISTS Zips (zip VARCHAR PRIMARY KEY, lat DOUBLE, lon DOUBLE);"
            )

        self.db_ops.execute_query("CREATE INDEX IF NOT EXISTS idx_zips_zip ON Zips(zip);")

    def _tune_conn(self, conn):
        conn.execute("SET preserve_insertion_order=false")
        conn.execute("SET threads=1")
        conn.execute("SET memory_limit='12GB'")

    def _archive_already_applied_count(self, conn) -> int:
        """Rows that look like archive/API wins (idempotency skip signal)."""
        return conn.execute("""
            SELECT COUNT(*) FROM Geocoding
            WHERE colocator IS NOT NULL AND TRIM(colocator) != ''
              AND (
                    geocoding_status = 'Match:Archive'
                 OR geocoding_status LIKE 'Match:%'
                 OR geocoding_status LIKE 'grok:%'
              )
        """).fetchone()[0]

    def apply_geocode_archive_cache(self, cache_file: str = "geocode_archive_distinct.tsv.gz") -> int:
        """Load pre-geocoded archive and propagate colocators to owners."""
        actual_cache_file = os.path.join(global_config.final_dir, cache_file)

        if not os.path.exists(actual_cache_file):
            log_warning(f"Geocode archive not found at {actual_cache_file} — skipping cache load")
            return 0

        with self.db_ops.acquire_write_conn() as conn:
            already_done = self._archive_already_applied_count(conn)

        # Production post-geolocate has ~13M+ matched rows; skip re-stream when saturated.
        archive_skip_threshold = 2_400_000
        try:
            if already_done >= archive_skip_threshold:
                log_info(
                    f"Archive cache already applied ({already_done:,} matched/colocated) — skipping TSV stream"
                )
                print(
                    f"[geolocate_prev] Archive already loaded ({already_done:,} rows), skipping stream",
                    flush=True,
                )
                total_geocoding = already_done
            else:
                if already_done > 0:
                    log_info(f"Resuming archive cache ({already_done:,} rows already matched/colocated)")
                log_info(f"Streaming geocode cache from {actual_cache_file}...")
                print(f"[geolocate_prev] Streaming archive from {actual_cache_file}", flush=True)
                chunk_size = 20000
                total_geocoding = 0
                chunk_num = 0

                with gzip.open(actual_cache_file, "rt", encoding="utf-8", errors="replace") as f:
                    reader = csv.DictReader(f, delimiter="\t")
                    chunk: List[ArchiveChunkRow] = []
                    for row in reader:
                        parsed = self._parse_archive_row(row)
                        if not parsed:
                            continue
                        chunk.append(parsed)
                        if len(chunk) >= chunk_size:
                            chunk_num += 1
                            total_geocoding += self._commit_archive_chunk(
                                chunk, chunk_num, total_geocoding
                            )
                            chunk = []
                    if chunk:
                        chunk_num += 1
                        total_geocoding += self._commit_archive_chunk(
                            chunk, chunk_num, total_geocoding
                        )

            with self.db_ops.acquire_write_conn() as conn:
                self._tune_conn(conn)
                self._propagate_colocators_to_owners(conn)
                for table in ["Geocoding", "Addresses", "Charities", "Grants"]:
                    print(f"[geolocate_prev] VACUUM ANALYZE {table}...", flush=True)
                    conn.execute(f"VACUUM ANALYZE {table};")

            log_info(f"Archive cache load complete: {total_geocoding:,} Geocoding records updated")
            print(f"[geolocate_prev] Archive cache complete: {total_geocoding:,} rows", flush=True)
            return total_geocoding

        except Exception as e:
            log_error(f"Archive cache load failed: {e}", exc_info=True)
            raise

    @staticmethod
    def _parse_archive_row(row: dict) -> Optional[ArchiveChunkRow]:
        """Normalize a TSV dict into (canon, colocator, status)."""
        if not row:
            return None
        canon = (row.get("canonical_address") or "").strip()
        coloc = (row.get("colocator") or "").strip()
        if not canon or not coloc:
            return None
        status = (row.get("geocoding_status") or "").strip()
        if not status:
            # Legacy 2-col archive, or status omitted
            status = coloc if coloc.startswith("grok:") else "Match:Archive"
        return (canon, coloc, status)

    def _commit_archive_chunk(
        self, chunk: List[ArchiveChunkRow], chunk_num: int, total_so_far: int
    ) -> int:
        """Apply one archive chunk on a fresh write connection to avoid memory accumulation."""
        with self.db_ops.acquire_write_conn() as conn:
            self._tune_conn(conn)
            try:
                conn.execute("BEGIN TRANSACTION")
                updated = self._apply_archive_chunk(conn, chunk, chunk_num)
                conn.commit()
                print(
                    f"[geolocate_prev] Committed chunk {chunk_num} "
                    f"(+{updated:,}, {total_so_far + updated:,} geocoding rows total)",
                    flush=True,
                )
                return updated
            except Exception:
                try:
                    conn.rollback()
                except Exception:
                    pass
                raise

    def _apply_archive_chunk(self, conn, chunk: List[ArchiveChunkRow], chunk_num: int) -> int:
        if not chunk:
            return 0

        log_info(f"[geolocate_prev] Processing archive chunk {chunk_num} ({len(chunk):,} rows)...")
        print(f"[geolocate_prev] Processing archive chunk {chunk_num} ({len(chunk):,} rows)...", flush=True)

        conn.execute("""
            CREATE OR REPLACE TEMP TABLE chunk_cache (
                canonical_address VARCHAR,
                colocator VARCHAR,
                geocoding_status VARCHAR
            )
        """)

        def _esc(s: str) -> str:
            return str(s or "").replace("'", "''")

        safe_chunk = [(_esc(ca), _esc(co), _esc(st)) for ca, co, st in chunk]
        batch_size = 2000
        for i in range(0, len(safe_chunk), batch_size):
            sub = safe_chunk[i : i + batch_size]
            values = ", ".join(f"('{ca}', '{co}', '{st}')" for ca, co, st in sub)
            conn.execute(f"INSERT INTO chunk_cache VALUES {values}")

        conn.execute("""
            CREATE OR REPLACE TEMP TABLE update_map AS
            SELECT
                g.geocoding_id,
                c.colocator,
                CASE
                    WHEN NULLIF(TRIM(c.geocoding_status), '') IS NOT NULL
                        THEN TRIM(c.geocoding_status)
                    WHEN c.colocator LIKE 'grok:%' THEN c.colocator
                    ELSE 'Match:Archive'
                END AS target_status,
                CASE WHEN c.colocator LIKE 'LL:%'
                     THEN TRY_CAST(split_part(c.colocator, ':', 2) AS DOUBLE)
                     ELSE NULL END AS parsed_lat,
                CASE WHEN c.colocator LIKE 'LL:%'
                     THEN TRY_CAST(split_part(c.colocator, ':', 3) AS DOUBLE)
                     ELSE NULL END AS parsed_lon
            FROM Geocoding g
            INNER JOIN chunk_cache c ON g.canonical_address = c.canonical_address
            WHERE g.geocoding_status IS NULL
               OR g.geocoding_status IN (
                    'pending', 'owners', 'No_Match',
                    'pending_api', 'geocode_tail', 'grok_pending'
               )
        """)

        map_count = conn.execute("SELECT COUNT(*) FROM update_map").fetchone()[0]
        if map_count == 0:
            conn.execute("DROP TABLE IF EXISTS chunk_cache; DROP TABLE IF EXISTS update_map;")
            return 0

        conn.execute("""
            UPDATE Geocoding g
            SET geocoding_status = m.target_status,
                last_attempt = CURRENT_TIMESTAMP,
                attempt_count = COALESCE(g.attempt_count, 0) + 1,
                matched_address = CASE
                    WHEN m.target_status LIKE 'grok:%' THEN 'Loaded from geocode_archive (grok failure)'
                    ELSE 'Loaded from geocode_archive (geolocate_prev)'
                END,
                colocator = m.colocator,
                latitude = m.parsed_lat,
                longitude = m.parsed_lon
            FROM update_map m
            WHERE g.geocoding_id = m.geocoding_id
        """)

        conn.execute("DROP TABLE IF EXISTS chunk_cache; DROP TABLE IF EXISTS update_map;")
        return map_count

    def _propagate_colocators_to_owners(self, conn):
        """Owner colocator backfill via Geocoding join (skip Addresses UPDATE — DuckDB index bug)."""
        log_info("Propagating colocators from Geocoding to owner tables...")
        print("[geolocate_prev] Propagating colocators to owner tables (via Geocoding)...", flush=True)
        for table, id_col, addr_type in [
            ("Charities", "charity_id", "charity"),
            ("Grants", "grant_id", "grant"),
            ("Officers", "officer_id", "officer"),
            ("Contractors", "contractor_id", "contractor"),
            ("PoliticalContributions", "political_id", "politicalcontribution"),
        ]:
            try:
                print(f"[geolocate_prev] Owner colocator → {table}...", flush=True)
                conn.execute(f"""
                    UPDATE {table} t
                    SET colocator = sub.colocator
                    FROM (
                        SELECT a.owner_id, ANY_VALUE(g.colocator) AS colocator
                        FROM Addresses a
                        INNER JOIN Geocoding g ON a.geocoding_id = g.geocoding_id
                        WHERE a.address_type = '{addr_type}'
                          AND g.colocator IS NOT NULL
                          AND (
                                g.geocoding_status = 'Match:Archive'
                             OR g.geocoding_status LIKE 'Match:%'
                             OR g.geocoding_status LIKE 'grok:%'
                          )
                          AND a.owner_id IS NOT NULL
                        GROUP BY a.owner_id
                    ) sub
                    WHERE t.{id_col} = sub.owner_id
                      AND (t.colocator IS NULL OR t.colocator = '')
                """)
            except Exception as ex:
                log_warning(f"Could not propagate to {table}: {ex}")

    def populate_lat_lon_and_loose_colocators(self):
        """
        After archive colocators are on Geocoding/owners:
        backfill lat/lon, then compute loose_colocator on Addresses + owner tables.
        Geocoding does not store loose_colocator.
        """
        log_info("Populating lat/lon (from colocator) and loose_colocator on Addresses/owners...")

        # Ensure Addresses has loose_colocator (schema has it; older DBs may not)
        self.db_ops.execute_query(
            "ALTER TABLE Addresses ADD COLUMN IF NOT EXISTS loose_colocator VARCHAR;"
        )
        self.db_ops.execute_query("ALTER TABLE Addresses ADD COLUMN IF NOT EXISTS latitude DOUBLE;")
        self.db_ops.execute_query("ALTER TABLE Addresses ADD COLUMN IF NOT EXISTS longitude DOUBLE;")

        for table in ["Grants", "Charities", "Officers", "Contractors", "PoliticalContributions"]:
            self.db_ops.execute_query(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS lat DOUBLE;")
            self.db_ops.execute_query(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS lon DOUBLE;")
            self.db_ops.execute_query(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS loose_colocator VARCHAR;")

        for table in ["Grants", "Charities", "Officers", "Contractors", "PoliticalContributions"]:
            self._backfill_lat_lon_for_table(table)

        # Addresses: copy lat/lon from Geocoding, then loose grid from lat/lon
        self.db_ops.execute_query("""
            UPDATE Addresses a
            SET latitude = g.latitude,
                longitude = g.longitude,
                colocator = COALESCE(NULLIF(TRIM(a.colocator), ''), g.colocator)
            FROM Geocoding g
            WHERE a.geocoding_id = g.geocoding_id
              AND g.latitude IS NOT NULL
              AND a.latitude IS NULL
        """)
        self.db_ops.execute_query("""
            UPDATE Addresses a
            SET latitude = TRY_CAST(split_part(g.colocator, ':', 2) AS DOUBLE),
                longitude = TRY_CAST(split_part(g.colocator, ':', 3) AS DOUBLE),
                colocator = COALESCE(NULLIF(TRIM(a.colocator), ''), g.colocator)
            FROM Geocoding g
            WHERE a.geocoding_id = g.geocoding_id
              AND a.latitude IS NULL
              AND g.colocator LIKE 'LL:%'
        """)
        self.db_ops.execute_query("""
            UPDATE Addresses
            SET loose_colocator = 'LL:' || ROUND(latitude / 0.5) * 0.5
                                      || ':' || ROUND(longitude / 0.5) * 0.5
            WHERE (loose_colocator IS NULL OR TRIM(loose_colocator) = '')
              AND latitude IS NOT NULL AND longitude IS NOT NULL
        """)

        # Owners: derive loose from their lat/lon (filled above)
        loose_sql = """
            UPDATE {table}
            SET loose_colocator = 'LL:' || ROUND(lat / 0.5) * 0.5 || ':' || ROUND(lon / 0.5) * 0.5
            WHERE (loose_colocator IS NULL OR loose_colocator = '') AND lat IS NOT NULL
        """
        for table in ["Grants", "Charities", "Officers", "Contractors", "PoliticalContributions"]:
            self.db_ops.execute_query(loose_sql.format(table=table))

        self.db_ops.execute_query(
            "CREATE INDEX IF NOT EXISTS idx_addresses_loose_colocator ON Addresses(loose_colocator);"
        )
        self.db_ops.execute_query("CREATE INDEX IF NOT EXISTS idx_grants_loose_colocator ON Grants(loose_colocator);")
        self.db_ops.execute_query("CREATE INDEX IF NOT EXISTS idx_charities_loose_colocator ON Charities(loose_colocator);")
        self.db_ops.execute_query("CREATE INDEX IF NOT EXISTS idx_grants_lat_lon ON Grants(lat, lon);")
        self.db_ops.execute_query("CREATE INDEX IF NOT EXISTS idx_charities_lat_lon ON Charities(lat, lon);")

        log_info("lat/lon + loose_colocator population complete (Addresses + owners)")

    def _backfill_lat_lon_for_table(self, table: str):
        owner_meta = {
            "Grants": ("grant_id", "grant"),
            "Charities": ("charity_id", "charity"),
            "Officers": ("officer_id", "officer"),
            "Contractors": ("contractor_id", "contractor"),
            "PoliticalContributions": ("political_id", "politicalcontribution"),
        }
        id_col, addr_type = owner_meta[table]
        self.db_ops.execute_query(f"""
            UPDATE {table} t
            SET colocator = COALESCE(NULLIF(t.colocator, ''), sub.colocator)
            FROM (
                SELECT a.owner_id, ANY_VALUE(g.colocator) AS colocator
                FROM Addresses a
                INNER JOIN Geocoding g ON a.geocoding_id = g.geocoding_id
                WHERE a.address_type = '{addr_type}'
                  AND g.colocator IS NOT NULL
                  AND a.owner_id IS NOT NULL
                GROUP BY a.owner_id
            ) sub
            WHERE t.{id_col} = sub.owner_id
              AND (t.colocator IS NULL OR t.colocator = '')
        """)
        self.db_ops.execute_query(f"""
            UPDATE {table} t
            SET lat = z.lat, lon = z.lon
            FROM Zips z
            WHERE t.colocator LIKE 'PO:%'
              AND z.zip = split_part(t.colocator, ':', 3)
              AND t.lat IS NULL
        """)
        self.db_ops.execute_query(f"""
            UPDATE {table} t
            SET lat = TRY_CAST(split_part(t.colocator, ':', 2) AS DOUBLE),
                lon = TRY_CAST(split_part(t.colocator, ':', 3) AS DOUBLE)
            WHERE t.colocator LIKE 'LL:%' AND t.lat IS NULL
        """)
        self.db_ops.execute_query(f"""
            UPDATE {table} t
            SET lat = g.latitude, lon = g.longitude
            FROM Addresses a
            INNER JOIN Geocoding g ON a.geocoding_id = g.geocoding_id
            WHERE t.{id_col} = a.owner_id
              AND a.address_type = '{addr_type}'
              AND t.lat IS NULL
              AND g.latitude IS NOT NULL
        """)
