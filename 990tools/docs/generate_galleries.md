# spending_reporting/generate_galleries.py

**What:** Read-only HTML galleries from DuckDB: PPP charged-off leftovers, Not yet paid (FOIA Exemption 4), forgiven-on-unfinished, dollars per reported job, zero-job loans, and (once ingested) FY2025 USASpending dollars on SAM physical pins.

**Why:** PPP is interesting when the loan was written off, when SBA has not published a finished status, or when dollars-per-job / zero jobs look unreasonable. SAM has no dollars; USASpending supplies them. Reports follow the DOT gallery pattern onto grumpytechbro `fun/` later — they are not a Jekyll tools-index page.

**How:**

```bash
python3 -u spending_reporting/generate_galleries.py \
    --db-path /Volumes/Data/final/irs990.duckdb
```

Writes `spending_reporting/reports/` (gitignored). Exemption 4 displays as **Not yet paid**. `forgiven.html` is every status with a forgiveness amount (Paid in Full leftover $0 included), table ranked by forgiven ÷ approved with a $150k approved floor. Paid in Full often records `forgiveness_amount` a little above `current_approval_amount` because SBA remits the 1% interest that accrued until forgiveness (median ~100.7%). A handful of ~2× rows look like two draws stacked on one current approval. Leftover (approved − forgiven) goes negative on those rows. Every gallery page embeds the same Leaflet widget as the DOT blanks/cluster maps (`dot_reporting/templates/partials/leaflet_map.mako`): ranked-row physical pins, size/color = that page's rank metric, double-click opens Google Maps. Ranked tables use the DOT TanStack widget so leftover / forgiven % / $ per job / obligated $ are sortable. Federal pins join `usaspending_by_uei.uei` → `sam_entities.uei` → `Addresses` `sam_entity_phy` (recipient HQ, not place of performance). Skip federal_pins.html when the USASpending table is empty. `file://` needs a local server for the Leaflet CDN.

Geography is `spending_reporting/generate_geo_reports.py` → `reports/geo/`. Those suites wrap onto the existing Fun with Geography matrix as **PPP Charged Off / Not yet paid / Forgiven / Payroll / Zero jobs** plus **Federal spending** (Address / Colocator / Zipcode / Loose Colocator). This `reports/index.html` is the leftover gallery list, not a second Fun page.

Does not write DuckDB. Does not ingest. Postgres dump stays parked.
