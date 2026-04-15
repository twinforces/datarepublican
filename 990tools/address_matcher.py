#!/usr/bin/env python3
"""
address_matcher.py - Full grant normalization + recipient_ein backfill

1. Geo normalization (grantee_name_geo)
2. Full 6-step name + EIN consolidation using grantee_name_geo
"""

import logging
import json
from pathlib import Path
from typing import Dict

from database_operations import DatabaseOperations
from logging_utils import log_info, log_error


class AddressMatcher:
    """Handles geo normalization + full name-based EIN backfill."""

    def __init__(self, db_ops: DatabaseOperations, rules_file: str = "name_rules.json"):
        self.db_ops = db_ops
        self.logger = logging.getLogger("address_matcher")
        self.rules_file = Path(rules_file)
        self.rules = self._load_rules()

    def _load_rules(self) -> Dict[str, list]:
        if self.rules_file.exists():
            with open(self.rules_file) as f:
                rules = json.load(f)
            log_info(f"Loaded {len(rules):,} name rules from {self.rules_file}")
            return rules
        log_error(f"Rules file {self.rules_file} not found!")
        return {}
    
    def dump_stats(self,conn, round:str):
        result=conn.execute("""SELECT 
    COUNT(*) AS total_grants,
    COUNT(CASE WHEN recipient_ein IS NOT NULL OR recipient_ein_backfilled IS NOT NULL THEN 1 END) AS grants_with_ein,
    ROUND(100.0 * COUNT(CASE WHEN recipient_ein IS NOT NULL OR recipient_ein_backfilled IS NOT NULL THEN 1 END) / COUNT(*), 2) AS pct_with_ein,
    COUNT(DISTINCT COALESCE(grantee_name_geo, grantee_name)) AS distinct_canonical_names
    FROM Grants;
    """).fetchone()
        print(f"[{round}] Total grants: {result[0]:,}")
        print(f"[{round}] Grants with EIN: {result[1]:,}")
        print(f"[{round}] Percentage with EIN: {result[2]}%")
        print(f"[{round}] Distinct canonical names: {result[3]:,}")

    def match_grants(self, dry_run: bool = False, batch_size: int = 50000) -> Dict[str, int]:
        """Full pipeline: geo normalization → full name-based EIN consolidation."""
        log_info("Starting full grant normalization + EIN backfill...")

        stats = {
            "geo_normalized": 0,
            "name_normalized_updated": 0,
            "total_updated": 0
        }

        with self.db_ops.acquire_write_conn() as conn:
            conn.execute("BEGIN TRANSACTION")

            try:
                
                # ====================== 1. GEO NORMALIZATION ======================
                log_info("Step 1: Geo-aware name normalization...")
                conn.execute("ALTER TABLE Grants ADD COLUMN IF NOT EXISTS grantee_name_geo VARCHAR;")
                conn.execute("ALTER TABLE Grants ADD COLUMN IF NOT EXISTS recipient_ein_backfilled VARCHAR;")
                self.dump_stats(conn, "Before Geo Normalization")
                for core, name_list in self.rules.items():
                    if not name_list:
                        continue
                    variant_list = "', '".join(n.replace("'", "''") for n in name_list)
                    conn.execute(f"""
                        UPDATE Grants 
                        SET grantee_name_geo = '{core.replace("'", "''")}' 
                        WHERE grantee_name_geo IS NULL 
                          AND grantee_name IN ('{variant_list}')
                    """) # note rules are all already uppercased
                self.dump_stats(conn, "After Geo Normalization")

                conn.execute("""
                    UPDATE Grants 
                    SET grantee_name_geo = upper(grantee_name) 
                    WHERE grantee_name_geo IS NULL
                """)
                self.dump_stats(conn, "After Geo Normalization+ case")


                stats["geo_normalized"] = conn.execute("SELECT COUNT(*) FROM Grants WHERE grantee_name_geo IS NOT NULL").fetchone()[0]
                log_info(f"Geo normalization complete: {stats['geo_normalized']:,} rows")

                self.db_ops._intermediate_commit_and_checkpoint(conn, stats["geo_normalized"])
                
                # if name and address matches, backfill EIN from known matches
                conn.execute("DROP TABLE IF EXISTS grant_update_map;")
                conn.execute("CREATE TEMP TABLE grant_update_map (grant_id UUID PRIMARY KEY, recipient_ein VARCHAR);")
                conn.execute("""
                    INSERT INTO grant_update_map (grant_id, recipient_ein)
                    SELECT 
                        g.grant_id,
                        ANY_VALUE(known.recipient_ein)
                    FROM Grants g
                    JOIN Addresses a ON a.owner_id = g.grant_id AND a.address_type = 'grant'
                    JOIN (
                        SELECT grantee_name_geo, canonical_address, recipient_ein
                        FROM Grants g2
                        JOIN Addresses a2 ON a2.owner_id = g2.grant_id AND a2.address_type = 'grant'
                        WHERE g2.recipient_ein IS NOT NULL AND g2.recipient_ein != ''
                    ) known
                    ON known.grantee_name_geo = g.grantee_name_geo 
                       AND known.canonical_address = a.canonical_address
                    WHERE (g.recipient_ein IS NULL OR g.recipient_ein = '')
                    GROUP BY g.grant_id;
                """)
                map_count1 = conn.execute("SELECT COUNT(*) FROM grant_update_map").fetchone()[0] or 0
                
                updates = conn.execute("SELECT grant_id, recipient_ein FROM grant_update_map").fetchall()
                update_list = [{'grant_id': row[0], 'recipient_ein_backfilled': row[1]} for row in updates]
                stats['address_match_updated'] = self.db_ops.bulk_update('Grants', update_list, id_column='grant_id', batch_size=batch_size, commit=False)
                log_info(f"Pass 1 (name+address) {'would update' if dry_run else 'updated'}: {stats['address_match_updated']:,} grants")
                self.dump_stats(conn, "After Address Match Backfill")


                # ====================== 2. FULL NAME + EIN CONSOLIDATION (your 6-step process) ======================
                log_info("Step 2: Full name-based EIN consolidation using grantee_name_geo...")

                # Use your proven 6-step process, but on grantee_name_geo instead of raw grantee_name
                # (I'll use your exact steps, adapted)

                # Step 2.1: Build base_name_ein using grantee_name_geo
                conn.execute("""
                    CREATE OR REPLACE TABLE base_name_ein AS
                    SELECT
                        grantee_name_geo AS original_name,
                        COALESCE(recipient_ein_backfilled, recipient_ein) AS recipient_ein,
                        COUNT(*) AS grant_count,
                        SUM(grant_amt) AS total_amount,
                        CAST(NULL AS VARCHAR) AS shortest_name,
                        CAST(NULL AS VARCHAR) AS grantee_name_conc
                    FROM Grants
                    WHERE grantee_name_geo IS NOT NULL
                    GROUP BY grantee_name_geo, COALESCE(recipient_ein_backfilled, recipient_ein);
                """)

                # Step 2.2: Collapse by EIN to get shortest name per EIN
                conn.execute("DROP TABLE IF EXISTS temp_per_ein;")
                conn.execute("""
                    CREATE TEMPORARY TABLE IF NOT EXISTS temp_per_ein AS
                        WITH base AS (
                            SELECT
                                COALESCE(recipient_ein_backfilled, recipient_ein) AS recipient_ein,
                                grantee_name_geo,
                                grant_amt
                            FROM Grants
                            WHERE COALESCE(recipient_ein_backfilled, recipient_ein) IS NOT NULL
                            AND COALESCE(recipient_ein_backfilled, recipient_ein) != ''
                        ),
                        ranked AS (
                            SELECT
                                recipient_ein,
                                grantee_name_geo AS grantee_name,
                                ROW_NUMBER() OVER (
                                    PARTITION BY recipient_ein
                                    ORDER BY array_length(regexp_split_to_array(grantee_name_geo, '\\s+'), 1) ASC,
                                            SUM(grant_amt) DESC
                                ) AS rn
                            FROM base
                            GROUP BY recipient_ein, grantee_name_geo
                        )
                        SELECT
                            recipient_ein,
                            grantee_name AS shortest_name
                        FROM ranked
                        WHERE rn = 1;
                """)

                # Step 2.3: Push shortest_name back
                conn.execute("""
                    UPDATE base_name_ein b
                    SET shortest_name = t.shortest_name
                    FROM temp_per_ein t
                    WHERE b.recipient_ein = t.recipient_ein;
                """)

                # Step 2.4: Apply prefix/suffix cleanup on shortest_name
                conn.execute("""
                    UPDATE base_name_ein
                    SET grantee_name_conc = CASE
                        WHEN shortest_name LIKE 'THE %' THEN regexp_replace(shortest_name, '^THE ', '', 'i')
                        WHEN shortest_name LIKE '% INC' THEN regexp_replace(shortest_name, ' INC$', '', 'i')
                        WHEN shortest_name LIKE '% FOUNDATION' THEN regexp_replace(shortest_name, ' FOUNDATION$', '', 'i')
                        WHEN shortest_name LIKE '% FOUNDATION INC' THEN regexp_replace(shortest_name, ' FOUNDATION INC$', '', 'i')
                        WHEN shortest_name LIKE '% LLC' THEN regexp_replace(shortest_name, ' LLC$', '', 'i')
                        WHEN shortest_name ~ ' (CORPORATION|CORP)$' THEN regexp_replace(shortest_name, ' (CORPORATION|CORP)$', '', 'i')
                        WHEN shortest_name ~ ' ASSOCIATION( INC)?$' THEN regexp_replace(shortest_name, ' ASSOCIATION( INC)?$', '', 'i')
                        WHEN shortest_name ~ ' (SCHOOL|SCHOOL DISTRICT)$' THEN regexp_replace(shortest_name, ' (SCHOOL|SCHOOL DISTRICT)$', '', 'i')
                        WHEN shortest_name ~ ' (CHURCH|METHODIST CHURCH|BAPTIST CHURCH)$' THEN regexp_replace(shortest_name, ' (CHURCH|METHODIST CHURCH|BAPTIST CHURCH)$', '', 'i')
                        WHEN shortest_name ~ ' CENTER( INC)?$' THEN regexp_replace(shortest_name, ' CENTER( INC)?$', '', 'i')
                        ELSE shortest_name
                    END;
                """)

                # Step 2.5: Final mapping table
                conn.execute("DROP TABLE IF EXISTS name_mapping;")
                conn.execute("""
                    CREATE TEMP TABLE name_mapping AS
                    SELECT
                        original_name,
                        grantee_name_conc,
                        ANY_VALUE(recipient_ein ORDER BY total_amount DESC) AS winner_ein
                    FROM base_name_ein
                    GROUP BY original_name, grantee_name_conc;
                """)

                # Step 2.6: Backfill to Grants
                conn.execute("""
                    ALTER TABLE Grants ADD COLUMN IF NOT EXISTS recipient_ein_backfilled VARCHAR;
                    ALTER TABLE Grants ADD COLUMN IF NOT EXISTS grantee_name_conc VARCHAR;
                """)


                conn.execute("""
                    UPDATE Grants g
                    SET
                        recipient_ein_backfilled = m.winner_ein,
                        grantee_name_conc = m.grantee_name_conc
                    FROM name_mapping m
                    WHERE g.grantee_name_geo = m.original_name;
                """)

                stats["name_normalized_updated"] = conn.execute("SELECT COUNT(*) FROM Grants WHERE recipient_ein_backfilled IS NOT NULL").fetchone()[0] or 0

                self.db_ops._intermediate_commit_and_checkpoint(conn, stats["geo_normalized"])
                self.dump_stats(conn, "After Name Backfill")


                stats["total_updated"] = stats["name_normalized_updated"]
                log_info(f"Full name-based EIN consolidation complete. Updated {stats['total_updated']:,} grants")

                return stats

            except Exception as e:
                conn.rollback()
                log_error(f"Full grant matching failed: {e}", exc_info=True)
                raise