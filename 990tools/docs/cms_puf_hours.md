# cms_puf_hours_processor.py

**What:** 2024 Physician PUF service lines × CY 2026 PFS work minutes and work RVUs.

**Why:** PUF has units, not hours. 99213 is 30 minutes. This is the conversion for a later reasonableness look. Building sqft: exact `LL:` cell plus ±3 cells (~33 m). `--sqft-only` after footprint v2. CLIA has no NPI; it is not joined here.

**Tables:**

| Table | Grain |
|---|---|
| `cms_puf_service_hours` | NPI × HCPCS × place. `hours` = `Tot_Srvcs × adj_total_min / 60`. Null hours = no time file for that code (labs, many drugs). |
| `cms_puf_npi_hours` | NPI. `hours_timed` sums timed lines only. `srvcs_untimed` / `suppressed_srvcs` (provider total − published lines). `colocator` from NPPES practice. `leie`. |

Hours are a **lower bound**: CMS omits service rows with ≤10 benes. Do not treat T-MSIS claims as 15-minute units. `Bene_Avg_Age` is NPI-year mix.

**How:**

```bash
python3 launch_overnight_cms_puf_hours.py
```

Not on `--step all`. Direct `duckdb.connect` on Data.

```sql
SELECT npi, name, provider_type, hours_timed, srvcs_untimed, suppressed_srvcs, colocator
FROM cms_puf_npi_hours
WHERE hours_timed > 3000
ORDER BY hours_timed DESC
LIMIT 50;
```

2026-11-05 ingest (wrapper **39329**): 9,781,673 service rows (9,236,202 timed) / 1,207,473 NPIs (1,192,538 with colocator). 15,076 NPIs above 3000 timed hours. A raw sort puts clinical labs first — pathology units × a time-file row is not “one physician’s year.” Filter `provider_type` / `entity` before reading it as a person.

Building join (`--sqft-only`, ±3 cells): **7,475** exact / **434,429** near of 1,207,473 NPIs. Cut `entity='I' AND hours_timed > 2080 AND max_area_near < 4000` excluding lab/pathology: **5,816** NPIs. Therapy and optometry dominate a raw hours sort — filter `provider_type` before reading it as a physician-year.
