# fec_processor.py

**What:** FEC **bulk** contribution and committee files into DuckDB (`fec_committees`, `fec_individual_contributions`, `fec_committee_transactions`, `fec_candidate_spendings`, `fec_operating_expenditures`) plus `Addresses` (`fec_*` types). The DuckDB table is `fec_candidate_spendings` (plural); `Addresses.address_type` for those rows is `fec_candidate_spending`.

**Why:** Campaign-finance streets sit next to 990 / SAM / PPP pins. Bulk zips from fec.gov, not the OpenFEC REST API. The api.data.gov key is unused here.

**Source:** [FEC bulk downloads](https://www.fec.gov/data/browse-data/?tab=bulk-data) (`https://www.fec.gov/files/bulk-downloads/{cycle}/{prefix}{yy}.zip`). Even-year cycles. Files: `cm`, `indiv`, `oth`, `pas2`, `oppexp`. A newer zip **replaces** that cycle’s rows (resume-append only when the zip is unchanged).

**How:**

```bash
# Zips + extracts on Insurrection; DuckDB on Data. Default cycles 2024,2026.
FEC_CYCLES=2024,2026 ./overnight_fec_run.sh

# Download only (no DuckDB). Safe while another ingest holds the DB.
FEC_CYCLES=2024,2026 ./overnight_fec_download.sh
# or: FEC_CYCLES=2024,2026 python3 -u fec_processor.py --download-only

# smoke
FEC_CYCLES=2026 python -u irs990processor.py --step fec --nostats -v
```

`FEC_DATA_DIR` defaults to `{final_dir}/cms_data/fec`. Overnight wrappers set `/Volumes/Insurrection/990data/cms_data/fec` because `indiv` unzipped is several times the zip (Data is tight). `FEC_CYCLES` empty → even years 2000–2026.

Reload (DELETE cycle then COPY) when the zip is new **or** `.fec_ingested_{table}_{cycle}.json` does not match that zip’s size+mtime. Extract uses a separate `.from_zip.json` so a failed promote does not re-unzip, but it still DELETEs. Cycle DELETE is 50k-row batches plus CHECKPOINT; a single 29M `DELETE` OOMs at 8GB. Overnight `DUCKDB_MEMORY_LIMIT` default is 12GB. Zip member mtimes are not used.

`read_csv` turns empty TSV cells into NULL. FEC `contributor_name` is `VARCHAR NOT NULL`; COPY uses `COALESCE(col, '')` so blank names insert.

macOS has no `setsid` binary. Launch with `python3 launch_overnight_fec.py` (double-fork + `os.setsid()`). Do not ingest while USASpending (or any other `irs990processor`) holds `/Volumes/Data/final/irs990.duckdb`.
