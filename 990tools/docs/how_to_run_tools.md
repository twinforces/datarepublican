# How to Run the Tools

## Generator (generate_name_rules_v19.1.py)
1. Edit name_rule_constants.py first (add to SIMPLES, GOOD_SINGLE_WORD_PRIORITIES, PROBLEM_SUFFIXES).
2. Clear cache for full run: rm -f rules_without_ein_cache.json name_rules_v19.json.gz
3. Run in background (long-running): 
   .venv/bin/python generate_name_rules_v19.1.py
   (use terminal(background=true, notify_on_complete=true) in Hermes)
4. Output: name_rules_v19.json.gz (dict keyed by canonical with variants).

WHY background: Full run takes 10-60+ min. Cache clear forces full pipeline (avoids "Restored session" short runs).

## v2 Analyzer
cd /Users/pierce/Development/datarepublican/990tools
PYTHONPATH=. .venv/bin/python /Users/pierce/.hermes/skills/irs-990-name-matching/scripts/analyze_name_space_v2.py --rules name_rules_v19.json.gz --top 30

Outputs: Console table + uncovered_high_value_detailed.tsv (raw/cleaned/best_canonical/similarity/EIN/reason).

Use this for "even better data" before adding rules.

## Reverse Coverage
.venv/bin/python reverse_coverage.py --threshold 0.4

Checks for bad variants. Should say "No significant bad variants detected".

## Main Pipeline (`irs990processor.py`)

Full step order:
```
irsfetch → zip → bmf → xml → fec → medicare → sanctions → dot → dot_safety → ppp → sam → usaspending → address → einless → match → geolocate_* → grant_match → backfill → photos → ratios → percentiles → export

XML writes Charities/Grants/Officers to DuckDB. Address rows append `/Volumes/Data/final/xml_addresses.tsv` (`XML_DEFER_ADDRESSES=1`). Load them at `--step address`, not during XML.
```

Common invocations:
```bash
# Full pipeline
python -u irs990processor.py --db-path irs990.duckdb

# Single step (resilient to CLI crashes — use nohup)
# FEC bulk 2024+2026 (fec.gov zips, not OpenFEC API). Zips on Insurrection.
FEC_CYCLES=2024,2026 ./overnight_fec_download.sh   # no DuckDB
FEC_CYCLES=2024,2026 ./overnight_fec_run.sh
# python3 launch_overnight_fec.py   # macOS double-fork + setsid
nohup python -u irs990processor.py --step sanctions --nostats -v > sanctions_step.log 2>&1 &
nohup python -u irs990processor.py --step dot --nostats -v > dot_step.log 2>&1 &
nohup python -u irs990processor.py --step dot_safety --nostats -v > dot_safety_step.log 2>&1 &
python3 -u dot_reporting/generate_safety_gallery.py --db-path /Volumes/Data/final/irs990.duckdb
nohup python -u irs990processor.py --step ppp --nostats -v > ppp_step.log 2>&1 &
# smoke: python -u irs990processor.py --step ppp --max-files 1 --nostats -v
nohup python -u irs990processor.py --step sam --nostats -v > sam_step.log 2>&1 &
# smoke: python -u irs990processor.py --step sam --max-files 1000 --nostats -v
# USASpending ZIPs on Insurrection; Postgres dump stays parked
# Default FY2022–2030 (UEI). One year: USASPENDING_START_YEAR=2025 USASPENDING_END_YEAR=2025
USASPENDING_DATA_DIR=/Volumes/Insurrection/990data/cms_data/usaspending \
  ./overnight_usaspending_run.sh
# smoke: --step usaspending --start-year 2025 --end-year 2025 --max-files 1000
# CMS 2024 Physician PUF + LEIE (not on --step all). CSVs on Insurrection.
python3 launch_overnight_cms_puf_download.py
python3 launch_overnight_cms_puf.py
# Hub parquet for new tables (HF_TOKEN from ../.envrc). Out on Insurrection.
python3 launch_overnight_hf.py
# After writers release Data: CHECKPOINT + ditto DuckDB to Insurrection.
python3 launch_overnight_duckdb_copy.py
python3 -u spending_reporting/generate_galleries.py --db-path /Volumes/Data/final/irs990.duckdb
python3 -u spending_reporting/generate_geo_reports.py --db-path /Volumes/Data/final/irs990.duckdb
nohup python -u irs990processor.py --step einless --nostats -v > einless_step.log 2>&1 &

# XML catch-up (16GB). TSV+COPY in bulk_insert. XML_ONLY=1 skips irsfetch.
XML_ONLY=1 ./overnight_ingest_run.sh

# Census pending → TSV → HTTP 2500/call (DuckDB closed) → COPY-back. Grok off.
./overnight_census_tsv_run.sh

# Catch up preprocess on pending_api (TSV skipped the live first stage). Grok off.
./overnight_preprocess_pending_api_run.sh
# or: python3 -u preprocess_grok_pending.py --statuses pending_api --batch-size 400

# Photon → maps.co → OpenCage on leftover pending_api. Grok off (misses → grok_pending).
./overnight_geolocate_api_run.sh
# pieces: python3 -u census_tsv_loop.py dump|geocode|apply
# resume: geocode skips ids already in results.tsv; skip dump if pending.tsv exists.

# Production resume (June 2026): sanctions + dot complete on /Volumes/Data/final/irs990.duckdb
nohup python -u irs990processor.py --start-step address --nostats -v > address_step.log 2>&1 &

# Resume after einless milestone (earlier checkpoint)
python -u irs990processor.py --start-step match --db-path irs990.duckdb
```

The `einless` step exports TSV inputs from DuckDB, runs phonebook cream + DAF resolution, and sets `recipient_ein_backfilled` (raw `recipient_ein` untouched). See `docs/pipeline_overview.md`.

## Processors (also callable standalone)
- `einless_processor.py` — phonebook backfill (normally run via `--step einless`)
- `address_matcher.py` — name + address backfill (`--step match`)
- `grant_match_processor.py` — colocator, GIN fallback, AuthoritativeEin table

Use the hermesex DuckDB copy: /Volumes/Data/final/irs990.duckdb.hermesex

## Git
git add generate_name_rules_v19.1.py name_rule_constants.py big_pharma_subsidy.json name_rules_v19.json.gz
git commit -m "v19.3: ..."

Always commit before major changes.

## Docs
See pipeline_overview.md and pharma_subsidy.md for SQL, context, and architecture.

Edit constants first, run greps + validation table before adding rules (your strong preference).
