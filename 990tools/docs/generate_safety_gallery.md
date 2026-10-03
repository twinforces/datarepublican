# generate_safety_gallery.py — unsafe physical DOT colocators

**What:** HTML gallery of physical yards (`Addresses.colocator` like `LL:%`, `address_type = dot_carrier_phy`). Three rankings, linked from `reports/master_index.html` as the DOT SMS Rogues gallery:

- `index.html` — volume: the 100 yards with the most SMS crash deaths (then crashes, OOS, BASIC). Apple/PU on this table is already score per truck; click that header to float 1-truck mass-casualty events. BASIC-only 1-trucks never enter this 100 (they live on Harm/PU).
- `rates.html` — two PU scalings on an expected-fatality scale. Weights are universe-wide `sum(fatalities)/sum(metric)` (`measure_fatality_weights`: 7,675 deaths / 255,582 crashes ≈ 0.030; 7,675 / 1,109,047 OOS ≈ 0.0069). Each BASIC category has its own weight (7,675 / count of that flag): unsafe ≈ 187, maint ≈ 23.5, HOS ≈ 12.7, drugs ≈ 12.6, fitness ≈ 10.3; cargo and crash-indicator are unused (weight 0). Yard/PU (outside the max): max(fatalities, sum of per-flag weights, w_crash×crashes, w_oos×OOS) / yard PU. Apple/PU (inside): each USDOT's max(fat, sum of its per-flag weights, w_crash×crashes, w_oos×OOS) / that USDOT's PU. Lit flags add. Hover a short label for that flag's expected-death weight; Flag wt is the sum. Twenty-six crashes on one truck score ~0.78, under a real death. Default sort is Yard/PU. Rank is 1, 2, 3 (color tracks Yard/PU). Yard/PU and Apple/PU are the same number on a one-truck yard; they split on mixed fleets (volume list, e.g. Dearborn 0.29 vs 1.17).
- `stacks.html` — identity farms / mill × harm: same shared-phone farm admission (paper extras PU ≤ 5 on a small-harm mill phone, or ≥2 small fatality DOTs). Sort is Mill×harm = Apple/PU × physical USDOT records. A mill is legal; a mill that did not vet a 1-truck killer or BASIC-alert driver ranks first. Signal Hill (725 names × ~0.030 crash-apple) is a crash mill; a 50-name mill with one death is 50.

**Why:** Census mail is paperwork. Raw crash volume is FedEx/UPS. An identity farm is how a bad carrier re-registers — new USDOT, same phone, same lot, new vinyl on the door. Signal Hill is the mill-phone version: hundreds of 1-truck LLCs sharing one number.

**How:**
```bash
python3 -u dot_reporting/generate_safety_gallery.py \
    --db-path /Volumes/Data/final/irs990.duckdb \
    --max-clusters 100
```
Writes `dot_reporting/reports/unsafe_colocators/`. Join key is `dot_norm`. Each USDOT uses the same Search Carriers / map / MOTUS / SAFER links as the other DOT reports, plus census power units, status, and phone. Detail pages reuse the shared carrier + phone-stack TanStack tables (`cluster_table_payload.build_detail_tables`). Column headers and dotted terms (Fat DOTs, New vinyl, Phone fat, Harm phones, Score, OOS, BASIC, Phy DOT#, Power units) have `title` hover definitions. Requires `dot_safety` tables in the same DuckDB. `file://` needs a local server for the CDN (`python3 -m http.server`).

National / by-state DOT cluster details (`generate_address_reports.py`, `generate_state_reports.py`) attach the same SMS totals onto every physical carrier row and show SMS cards next to active/inactive power units. Re-run those generators to refresh already-written HTML. Gallery pages link `../master_index.html`. The hub lists this suite as a featured Rogues row (`FEATURED` in `build_master_index.py`), not a matrix cell.

Ship regen (sequential, one DuckDB writer):

```bash
IRS990_DB_PATH=/Volumes/Data/final/irs990.duckdb \
  nohup caffeinate -i -s bash dot_reporting/regen_dot_sms.sh \
  > dot_reporting/regen_dot_sms_nohup.out 2>&1 &
disown
```

Phase 0 refreshes the gallery. Phase 1 writes stable undated national `colocator_clusters` / `zipcode_clusters` / `loose_colocator_clusters` / `address_clusters`. Phase 2 writes `dot_*_by_state` for the same four slices. `build_master_index.py` runs after each suite. Pass `--db-path` to the address generator; its default is still `irs990.duckdb.geolocate`. Do not `--step address`.
