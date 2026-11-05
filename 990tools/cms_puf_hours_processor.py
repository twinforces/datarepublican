#!/usr/bin/env python3
"""PUF service volume × CMS physician minutes/RVU → implied hours.

Does not wait on building footprints. Pin sqft is a later ATTACH.
Hours are a lower bound: cms_puf_service omits HCPCS lines with ≤10 benes.
Untimed codes (labs, many drugs) stay in srvcs_untimed, not as zero hours.
"""

from __future__ import annotations

import argparse
import os
from typing import Dict

import duckdb

from logging_utils import log_info

DEFAULT_DB = "/Volumes/Data/final/irs990.duckdb"
DEFAULT_BUILDINGS_DB = "/Volumes/Insurrection/990data/buildings.duckdb"
PUF_YEAR = 2024

SERVICE_HOURS_SQL = f"""
CREATE OR REPLACE TABLE cms_puf_service_hours AS
SELECT
    {PUF_YEAR} AS puf_year,
    s.Rndrng_NPI AS npi,
    upper(trim(CAST(s.HCPCS_Cd AS VARCHAR))) AS hcpcs,
    s.Place_Of_Srvc AS place,
    s.HCPCS_Drug_Ind AS drug,
    s.Tot_Benes AS tot_benes,
    s.Tot_Srvcs AS tot_srvcs,
    t.adj_total_min,
    t.adj_intra_min,
    CASE
        WHEN t.adj_total_min IS NOT NULL
        THEN s.Tot_Srvcs * t.adj_total_min / 60.0
    END AS hours,
    r.work_rvu,
    CASE
        WHEN r.work_rvu IS NOT NULL
        THEN s.Tot_Srvcs * r.work_rvu
    END AS work_rvu_total
FROM cms_puf_service s
LEFT JOIN cms_pfs_work_time t
  ON t.cpt_code = upper(trim(CAST(s.HCPCS_Cd AS VARCHAR)))
LEFT JOIN cms_pfs_rvu r
  ON r.hcpcs = upper(trim(CAST(s.HCPCS_Cd AS VARCHAR)))
 AND r.mod IS NULL
"""

NPI_HOURS_SQL = """
CREATE OR REPLACE TABLE cms_puf_npi_hours AS
SELECT
    agg.puf_year,
    agg.npi,
    p.Rndrng_Prvdr_Last_Org_Name AS name,
    p.Rndrng_Prvdr_First_Name AS first_name,
    p.Rndrng_Prvdr_Type AS provider_type,
    p.Rndrng_Prvdr_Ent_Cd AS entity,
    p.Rndrng_Prvdr_St1 AS street,
    p.Rndrng_Prvdr_City AS city,
    p.Rndrng_Prvdr_State_Abrvtn AS state,
    p.Rndrng_Prvdr_Zip5 AS zip,
    p.Bene_Avg_Age AS bene_avg_age,
    p.Tot_Benes AS provider_tot_benes,
    p.Tot_Srvcs AS provider_tot_srvcs,
    p.Tot_HCPCS_Cds AS provider_tot_hcpcs,
    agg.published_srvcs,
    agg.hours_timed,
    agg.srvcs_untimed,
    agg.service_rows,
    agg.timed_rows,
    p.Tot_Srvcs - agg.published_srvcs AS suppressed_srvcs,
    prac.colocator,
    (l.NPI IS NOT NULL) AS leie
FROM (
    SELECT
        puf_year,
        npi,
        SUM(tot_srvcs) AS published_srvcs,
        SUM(hours) AS hours_timed,
        SUM(CASE WHEN hours IS NULL THEN tot_srvcs ELSE 0 END) AS srvcs_untimed,
        COUNT(*) AS service_rows,
        COUNT(hours) AS timed_rows
    FROM cms_puf_service_hours
    GROUP BY puf_year, npi
) agg
JOIN cms_puf_provider p ON p.Rndrng_NPI = agg.npi
LEFT JOIN medicare_providers m ON m.npi = CAST(agg.npi AS VARCHAR)
LEFT JOIN (
    SELECT owner_id, any_value(colocator) AS colocator
    FROM Addresses
    WHERE address_type = 'nppes_practice'
    GROUP BY 1
) prac ON prac.owner_id = m.id
LEFT JOIN (
    SELECT DISTINCT NPI FROM cms_leie WHERE NPI IS NOT NULL AND NPI != 0
) l ON l.NPI = agg.npi
"""

SQFT_COLS = (
    "n_buildings",
    "min_area_sqft",
    "max_area_sqft",
    "sum_area_sqft",
    "n_buildings_near",
    "min_area_near",
    "max_area_near",
    "sum_area_near",
    "n_cells_near",
)
# ±3 × 0.0001° (~33 m) so Allcare's 0.0003° miss still hits.
NEAR_STEPS = 3

INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_cms_puf_service_hours_npi ON cms_puf_service_hours(npi)",
    "CREATE INDEX IF NOT EXISTS idx_cms_puf_service_hours_hcpcs ON cms_puf_service_hours(hcpcs)",
    "CREATE INDEX IF NOT EXISTS idx_cms_puf_npi_hours_npi ON cms_puf_npi_hours(npi)",
    "CREATE INDEX IF NOT EXISTS idx_cms_puf_npi_hours_hours ON cms_puf_npi_hours(hours_timed)",
    "CREATE INDEX IF NOT EXISTS idx_cms_puf_npi_hours_colocator ON cms_puf_npi_hours(colocator)",
)


def _say(msg: str) -> None:
    print(msg, flush=True)
    log_info(msg)


def _hours_select_sql(conn: duckdb.DuckDBPyConnection) -> str:
    cols = [r[0] for r in conn.execute("DESCRIBE cms_puf_npi_hours").fetchall()]
    drop = [c for c in SQFT_COLS if c in cols]
    if drop:
        return "n.* EXCLUDE (" + ", ".join(drop) + ")"
    return "n.*"


def attach_building_sqft(conn: duckdb.DuckDBPyConnection, buildings_db: str) -> Dict[str, int]:
    """Exact LL: cell plus ±3 cells (~33 m) so a pin one house off still gets sqft."""
    _say(f"ATTACH buildings {buildings_db} near_steps={NEAR_STEPS}")
    conn.execute("DETACH DATABASE IF EXISTS bld")
    conn.execute(f"ATTACH '{buildings_db}' AS bld (READ_ONLY)")
    core = _hours_select_sql(conn)
    conn.execute(
        f"""
        CREATE OR REPLACE TABLE cms_puf_npi_hours_sqft AS
        WITH npi_ll AS (
            SELECT
                npi,
                colocator,
                TRY_CAST(split_part(colocator, ':', 2) AS DOUBLE) AS lat,
                TRY_CAST(split_part(colocator, ':', 3) AS DOUBLE) AS lon
            FROM cms_puf_npi_hours
            WHERE colocator LIKE 'LL:%'
        ),
        steps AS (
            SELECT unnest(generate_series(-{NEAR_STEPS}, {NEAR_STEPS})) * 0.0001 AS delta
        ),
        npi_cells AS (
            SELECT
                n.npi,
                printf('LL:%.4f:%.4f', ROUND(n.lat + dlat.delta, 4), ROUND(n.lon + dlon.delta, 4)) AS cell,
                (dlat.delta = 0 AND dlon.delta = 0) AS is_exact
            FROM npi_ll n
            CROSS JOIN steps dlat
            CROSS JOIN steps dlon
            WHERE n.lat IS NOT NULL AND n.lon IS NOT NULL
        ),
        near AS (
            SELECT
                c.npi,
                SUM(b.n_buildings) AS n_buildings_near,
                MIN(b.min_area_sqft) AS min_area_near,
                MAX(b.max_area_sqft) AS max_area_near,
                SUM(b.sum_area_sqft) AS sum_area_near,
                COUNT(*) AS n_cells_near
            FROM npi_cells c
            JOIN bld.building_colocator b ON b.colocator = c.cell
            GROUP BY 1
        )
        SELECT
            {core},
            e.n_buildings,
            e.min_area_sqft,
            e.max_area_sqft,
            e.sum_area_sqft,
            nb.n_buildings_near,
            nb.min_area_near,
            nb.max_area_near,
            nb.sum_area_near,
            nb.n_cells_near
        FROM cms_puf_npi_hours n
        LEFT JOIN bld.building_colocator e ON e.colocator = n.colocator
        LEFT JOIN near nb ON nb.npi = n.npi
        """
    )
    conn.execute("DROP TABLE cms_puf_npi_hours")
    conn.execute("ALTER TABLE cms_puf_npi_hours_sqft RENAME TO cms_puf_npi_hours")
    conn.execute("DETACH bld")
    for sql in INDEXES:
        try:
            conn.execute(sql)
        except duckdb.Error:
            pass
    n = int(conn.execute("SELECT COUNT(*) FROM cms_puf_npi_hours").fetchone()[0])
    exact = int(
        conn.execute(
            "SELECT COUNT(*) FROM cms_puf_npi_hours WHERE n_buildings IS NOT NULL"
        ).fetchone()[0]
    )
    near_n = int(
        conn.execute(
            "SELECT COUNT(*) FROM cms_puf_npi_hours WHERE n_buildings_near IS NOT NULL"
        ).fetchone()[0]
    )
    _say(
        f"cms_puf_npi_hours sqft: {exact:,} exact / {near_n:,} within ±{NEAR_STEPS} cells / {n:,} NPIs"
    )
    return {
        "cms_puf_npi_hours": n,
        "cms_puf_npi_hours_sqft": exact,
        "cms_puf_npi_hours_sqft_near": near_n,
    }


def build_hours(conn: duckdb.DuckDBPyConnection) -> Dict[str, int]:
    _say("cms_puf_service_hours: PUF × work time × RVU")
    conn.execute(SERVICE_HOURS_SQL)
    n_svc = int(conn.execute("SELECT COUNT(*) FROM cms_puf_service_hours").fetchone()[0])
    timed = int(
        conn.execute("SELECT COUNT(*) FROM cms_puf_service_hours WHERE hours IS NOT NULL").fetchone()[0]
    )
    _say(f"cms_puf_service_hours: {n_svc:,} rows ({timed:,} timed)")
    _say("cms_puf_npi_hours: rollup + NPPES colocator + LEIE")
    conn.execute(NPI_HOURS_SQL)
    n_npi = int(conn.execute("SELECT COUNT(*) FROM cms_puf_npi_hours").fetchone()[0])
    with_col = int(
        conn.execute(
            "SELECT COUNT(*) FROM cms_puf_npi_hours WHERE colocator IS NOT NULL"
        ).fetchone()[0]
    )
    _say(f"cms_puf_npi_hours: {n_npi:,} NPIs ({with_col:,} with colocator)")
    for sql in INDEXES:
        conn.execute(sql)
    return {
        "cms_puf_service_hours": n_svc,
        "cms_puf_service_hours_timed": timed,
        "cms_puf_npi_hours": n_npi,
        "cms_puf_npi_hours_colocator": with_col,
    }


class CmsPufHoursProcessor:
    def __init__(self, db_path: str, buildings_db: str | None = None):
        self.db_path = db_path
        self.buildings_db = buildings_db or os.environ.get(
            "BUILDING_DB", DEFAULT_BUILDINGS_DB
        )

    def run(self, *, sqft_only: bool = False) -> Dict[str, int]:
        mem = os.environ.get("DUCKDB_MEMORY_LIMIT", "12GB")
        _say(f"PUF hours db={self.db_path} mem={mem} sqft_only={sqft_only}")
        conn = duckdb.connect(
            self.db_path,
            config={
                "memory_limit": mem,
                "threads": "1",
                "preserve_insertion_order": "false",
            },
        )
        try:
            stats: Dict[str, int] = {}
            if not sqft_only:
                stats.update(build_hours(conn))
            if self.buildings_db and os.path.exists(self.buildings_db):
                stats.update(attach_building_sqft(conn, self.buildings_db))
            else:
                _say(f"skip sqft: buildings db missing ({self.buildings_db})")
            conn.execute("CHECKPOINT")
        finally:
            conn.close()
        _say(f"=== PUF hours complete: {stats} ===")
        return stats


def main() -> int:
    p = argparse.ArgumentParser(description="CMS PUF implied physician hours")
    p.add_argument("--db", default=os.environ.get("DB", DEFAULT_DB))
    p.add_argument("--buildings-db", default=None)
    p.add_argument(
        "--sqft-only",
        action="store_true",
        help="Skip PUF×time rebuild; ATTACH building_colocator onto cms_puf_npi_hours",
    )
    args = p.parse_args()
    CmsPufHoursProcessor(args.db, args.buildings_db).run(sqft_only=args.sqft_only)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
