#!/usr/bin/env python3
"""
grant_match_processor.py - Processor for matching grants to charities
"""

from typing import List, Dict, Any, Tuple, Optional
import queue
import re
import os
from database_operations import DatabaseOperations, DatabaseOperation, DatabaseOperationType
from logging_utils import log_info, log_debug
from config import global_config
from base_processor import BaseProcessor, WorkUnit
from pending_database_context import PendingDatabaseContext
from collections import defaultdict
from countryCodes import iso3166_alpha2  # ← from your generate_foreign_update.py
from models import AuthoritativeEin
import hashlib

COMPANY_SUFFIX_REGEX = re.compile(r'\\s+(INC|CORP|LLC|FOUNDATION|MINISTRY|ASSOCIATION|CHURCH)$', re.IGNORECASE)
    
def word_jaccard(name1: str, name2: str) -> float:
    """Compute Jaccard similarity on word sets"""
    if not name1 or not name2:
        return 0.0
    if name1.lower() == name2.lower():
        return 1.0
    cleaned1 = COMPANY_SUFFIX_REGEX.sub('', name1.lower()).strip()
    cleaned2 = COMPANY_SUFFIX_REGEX.sub('', name2.lower()).strip()
    if cleaned1 == cleaned2:
        return 1.0
    words1 = set(cleaned1.lower().split())
    words2 = set(cleaned2.lower().split())
    intersection = len(words1.intersection(words2))
    union = len(words1.union(words2))
    return intersection / union if union else 0.0

class GrantMatchProcessor(BaseProcessor):
    """Processor for matching grants to charities"""

    def __init__(self, db_ops: DatabaseOperations):
        super().__init__(db_ops)
        self.batch_size = 1000
        self.jaccard_threshold = 0.7
        self.distance_km = 40.23  # 25 mi in km
    
    def match_grants(self) -> int:
        """Run the grant matching process"""

        # === VERY FIRST STEP: Foreign EINs ===
        self.apply_foreign_eins()
        self.build_zips_table()

        # === NEW STEP #1: lat/lon population (idempotent) ===
        self.populate_lat_lon_columns()

        # === Tier 1-3 initialization (only if needed) ===
        table_exists = self.db_ops.execute_query(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='AuthoritativeEin'"
        ).fetchone() is not None

        has_rows = False
        if table_exists:
            has_rows = self.db_ops.execute_query(
                "SELECT 1 FROM AuthoritativeEin LIMIT 1"
            ).fetchone() is not None

        if not table_exists or not has_rows:
            log_info("AuthoritativeEin table missing or empty → running full initialization")
            self.build_authoritative_ein_table()
            self.mass_pre_match_exact_colocator()
            self.mass_pre_match_loose_colocator()
            self.backfill_gins_for_remaining_floating()
        else:
            log_info("AuthoritativeEin table exists → skipping initialization")

        # === Tier 4: Parallel matching on remaining loose grants ===
        processed = self.process_parallel(global_config.max_files, global_config.workers)

        return processed
    
    def apply_foreign_eins(self):
        """Foreign EINs as the very first step. Updates Grants + populates Backfill."""
        log_info("Applying foreign EINs from country codes...")

        for iso, data in iso3166_alpha2.items():
            ein = data["number"]
            conditions = [f"colocator = 'FA:{iso}'"]
            # Add FIPS variants if any
            # (your original script logic)
            update_sql = f"""
            UPDATE Grants 
            SET recipient_ein = '{ein}'
            WHERE { ' OR '.join(conditions) };
            """
            self.db_ops.execute_query(update_sql)

            # Also populate Backfill
            insert_sql = """
            INSERT INTO Backfill (recipient_ein, name, source)
            SELECT DISTINCT ?, grantee_name, 'foreign'
            FROM Grants 
            WHERE recipient_ein = ? 
            ON CONFLICT (recipient_ein) DO NOTHING
            """
            self.db_ops.execute_query(insert_sql, [ein, ein])

        log_info("Foreign EINs applied and Backfill populated")

    def build_authoritative_ein_table(self):
        """Rebuild AuthoritativeEin with BOTH colocator and loose_colocator."""
        log_info("Rebuilding AuthoritativeEin with colocator + loose_colocator...")

        self.db_ops.execute_query("DROP TABLE IF EXISTS AuthoritativeEin;")

        self.db_ops.execute_query("""
        CREATE TABLE AuthoritativeEin (
            name            VARCHAR NOT NULL,
            colocator       VARCHAR,           -- exact original
            loose_colocator VARCHAR,           -- 0.5° grid
            ein             VARCHAR(9) NOT NULL,
            count           INTEGER,
            PRIMARY KEY (name, colocator, loose_colocator)
        );
        """)

        self.db_ops.execute_query("""
        CREATE INDEX idx_an_name_colocator ON AuthoritativeEin(name, colocator);
        CREATE INDEX idx_an_name_loose     ON AuthoritativeEin(name, loose_colocator);
        """)

        # Global rows (most common EIN per name)
        self.db_ops.execute_query("""
        INSERT INTO AuthoritativeEin (name, colocator, loose_colocator, ein, count)
        WITH ranked AS (
            SELECT grantee_name, recipient_ein, COUNT(*) AS cnt,
                   ROW_NUMBER() OVER (PARTITION BY grantee_name ORDER BY COUNT(*) DESC) AS rn
            FROM Grants
            WHERE recipient_ein IS NOT NULL 
              AND recipient_ein != '8686'
              AND recipient_ein NOT LIKE '70%'
            GROUP BY grantee_name, recipient_ein
        )
        SELECT grantee_name, 'NULL', 'NULL', recipient_ein, cnt
        FROM ranked WHERE rn = 1;
        """)

        # Specific rows — both exact and loose
        self.db_ops.execute_query("""
        INSERT INTO AuthoritativeEin (name, colocator, loose_colocator, ein, count)
        WITH ranked AS (
            SELECT grantee_name, colocator, loose_colocator, recipient_ein, COUNT(*) AS cnt,
                   ROW_NUMBER() OVER (PARTITION BY grantee_name, colocator, loose_colocator 
                                      ORDER BY COUNT(*) DESC) AS rn
            FROM Grants
            WHERE recipient_ein IS NOT NULL 
              AND recipient_ein != '8686'
              AND recipient_ein NOT LIKE '70%'
              AND (colocator IS NOT NULL AND loose_colocator IS NOT NULL)
            GROUP BY grantee_name, colocator, loose_colocator, recipient_ein
        )
        SELECT grantee_name, colocator, loose_colocator, recipient_ein, cnt
        FROM ranked WHERE rn = 1;
        """)

        log_info("AuthoritativeEin rebuilt with both colocator and loose_colocator")

    def mass_pre_match_exact_colocator(self):
        """First pass: exact colocator match (best case)."""
        self.db_ops.execute_query("""
        UPDATE Grants g
        SET recipient_ein = a.ein
        FROM AuthoritativeEin a
        WHERE g.grantee_name = a.name
          AND g.colocator = a.colocator
          AND (g.recipient_ein IS NULL OR g.recipient_ein = '8686');
        """)
        log_info("Mass pre-match on exact colocator complete")

    def mass_pre_match_loose_colocator(self):
        """Second pass: loose_colocator match (weeds)."""
        self.db_ops.execute_query("""
        UPDATE Grants g
        SET recipient_ein = a.ein
        FROM AuthoritativeEin a
        WHERE g.grantee_name = a.name
          AND g.loose_colocator = a.loose_colocator
          AND (g.recipient_ein IS NULL OR g.recipient_ein = '8686');
        """)
        log_info("Mass pre-match on loose_colocator complete")

    def backfill_gins_for_remaining_floating(self):
        """Step 3: GINs for remaining floating grants using full SHA256."""
        self.db_ops.execute_query("""
        drop index idx_backfill_recipient_ein; CREATE UNIQUE INDEX idx_backfill_recipient_ein ON Backfill (recipient_ein);
        """)

        self.db_ops.execute_query("""
       CREATE TEMP TABLE temp_gins AS
        SELECT DISTINCT 
            g.grantee_name,
            '70' || HEX(SHA256(
                UPPER(TRIM(REGEXP_REPLACE(g.grantee_name, '\\s+(INC|CORP|LLC|FOUNDATION|MINISTRY|ASSOCIATION|CHURCH)$', '', 'gi')))
            )) AS generated_gin
        FROM Grants g
        WHERE (g.recipient_ein IS NULL OR g.recipient_ein = '8686')
          AND g.colocator IS NULL;
        """)

        self.db_ops.execute_query("""
        INSERT INTO Backfill (recipient_ein, name, source)
        SELECT generated_gin, grantee_name, 'gin'
        FROM temp_gins
        ON CONFLICT (recipient_ein) DO NOTHING;
        """)

        self.db_ops.execute_query("""
        UPDATE Grants g
        SET recipient_ein = t.generated_gin
        FROM temp_gins t
        WHERE g.grantee_name = t.grantee_name
          AND g.colocator IS NULL
          AND (g.recipient_ein IS NULL OR g.recipient_ein = '8686');
        """)

        self.db_ops.execute_query("DROP TABLE IF EXISTS temp_gins;")
        log_info("Backfill full-SHA256 GINs created for remaining floating grants")
        
    def build_zips_table(self):
        """Build the Zips table from US_zips.txt.gz (12 columns, deduplicate on zip)."""
        log_info("Building Zips table from US_zips.txt.gz...")

        # Build path relative to current working directory
        zip_file_path = os.path.join(os.getcwd(), "US_zips.txt.gz")

        # Drop and recreate clean Zips table
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
            lat       DOUBLE,
            lon      DOUBLE,
            accuracy       INTEGER
        );
        """)

        # Step 1: Load raw data into temp table
        self.db_ops.execute_query(f"""
        CREATE TEMP TABLE zips_raw AS
        SELECT * FROM read_csv_auto('{zip_file_path}',
            delim = '\t',
            header = false,
            columns = {{
                'country_code': 'VARCHAR',
                'zip': 'VARCHAR',
                'place_name': 'VARCHAR',
                'state_name': 'VARCHAR',
                'state_code': 'VARCHAR',
                'region_name': 'VARCHAR',
                'region_code': 'VARCHAR',
                'extra1': 'VARCHAR',
                'extra2': 'VARCHAR',
                'lat': 'DOUBLE',
                'lon': 'DOUBLE',
                'accuracy': 'INTEGER'
            }}
        );
        """)

        # Step 2: Insert into final table with deduplication (first row per zip wins)
        self.db_ops.execute_query("""
        INSERT INTO Zips
        SELECT 
            country_code,
            zip,
            place_name,
            state_name,
            state_code,
            region_name,
            region_code,
            extra1,
            extra2,
            lat,
            lon,
            accuracy
        FROM (
            SELECT *,
                   ROW_NUMBER() OVER (PARTITION BY zip ORDER BY 1) AS rn  -- first row per zip
            FROM zips_raw
        ) t
        WHERE rn = 1;
        """)

        self.db_ops.execute_query("""
        CREATE INDEX idx_zips_zip ON Zips(zip);
        """)

        self.db_ops.execute_query("DROP TABLE IF EXISTS zips_raw;")

        count = self.db_ops.execute_query("SELECT COUNT(*) FROM Zips").fetchone()[0]
        log_info(f"Zips table built successfully — {count:,} unique zip codes loaded")

        self.db_ops.execute_query("CREATE INDEX IF NOT EXISTS idx_zips_zip ON Zips(zip);")
        
    def populate_lat_lon_columns(self):
        """Step #1: Add and populate lat/lon columns on Grants and Charities.
        100% safe idempotent check using pragma_table_info (no Binder Error)."""
        log_info("Populating lat/lon columns (safe idempotent check first)...")

        # Safe column existence check (works even if column doesn't exist)
        def column_exists(table: str, col: str) -> bool:
            row = self.db_ops.execute_query(
                "SELECT 1 FROM pragma_table_info(?) WHERE name = ? LIMIT 1",
                [table, col]
            ).fetchone()
            return row is not None

        grants_done = column_exists('Grants', 'lat') and self.db_ops.execute_query(
            "SELECT 1 FROM Grants WHERE lat IS NOT NULL LIMIT 1"
        ).fetchone() is not None

        charities_done = column_exists('Charities', 'lat') and self.db_ops.execute_query(
            "SELECT 1 FROM Charities WHERE lat IS NOT NULL LIMIT 1"
        ).fetchone() is not None

        if grants_done and charities_done:
            log_info("lat/lon columns already populated — skipping")
            return

        # Add columns if missing
        self.db_ops.execute_query("ALTER TABLE Grants ADD COLUMN IF NOT EXISTS lat DOUBLE;")
        self.db_ops.execute_query("ALTER TABLE Grants ADD COLUMN IF NOT EXISTS lon DOUBLE;")
        self.db_ops.execute_query("ALTER TABLE Charities ADD COLUMN IF NOT EXISTS lat DOUBLE;")
        self.db_ops.execute_query("ALTER TABLE Charities ADD COLUMN IF NOT EXISTS lon DOUBLE;")

        # === Grants: PO: cases (zip lookup) ===
        self.db_ops.execute_query("""
        UPDATE Grants g
        SET lat = z.lat, lon = z.lon
        FROM Zips z
        WHERE g.colocator LIKE 'PO:%'
          AND z.zip = split_part(g.colocator, ':', 3)
          AND g.lat IS NULL;
        """)

        # === Grants: LL: cases ===
        self.db_ops.execute_query("""
        UPDATE Grants g
        SET lat = split_part(g.colocator, ':', 2)::DOUBLE,
            lon = split_part(g.colocator, ':', 3)::DOUBLE
        WHERE g.colocator LIKE 'LL:%'
          AND g.lat IS NULL;
        """)
        
        # === Grants: weird: cases ===
        self.db_ops.execute_query("""
        -- Handle PRIV:, MALL:, MAJOR: and similar custom formats
            UPDATE Grants g
            SET 
                lat = z.lat,
                lon = z.lon
            FROM Zips z
            WHERE g.lat IS NULL
            AND g.colocator IS NOT NULL
            AND (
                g.colocator LIKE 'PRIV:%' 
            OR g.colocator LIKE 'MALL:%' 
            OR g.colocator LIKE 'MAJOR:%'
            )
            AND z.zip = split_part(g.colocator, ':', -1);   -- take the LAST part after final ':'
        """)


        # === Charities: PO: cases ===
        self.db_ops.execute_query("""
        UPDATE Charities c
        SET lat = z.lat, lon = z.lon
        FROM Zips z
        WHERE c.colocator LIKE 'PO:%'
          AND z.zip = split_part(c.colocator, ':', 3)
          AND c.lat IS NULL;
        """)

        # === Charities: LL: cases ===
        self.db_ops.execute_query("""
        UPDATE Charities c
        SET lat = split_part(c.colocator, ':', 2)::DOUBLE,
            lon = split_part(c.colocator, ':', 3)::DOUBLE
        WHERE c.colocator LIKE 'LL:%'
          AND c.lat IS NULL;
        """)
        
        ## Loose colocators 
        self.db_ops.execute_query("""
            UPDATE Grants
            SET loose_colocator = 'LL:' || ROUND(lat / 0.5) * 0.5 || ':' || ROUND(lon / 0.5) * 0.5
            WHERE loose_colocator IS NULL AND lat IS NOT NULL;""")

        self.db_ops.execute_query("""
                                  UPDATE Charities
                            SET loose_colocator = 'LL:' || ROUND(lat / 0.5) * 0.5 || ':' || ROUND(lon / 0.5) * 0.5
                            WHERE loose_colocator IS NULL AND lat IS NOT NULL""");

        #-- Index (already working for you)
        self.db_ops.execute_query("CREATE INDEX IF NOT EXISTS idx_charities_loose_colocator ON Charities(loose_colocator);")

        # === Indexes (idempotent) ===
        self.db_ops.execute_query("CREATE INDEX IF NOT EXISTS idx_charities_lat ON Charities(lat);")
        self.db_ops.execute_query("CREATE INDEX IF NOT EXISTS idx_charities_lon ON Charities(lon);")
        self.db_ops.execute_query("CREATE INDEX IF NOT EXISTS idx_grants_lat ON Grants(lat);")
        self.db_ops.execute_query("CREATE INDEX IF NOT EXISTS idx_grants_lon ON Grants(lon);")

        log_info("lat/lon columns populated + indexes created")
    
    def _process_batch(self, batch: List[Dict[str, Any]]) -> PendingDatabaseContext:
        context = PendingDatabaseContext()
        updates = []

        for item in batch:
            name = item['grantee_name']
            loose_colocator = item.get('loose_colocator')
            best_ein = None
            source = None

            # 1. Geo fallback first
            if loose_colocator:
                best_ein = self._geo_fallback_match(item)
                if best_ein:
                    source = 'geo_fallback'

            # 2. Authoritative specific loose_colocator
            if not best_ein and loose_colocator:
                row = self.db_ops.execute_query(
                    "SELECT ein FROM AuthoritativeEin WHERE name = ? AND loose_colocator = ? LIMIT 1",
                    [name, loose_colocator]
                ).fetchone()
                if row:
                    best_ein = row[0]
                    source = 'authoritative-colocator'

            # 3. Hail mary: global name match
            if not best_ein:
                row = self.db_ops.execute_query(
                    "SELECT ein FROM AuthoritativeEin WHERE name = ? AND loose_colocator = 'NULL' LIMIT 1",
                    [name]
                ).fetchone()
                if row:
                    best_ein = row[0]
                    source = 'authoritative-global'

            # 4. Final fallback: GIN
            if not best_ein:
                best_ein = self._generate_gin(name)
                source = 'gin'

            final_ein = best_ein or '8686'
            updates.append({'grant_id': item['grant_id'], 'recipient_ein': final_ein})

            # Scoped propagation
            if final_ein and loose_colocator:
                prop_op = DatabaseOperation(
                    DatabaseOperationType.GENERIC_UPDATE,
                    data={
                        'table': 'Grants',
                        'set_clause': 'recipient_ein = ?',
                        'where_clause': "grantee_name = ? AND loose_colocator = ? AND (recipient_ein IS NULL OR recipient_ein = '8686')",
                        'params': [final_ein, name, loose_colocator]
                    }
                )
                context.addOperationToDatabase(prop_op)

                # Update AuthoritativeEin via proper operation (consumer will handle it)
                if False and source in ('geo_fallback', 'gin'): # intentionally disable for now  there might be other writes.
                    auth_op = DatabaseOperation(
                        DatabaseOperationType.AUTHORITATIVE_EIN_UPDATE,
                        data={
                            'name': name,
                            'loose_colocator': loose_colocator,
                            'ein': best_ein
                        }
                    )
                    context.addOperationToDatabase(auth_op)

        # Bulk update this batch
        if updates:
            bulk_op = DatabaseOperation(
                DatabaseOperationType.GENERIC_UPDATE,
                data={'table': 'Grants', 'updates': updates, 'id_column': 'grant_id'}
            )
            context.addOperationToDatabase(bulk_op)

        # Progress
        progress_op = DatabaseOperation(
            DatabaseOperationType.PROGRESS_UPDATE,
            data={"count": len(batch)}
        )
        context.addOperationToDatabase(progress_op)

        return context
    
    def _generate_gin(self, name: str) -> str:
        """Full SHA256 GIN (70 + 64 hex chars). Zero practical collision risk."""
        cleaned = name.upper().strip()
        cleaned = COMPANY_SUFFIX_REGEX.sub('', cleaned)
        hash_obj = hashlib.sha256(cleaned.encode('utf-8'))
        return "70" + hash_obj.hexdigest()

    def get_work_count(self, max_files: Optional[int] = None) -> int:
        query = "SELECT COUNT(*) FROM Grants WHERE recipient_ein IS NULL and colocator IS NOT NULL"
        result = self.db_ops.execute_query(query)
        total = result.fetchone()[0]
        if max_files:
            total = min(total, max_files)
        return total

    def get_progress_config(self, max_files: Optional[int] = None) -> Tuple[int, str, str]:
        total = self.get_work_count(max_files)
        return total, 'grants', 'Matching grants to charities'

    def _feed_thread(self, work_queue: queue.Queue, max_files: Optional[int], num_producers: int):
        last_id = None
        enqueued = 0
        while True:
            if self.exit_processing:
                break
            batch, new_last = self._get_work_batch(last_id)
            if not batch:
                break
            work_queue.put(WorkUnit.batch(batch))
            enqueued += len(batch)
            last_id = new_last
            if max_files and enqueued >= max_files:
                break
        for i in range(num_producers):
            work_queue.put(WorkUnit.sentinel(i))

    def _get_work_batch(self, last_id: Optional[str]) -> Tuple[List[Dict[str, Any]], Optional[str]]:
        query = """
        SELECT grant_id, grantee_name, colocator, loose_colocator, grantee_sndx
        FROM Grants
        WHERE recipient_ein IS NULL and loose_colocator IS NOT NULL
        """
        params: Tuple = (self.batch_size,)
        if last_id:
            query += " AND grant_id > ?"
            params = (last_id,) + params
        query += " ORDER BY grant_id LIMIT ?"
        rows = self.db_ops.execute_query(query, params).fetchall()
        batch = [dict(zip(['grant_id', 'grantee_name', 'loose_colocator', 'grantee_sndx'], row)) for row in rows]
        new_last = rows[-1][0] if rows else None
        return batch, new_last

    def _get_best_ein(self, charities: List[Tuple[str, str, float, str]], grantee_name: str, grantee_sndx: str) -> str:
        """Get best EIN by jaccard, prefer same sndx, tiebreak wealth"""
        matches = []
        for ein, filer_name, wealth, sndx in charities:
            jacc = word_jaccard(grantee_name, filer_name)
            sndx_match = (sndx == grantee_sndx) * 0.2  # Bonus for sndx match
            score = jacc + sndx_match
            if score >= self.jaccard_threshold:
                matches.append((score, ein, wealth))
        
        if matches:
            matches.sort(key=lambda x: (-x[0], -x[2]))
            return matches[0][1]
        else:
            best = max(charities, key=lambda x: x[2])
            return best[0]

    def _geo_fallback_match(self, item: Dict[str, Any]) -> Optional[str]:
        """Fast exact match on loose_colocator (0.5° grid) — uses the index perfectly."""
        loose = item.get('loose_colocator')
        if not loose:
            return None

        candidates = self.db_ops.execute_query("""
            SELECT c.ein, 
                   c.filer_name, 
                   (COALESCE(c.govt_amt, 0) + COALESCE(c.receipt_amt, 0)) AS wealth, 
                   c.sndx
            FROM Charities c
            WHERE c.loose_colocator = ?
        """, [loose]).fetchall()

        return self._get_best_ein(candidates, item['grantee_name'], item['grantee_sndx']) if candidates else None
    def _global_fallback_match(self, grantee_name: str) -> Optional[str]:
        """Original global name fallback with LIKE and wealth"""
        words = grantee_name.lower().split()[:2]
        like_pattern = '%' + '%'.join(words) + '%'
        query_global = """
        SELECT ein, filer_name, (COALESCE(govt_amt, 0) + COALESCE(receipt_amt, 0)) AS wealth, sndx
        FROM Charities
        WHERE LOWER(filer_name) LIKE ?
        ORDER BY wealth DESC
        LIMIT 50
        """
        global_charities = self.db_ops.execute_query(query_global, [like_pattern]).fetchall()
        
        if not global_charities:
            return None
        
        matches = []
        for ein, filer_name, wealth, sndx in global_charities:
            jacc = word_jaccard(grantee_name, filer_name)
            if jacc >= self.jaccard_threshold:
                matches.append((jacc, ein, wealth))
        
        if matches:
            matches.sort(key=lambda x: (-x[0], -x[2]))
            return matches[0][1]
        else:
            best = max(global_charities, key=lambda x: x[2])
            return best[0]

    def _parse_zip(self, colocator: str) -> Optional[str]:
        if colocator and colocator.startswith('PO:'):
            return colocator.split(':')[2]
        return None # No zip code available