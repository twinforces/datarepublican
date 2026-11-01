# spending_reporting/generate_geo_reports.py

**What:** DOT-style geography for PPP leftover $ plus five PPP cuts (Charged Off, Not yet paid, Forgiven, Payroll, Zero jobs) and FY2025 federal $ on SAM physical pins. Slices: address, colocator, zip code, loose colocator (0.5°). Each slice has a national list and a by-state heatmap. Cluster indexes, by-state ranking tables, and pin detail tables (PPP loans, SAM/UEI, address types, charities) use the DOT TanStack table so any column is sortable. `tanstack_assets_html` slices `tanstack_table_assets.mako` after `</%doc>` — the doc comment mentions `<%text>` in prose, and a first-match find leaked that sentence onto Fun indexes. Pin pages use the trucking zoom-out: breadcrumbs (`All reports › PPP Charged Off · Address`) and **Widen:** address → colocator (all street variants at the pin) → loose 0.5° → ZIP → state. Suite indexes have breadcrumbs only. When the pin also has NPPES / contractor / grant / charity / DOT / FEC rows, a **Bad address** panel lists those Fun lanes and links the sibling detail page when that file exists (national top-N often has the type but not the page). Other PPP cuts and Federal spending at the same key are linked the same way. Types persist in the sidecar so `refresh_geo_overlap` can patch pages after Fun copies land.

**Why:** The loan galleries rank individual rows. Cluster reports answer “who else sits on this pin / ZIP / half-degree cell / state.” Leftover>0 cannot rank forgiven leftover $0, payroll $/job, or zero-job approved, so those are their own Fun rows. Federal $ is FY2025 USASpending rolled to SAM HQ, not place of performance.

**How:**

```bash
python3 -u spending_reporting/generate_geo_reports.py \
    --db-path /Volumes/Data/final/irs990.duckdb \
    --only ppp_charged_off,ppp_not_yet_paid,ppp_forgiven,ppp_payroll,ppp_zero_jobs,federal
```

Writes cluster pages under `spending_reporting/reports/geo/`. `geo/index.html` lists the suite dirs. Public lanes are the five PPP cut rows plus **Federal spending** on existing Fun with Geography (`grumpytechbro.com/fun/`), same Address / Colocator / Zipcode / Loose Colocator columns as DOT, National + By state. Wrap with `python3 scripts/build_site.py --copy-focus ppp,federal` from that repo (`ppp` expands to the five cuts). `--max-clusters` caps the national lists (default 100). `--only ppp` is leftover only and does not match `ppp_charged_off_*`. Cut tokens match that cut’s eight dirs. Colocator by-state dirs stay `{domain}_by_state`; other slices use `{domain}_{slice}_by_state`. State pages show up to `min(40, 10% of clusters)`. Federal by-state indexes add two extra heatmaps: **$ / pop** (Census Vintage 2024 resident estimates) and **$ / electoral vote** (2024 College, 538 including DC). Recipient HQ, not place of performance. Weights live in `spending_reporting/state_weights.py`. Rewrite maps without regenerating cluster pages: `--heatmaps-only --only federal`. `--national-only` skips by-state dirs. `--skip-details` rewrites indexes and leaves existing pin HTML. After suites write, Widen: links are refreshed so address pages can point at colocator/zip files that did not exist yet, then Bad address links are refreshed against Fun + DOT reports. `file://` needs a local server for the Leaflet CDN.

Does not write DuckDB. Does not ingest. Postgres dump stays parked.
