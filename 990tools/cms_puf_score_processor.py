#!/usr/bin/env python3
"""Turn cms_puf_npi_hours into a watch list, not a fraud determination.

Raw hours_timed counts facility rounding, 15-minute therapy, and cataract
units as "a physician year." This table splits office vs facility, compares
office hours to the specialty 90th percentile, and treats a stack of high-hour
NPIs in a small building as a mill prior.
"""

from __future__ import annotations

import argparse
import os
from typing import Dict

import duckdb

from logging_utils import log_info

DEFAULT_DB = "/Volumes/Data/final/irs990.duckdb"

SCORE_SQL = """
CREATE OR REPLACE TABLE cms_puf_npi_score AS
WITH place AS (
    SELECT
        npi,
        SUM(CASE WHEN place = 'O' THEN hours ELSE 0 END) AS hours_office,
        SUM(CASE WHEN place = 'F' THEN hours ELSE 0 END) AS hours_facility,
        SUM(hours) AS hours_timed_svc,
        SUM(CASE WHEN hcpcs BETWEEN '99202' AND '99499' THEN hours ELSE 0 END) AS hours_em,
        SUM(CASE WHEN place = 'O' AND hcpcs BETWEEN '99202' AND '99499' THEN hours ELSE 0 END) AS hours_em_office,
        SUM(CASE WHEN hcpcs LIKE '97%' THEN hours ELSE 0 END) AS hours_therapy,
        SUM(work_rvu_total) AS work_rvu_total
    FROM cms_puf_service_hours
    WHERE hours IS NOT NULL
    GROUP BY 1
),
spec AS (
    SELECT
        n.provider_type,
        quantile_cont(p.hours_office, 0.9) AS p90_office
    FROM place p
    JOIN cms_puf_npi_hours n ON n.npi = p.npi
    WHERE n.entity = 'I' AND p.hours_office > 0 AND n.provider_type IS NOT NULL
    GROUP BY 1
),
stack AS (
    SELECT
        n.colocator,
        COUNT(*) AS npi_high_at_cell,
        COUNT(DISTINCT n.city) AS cell_cities,
        COUNT(DISTINCT n.state) AS cell_states
    FROM cms_puf_npi_hours n
    JOIN place p ON p.npi = n.npi
    WHERE n.entity = 'I'
      AND p.hours_office > 2080
      AND n.colocator LIKE 'LL:%'
    GROUP BY 1
)
SELECT
    n.npi,
    n.name,
    n.first_name,
    n.provider_type,
    n.entity,
    n.city,
    n.state,
    n.zip,
    n.street,
    n.colocator,
    n.bene_avg_age,
    n.leie,
    n.max_area_near,
    n.n_buildings_near,
    n.n_cells_near,
    n.hours_timed,
    p.hours_office,
    p.hours_facility,
    CASE WHEN p.hours_timed_svc > 0 THEN p.hours_office / p.hours_timed_svc END AS office_share,
    p.hours_em,
    p.hours_em_office,
    p.hours_therapy,
    p.work_rvu_total,
    s.p90_office,
    CASE
        WHEN s.p90_office > 0 THEN p.hours_office / s.p90_office
    END AS office_vs_p90,
    coalesce(st.npi_high_at_cell, 0) AS npi_high_at_cell,
    coalesce(st.cell_cities, 0) AS cell_cities,
    coalesce(st.cell_states, 0) AS cell_states,
    (coalesce(st.cell_states, 1) > 1 OR coalesce(st.cell_cities, 1) >= 3) AS pin_collision,
    (p.hours_timed_svc > 0 AND p.hours_office / p.hours_timed_svc >= 0.7) AS office_heavy,
    (p.hours_office > 2000 AND s.p90_office IS NOT NULL AND p.hours_office >= 2 * s.p90_office) AS extreme_office,
    (n.max_area_near IS NOT NULL AND n.max_area_near < 4000) AS small_site,
    (coalesce(st.npi_high_at_cell, 0) >= 5) AS stacked,
    (
        n.provider_type ILIKE '%physical therapist%'
        OR n.provider_type ILIKE '%occupational therapist%'
        OR n.provider_type ILIKE '%speech%'
        OR n.provider_type ILIKE '%chiropr%'
    ) AS therapy_type,
    (n.provider_type ILIKE '%optometr%') AS optometry_type,
    (
        n.provider_type ILIKE '%laborator%'
        OR n.provider_type ILIKE '%patholog%'
    ) AS lab_type,
    (
        n.entity = 'I'
        AND (p.hours_timed_svc > 0 AND p.hours_office / p.hours_timed_svc >= 0.7)
        AND p.hours_office > 2000
        AND s.p90_office IS NOT NULL
        AND p.hours_office >= 2 * s.p90_office
        AND n.max_area_near IS NOT NULL
        AND n.max_area_near < 4000
        AND NOT (
            n.provider_type ILIKE '%physical therapist%'
            OR n.provider_type ILIKE '%occupational therapist%'
            OR n.provider_type ILIKE '%speech%'
            OR n.provider_type ILIKE '%chiropr%'
            OR n.provider_type ILIKE '%optometr%'
            OR n.provider_type ILIKE '%laborator%'
            OR n.provider_type ILIKE '%patholog%'
        )
    ) AS watch_solo,
    (
        n.entity = 'I'
        AND coalesce(st.npi_high_at_cell, 0) >= 5
        AND n.max_area_near IS NOT NULL
        AND n.max_area_near < 3000
        AND coalesce(st.cell_states, 1) = 1
        AND coalesce(st.cell_cities, 1) < 3
        AND NOT (
            n.provider_type ILIKE '%laborator%'
            OR n.provider_type ILIKE '%patholog%'
        )
        AND p.hours_office > 2080
    ) AS watch_mill,
    FALSE AS watch,
    CAST(0 AS DOUBLE) AS priority
FROM cms_puf_npi_hours n
JOIN place p ON p.npi = n.npi
LEFT JOIN spec s ON s.provider_type = n.provider_type
LEFT JOIN stack st ON st.colocator = n.colocator
"""

WATCH_SQL = """
UPDATE cms_puf_npi_score
SET watch = (watch_solo OR watch_mill)
"""

PRIORITY_SQL = """
UPDATE cms_puf_npi_score
SET priority = CASE
    WHEN NOT (watch_solo OR watch_mill) THEN 0
    ELSE
        coalesce(office_vs_p90, 0) * CASE WHEN watch_solo THEN 1.0 ELSE 0.25 END
        + CASE WHEN watch_mill THEN npi_high_at_cell / 5.0 ELSE 0 END
        + CASE
            WHEN max_area_near IS NOT NULL AND max_area_near > 0 AND max_area_near < 4000
            THEN 4000.0 / max_area_near
            ELSE 0
          END
        + CASE WHEN leie THEN 10 ELSE 0 END
END
"""

CELLS_SQL = """
CREATE OR REPLACE TABLE cms_puf_watch_cells AS
SELECT
    colocator,
    street,
    city,
    state,
    zip,
    COUNT(*) AS n_watch,
    COUNT(*) FILTER (WHERE watch_solo) AS n_solo,
    COUNT(*) FILTER (WHERE watch_mill) AS n_mill,
    MAX(npi_high_at_cell) AS npi_high_at_cell,
    MIN(max_area_near) AS max_area_near,
    SUM(hours_office) AS hours_office,
    MAX(priority) AS max_priority,
    BOOL_OR(leie) AS any_leie
FROM cms_puf_npi_score
WHERE watch
GROUP BY 1, 2, 3, 4, 5
"""

INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_cms_puf_npi_score_npi ON cms_puf_npi_score(npi)",
    "CREATE INDEX IF NOT EXISTS idx_cms_puf_npi_score_watch ON cms_puf_npi_score(watch)",
    "CREATE INDEX IF NOT EXISTS idx_cms_puf_npi_score_cell ON cms_puf_npi_score(colocator)",
    "CREATE INDEX IF NOT EXISTS idx_cms_puf_npi_score_priority ON cms_puf_npi_score(priority)",
    "CREATE INDEX IF NOT EXISTS idx_cms_puf_watch_cells_priority ON cms_puf_watch_cells(max_priority)",
)


def _say(msg: str) -> None:
    print(msg, flush=True)
    log_info(msg)


def build_score(conn: duckdb.DuckDBPyConnection) -> Dict[str, int]:
    _say("cms_puf_npi_score: office/facility split, specialty p90, mill stack")
    conn.execute(SCORE_SQL)
    conn.execute(WATCH_SQL)
    conn.execute(PRIORITY_SQL)
    conn.execute(CELLS_SQL)
    for sql in INDEXES:
        conn.execute(sql)
    n = int(conn.execute("SELECT COUNT(*) FROM cms_puf_npi_score").fetchone()[0])
    solo = int(conn.execute("SELECT COUNT(*) FROM cms_puf_npi_score WHERE watch_solo").fetchone()[0])
    mill = int(conn.execute("SELECT COUNT(*) FROM cms_puf_npi_score WHERE watch_mill").fetchone()[0])
    watch = int(conn.execute("SELECT COUNT(*) FROM cms_puf_npi_score WHERE watch").fetchone()[0])
    mills = int(
        conn.execute(
            "SELECT COUNT(DISTINCT colocator) FROM cms_puf_npi_score WHERE watch_mill"
        ).fetchone()[0]
    )
    cells = int(conn.execute("SELECT COUNT(*) FROM cms_puf_watch_cells").fetchone()[0])
    _say(
        f"cms_puf_npi_score: {n:,} NPIs, watch={watch:,} "
        f"(solo={solo:,}, mill={mill:,} NPIs at {mills:,} cells); "
        f"cms_puf_watch_cells={cells:,}"
    )
    return {
        "cms_puf_npi_score": n,
        "watch": watch,
        "watch_solo": solo,
        "watch_mill": mill,
        "watch_mill_cells": mills,
        "cms_puf_watch_cells": cells,
    }


class CmsPufScoreProcessor:
    def __init__(self, db_path: str):
        self.db_path = db_path

    def run(self) -> Dict[str, int]:
        mem = os.environ.get("DUCKDB_MEMORY_LIMIT", "12GB")
        _say(f"PUF score db={self.db_path} mem={mem}")
        conn = duckdb.connect(
            self.db_path,
            config={
                "memory_limit": mem,
                "threads": "1",
                "preserve_insertion_order": "false",
            },
        )
        try:
            stats = build_score(conn)
            conn.execute("CHECKPOINT")
        finally:
            conn.close()
        _say(f"=== PUF score complete: {stats} ===")
        return stats


def main() -> int:
    p = argparse.ArgumentParser(description="CMS PUF watch list (not a fraud determination)")
    p.add_argument("--db", default=os.environ.get("DB", DEFAULT_DB))
    args = p.parse_args()
    CmsPufScoreProcessor(args.db).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
