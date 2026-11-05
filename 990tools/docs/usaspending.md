# usaspending_processor.py

**What:** Slim COPY of USASpending Award Data Archive CSVs into DuckDB (`usaspending_actions`, rolled up as `usaspending_by_uei`). Join later to SAM pins by `recipient_uei` = `sam_entities.uei`.

**Why:** SAM is a phone book (no dollars). Archive CSVs are who got paid. Map federal $ onto the physical pins we already geocoded. Not a fraud cut.

**Source:** [Award Data Archive](https://www.usaspending.gov/download_center/award_data_archive) (`files.usaspending.gov/award_data_archive/`). All-Agencies **Full** ZIPs, contracts + assistance, one pair per federal FY. Treasury republishes every FY with the same stamp (`YYYYMMDD`) **by the 15th of each month**. Full file = that FY through the stamp date. Delta files exist; we use Full. Archive starts FY2008 (Custom Award Data before that). Objects expire ~60 days on the CDN, so keep the current stamp.

`--start-year` / `--end-year` default **2022–2030** (UEI). A 990 `--start-year 2017` is raised to 2022. Missing FYs (2028+ today) skip. One FY: `--start-year 2025 --end-year 2025`. Stamp override: `USASPENDING_STAMP=20261006`. Transaction grain; we keep UEI, obligation, agency, NAICS/CFDA, recipient city, place of performance. Join to SAM is `recipient_uei`.

`usaspending_by_uei` is the **sum of whatever FYs are in `usaspending_actions`**. Fun federal $ follows that total. FY2025-only is `--start-year 2025 --end-year 2025`.

**Parked:** the USASpending **Postgres database dump**. Do not download it.

Award **descriptions** and a keyword index are a second pass: `docs/usaspending_text.md` (`python3 launch_overnight_usaspending_text.py`). Slim `usaspending_actions` is unchanged.

**How:**

```bash
# ZIPs on Insurrection, DuckDB on Data. Default FY2022–FY2030 (UEI).
USASPENDING_DATA_DIR=/Volumes/Insurrection/990data/cms_data/usaspending \
  ./overnight_usaspending_run.sh

# One FY
USASPENDING_START_YEAR=2025 USASPENDING_END_YEAR=2025 \
  USASPENDING_DATA_DIR=/Volumes/Insurrection/990data/cms_data/usaspending \
  ./overnight_usaspending_run.sh

# Smoke: first 1,000 rows per CSV
USASPENDING_DATA_DIR=/Volumes/Insurrection/990data/cms_data/usaspending \
  python -u irs990processor.py --step usaspending --start-year 2025 --end-year 2025 --max-files 1000 --nostats -v
```

`--max-files` is a **row cap per CSV** (smoke), not a file count. Each archive ZIP may contain several CSVs; all of them COPY. Per-FY skip via `.usaspending_ingest.json` (path + size); adding years does not wipe years already loaded. Does **not** write Addresses. Pipeline: after `sam`, before `address`. ~3GB zip per FY (contracts+assistance); extracted CSVs are several times that — land on Insurrection.

Missing FYs skip after a failed **GET**, not a failed HEAD. `files.usaspending.gov` sometimes answers HEAD with an empty body while GET works; treating that as “missing” aborted a FY2022–2030 run at FY2024.

`download_utils.ensure_download` never unlinks dest until a GET to `.part` succeeds. A flaky HEAD that reports a tiny length against a local file ≥10MB is treated as implausible and the local copy is kept. curl 52/28/56 retry even when dest is still empty. curl 22 with dest <64KB unlinks and skips (unpublished FY).

macOS has no `setsid` binary. Launch the overnight wrapper with a Python double-fork + `os.setsid()` so a Grok 10h process-group kill does not take the job with it.

Reports: `python3 -u spending_reporting/generate_galleries.py --db-path /Volumes/Data/final/irs990.duckdb` (`docs/generate_galleries.md`).
