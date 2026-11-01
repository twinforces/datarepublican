# usaspending_processor.py

**What:** Slim COPY of USASpending Award Data Archive CSVs into DuckDB (`usaspending_actions`, rolled up as `usaspending_by_uei`). Join later to SAM pins by `recipient_uei` = `sam_entities.uei`.

**Why:** SAM is a phone book (no dollars). Archive CSVs are who got paid. Map federal $ onto the physical pins we already geocoded. Not a fraud cut.

**Source:** [Award Data Archive](https://www.usaspending.gov/download_center/award_data_archive) (`files.usaspending.gov/award_data_archive/`). FY2025 All Agencies contracts + assistance Full ZIPs (stamp `20261006`). Transaction grain; we keep UEI, obligation, agency, NAICS/CFDA, recipient city, place of performance.

**Parked:** the USASpending **Postgres database dump**. Do not download it.

**How:**

```bash
# ZIPs on Insurrection, DuckDB on Data
USASPENDING_DATA_DIR=/Volumes/Insurrection/990data/cms_data/usaspending \
  ./overnight_usaspending_run.sh

# Smoke: first 1,000 rows per CSV
USASPENDING_DATA_DIR=/Volumes/Insurrection/990data/cms_data/usaspending \
  python -u irs990processor.py --step usaspending --max-files 1000 --nostats -v
```

`--max-files` is a **row cap per CSV** (smoke), not a file count. Each archive ZIP may contain several CSVs; all of them COPY. Idempotent `.usaspending_ingest.json` (path + size + ingest_version). Does **not** write Addresses. Pipeline: after `sam`, before `address`.

macOS has no `setsid` binary. Launch the overnight wrapper with a Python double-fork + `os.setsid()` so a Grok 10h process-group kill does not take the job with it.

Reports: `python3 -u spending_reporting/generate_galleries.py --db-path /Volumes/Data/final/irs990.duckdb` (`docs/generate_galleries.md`).
