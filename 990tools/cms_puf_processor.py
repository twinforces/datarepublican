#!/usr/bin/env python3
"""CMS Physician PUF 2024 + LEIE into DuckDB. No NPPES. No Addresses.

By Provider has Bene_Avg_Age (NPI-year). By Provider and Service is NPI×HCPCS
volume with no age. LEIE is the OIG exclusion list.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Dict

import duckdb

from logging_utils import log_info

DEFAULT_DATA_DIR = Path("/Volumes/Insurrection/990data/cms_data/medicare_puf")

FILES = (
    ("cms_puf_provider", "MUP_PHY_R26_P05_V10_D24_Prov.csv"),
    ("cms_puf_service", "PHY_R26_P05_V10_D24_Prov_Svc.csv"),
    ("cms_puf_geo", "MUP_PHY_R26_P05_V10_D24_Geo.csv"),
    ("cms_leie", "LEIE_UPDATED.csv"),
)

INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_cms_puf_provider_npi ON cms_puf_provider(Rndrng_NPI)",
    "CREATE INDEX IF NOT EXISTS idx_cms_puf_service_npi ON cms_puf_service(Rndrng_NPI)",
    "CREATE INDEX IF NOT EXISTS idx_cms_puf_service_hcpcs ON cms_puf_service(HCPCS_Cd)",
    "CREATE INDEX IF NOT EXISTS idx_cms_leie_npi ON cms_leie(NPI)",
)


def default_data_dir() -> Path:
    return Path(os.environ.get("CMS_PUF_DATA_DIR", str(DEFAULT_DATA_DIR)))


class CmsPufProcessor:
    def __init__(self, db_path: str, data_dir: Path | None = None):
        self.db_path = db_path
        self.data_dir = Path(data_dir) if data_dir else default_data_dir()

    def run(self) -> Dict[str, int]:
        stats: Dict[str, int] = {}
        mem = os.environ.get("DUCKDB_MEMORY_LIMIT", "12GB")
        log_info(f"CMS PUF ingest from {self.data_dir} db={self.db_path} mem={mem}")
        conn = duckdb.connect(
            self.db_path,
            config={
                "memory_limit": mem,
                "threads": "1",
                "preserve_insertion_order": "false",
            },
        )
        try:
            for table, name in FILES:
                path = self.data_dir / name
                if not path.exists() or path.stat().st_size == 0:
                    raise RuntimeError(f"missing PUF file {path}")
                log_info(f"CMS PUF COPY {name} → {table} ({path.stat().st_size:,} bytes)")
                conn.execute(
                    f"""
                    CREATE OR REPLACE TABLE {table} AS
                    SELECT * FROM read_csv(
                        ?,
                        header=true,
                        auto_detect=true,
                        ignore_errors=true,
                        sample_size=50000
                    )
                    """,
                    [str(path)],
                )
                n = int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
                stats[table] = n
                log_info(f"CMS PUF {table}: {n:,} rows")
            for sql in INDEXES:
                conn.execute(sql)
            conn.execute("CHECKPOINT")
        finally:
            conn.close()
        log_info(f"=== CMS PUF complete: {stats} ===")
        return stats


def main() -> int:
    db_path = os.environ.get("DB", "/Volumes/Data/final/irs990.duckdb")
    CmsPufProcessor(db_path).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
