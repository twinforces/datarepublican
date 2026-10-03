# build_master_index.py — hub for cluster report suites

**What:** Scans `dot_reporting/reports/` and writes `master_index.html`. The matrix is focus × slice × national/by-state. Rogues galleries (`unsafe_colocators` and later extras) sit in a featured section above the matrix; they are not a focus×slice cell.

**Why:** The SMS gallery is a ranking of unsafe yards, not another colocator/zip/address suite. `parse_suite` only matches `{focus}_{slice}_clusters` / `_by_state` names, so stuffing `unsafe_colocators` into the DOT/colocator cell would hide the real national colocator pages. Featured extras keep Volume / Harm/PU / Identity farms on the hub without colliding with the stack.

**How:**
```bash
python3 dot_reporting/build_master_index.py --reports-dir dot_reporting/reports
```
Stable undated suite dirs win over dated archives. Stamp is Pacific (`America/Los_Angeles`). Tests: `test_build_master_index.py`. Full DOT SMS refresh (gallery + 4 national + 4 by-state, rebuild after each) is `dot_reporting/regen_dot_sms.sh` against `/Volumes/Data/final/irs990.duckdb`. Do not run that in parallel with ingest or `--step address`.
