# fmcsa_safety_processor.py — FMCSA SMS crash / inspection / BASIC

**What:** Pipeline step `dot_safety`. Downloads the current-month SMS public files from data.transportation.gov and promotes them to `fmcsa_sms_crashes`, `fmcsa_sms_inspections`, and `fmcsa_sms_basics`. Adds `dot_norm` (leading zeros stripped) for joins. Does not write `Addresses`.

**Why:** Company Census (`dot` step) is identity and yards. Harm lives in SMS: ~256k crash involvements, ~5.8M inspections, ~717k BASIC summaries. Those files are the monthly measurement window, not full MCMIS history. Stacking them on physical `LL:` colocators is how a yard with many paper identities shows up as one unsafe place.

**How:** `--step dot_safety` after `dot`. CSVs under `{final_dir}/cms_data/dot/`. Skip via `.crash_ingest.json` / `.inspection_ingest.json` / `.basics_ingest.json` when source size/mtime match. `sms_per_dot_sql()` / `load_sms_by_dot()` feed the gallery and existing cluster detail pages (fatalities, crashes, OOS, BASIC on each USDOT). Gallery: `docs/generate_safety_gallery.md`. Do not re-run `--step dot` or `--step address` for this.
