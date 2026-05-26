#!/usr/bin/env python3
"""
One Stop Shopping Script
Exports from DuckDB → clean Parquet (with proper UUID handling) → uploads to HF
"""

import os
import shutil
import duckdb
from huggingface_hub import HfApi, create_repo, upload_folder
from pathlib import Path

# ====================== CONFIGURATION ======================
HF_TOKEN = os.getenv("HF_TOKEN")
REPO_ID = "piercewetter3/irs-990-parsed"
DUCKDB_PATH = "/Volumes/Data/final/irs990.duckdb.address"   # <-- UPDATE THIS
SCHEMA_PATH = "/Users/pierce/Development/datarepublican/990tools/schema_duckdb.sql"
OUTPUT_DIR = "./parquet_export_990"
# ===========================================================

def get_clean_select(con, table: str) -> str:
    """Build a clean SELECT that casts UUIDs to VARCHAR without duplicates"""
    # Get column info
    columns = con.execute(f"DESCRIBE {table}").fetchall()
    
    select_parts = []
    for col_name, col_type, *_ in columns:
        if col_type.upper() == "UUID":
            select_parts.append(f"CAST({col_name} AS VARCHAR) AS {col_name}")
        else:
            select_parts.append(col_name)
    
    return f"SELECT {', '.join(select_parts)} FROM {table}"


def main():
    if not HF_TOKEN:
        raise ValueError("Please set HF_TOKEN environment variable")

    print("Connecting to DuckDB...")
    con = duckdb.connect(DUCKDB_PATH, read_only=True)

    # Tables to export (add more if needed)
    tables = [
        "Charities", "Grants", "Officers", "Addresses", "Contributions",
        "Contractors", "IrsBmf", "Backfill",
        "Geocoding", "XmlFiles", "ZipFiles", "Zips",
        "fec_committees", "fec_candidate_spendings", "fec_committee_transactions",
        "fec_individual_contributions", "fec_operating_expenditures"
    ]

    Path(OUTPUT_DIR).mkdir(parents=True, exist_ok=True)

    print("Exporting tables with clean UUID handling...")
    for table in tables:
        print(f"  → {table}")
        select_sql = get_clean_select(con, table)
        output_file = f"{OUTPUT_DIR}/{table}.parquet"
        
        con.execute(f"""
            COPY ({select_sql}) 
            TO '{output_file}' 
            (FORMAT PARQUET, COMPRESSION ZSTD)
        """)

    # Copy schema file
    shutil.copy(SCHEMA_PATH, f"{OUTPUT_DIR}/schema_duckdb.sql")
    print("Schema file copied.")

    # Upload to Hugging Face
    print(f"\nUploading to {REPO_ID} ...")
    api = HfApi(token=HF_TOKEN)
    create_repo(REPO_ID, repo_type="dataset", exist_ok=True, token=HF_TOKEN)

    upload_folder(
        folder_path=OUTPUT_DIR,
        repo_id=REPO_ID,
        repo_type="dataset",
        token=HF_TOKEN,
        commit_message="Clean upload with proper UUID → VARCHAR casting (no duplicates)"
    )

    print("\n✅ Upload complete!")
    print(f"View your dataset: https://huggingface.co/datasets/{REPO_ID}")
    print("The Dataset Viewer should now work correctly.")


if __name__ == "__main__":
    main()