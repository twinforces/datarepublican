# cms_puf_processor.py

**What:** 2024 CMS Physician & Other Practitioners PUF + OIG LEIE into DuckDB.

**Why:** NPPES is who/where. This is Part B **utilization**: hours, HCPCS volume, `Bene_Avg_Age` at the NPI (not at the HCPCS line).

**Files** (`CMS_PUF_DATA_DIR`, default `/Volumes/Insurrection/990data/cms_data/medicare_puf`):

| File | Table | Age |
|---|---|---|
| `MUP_PHY_R26_P05_V10_D24_Prov.csv` | `cms_puf_provider` | `Bene_Avg_Age` |
| `PHY_R26_P05_V10_D24_Prov_Svc.csv` | `cms_puf_service` | none |
| `MUP_PHY_R26_P05_V10_D24_Geo.csv` | `cms_puf_geo` | none |
| `LEIE_UPDATED.csv` | `cms_leie` | n/a |

**How:** `python -u irs990processor.py --step cms_puf --nostats -v` (download + COPY). DuckDB on Data. Overnight wrappers still work.

Column names stay as CMS published them (`Rndrng_NPI`, `HCPCS_Cd`, `Bene_Avg_Age`).

2026-11-03 ingest (wrapper **11290**): provider 1,296,739 / service 9,781,673 / geo 268,350 / LEIE 84,093. `Bene_Avg_Age` mean 73 (min 7, max 94).

## Redaction (≤10 benes)

CMS drops the **row**, it does not star the cell. `cms_puf_service` has no null `Tot_Benes` / `Tot_Srvcs`; the minimum published `Tot_Benes` is **11**. A 200k-row scan of the CSV found no `*`. The By Provider file keeps NPI totals that still include those dropped lines.

| | |
|---|---|
| Null in an age/race/dual **bucket** | 1–10 people in that bucket. **0 is published as 0.** |
| `Bene_Avg_Age` / `Tot_Benes` on By Provider | Always present here (min benes 11, min age 7). |
| `provider.Tot_HCPCS_Cds − COUNT(service rows)` | Count of suppressed HCPCS lines for that NPI. 1.25M of 1.30M NPIs have a gap; median 11 codes, p90 43. |
| `provider.Tot_Srvcs − SUM(service.Tot_Srvcs)` | Suppressed units (often drugs/pharmacy). |
| Provider row, zero service rows | 89,266 NPIs: every HCPCS was a small cell. Median 10 codes / 35 units / 17 benes. |

Each missing line is 1–10 benes. You cannot recover which HCPCS those lines were.

Implied hours from this PUF × fee-schedule time: `docs/cms_puf_hours.md` (`cms_puf_npi_hours`). Building sqft is a later ATTACH.

## Reasonableness grade (design, not built)

Mickiewicz-style flags want **hours vs a calendar year**, **HCPCS mix vs specialty**, **childhood codes on a high `Bene_Avg_Age` NPI**, **DME concentration**, and **LEIE overlap**. Age is the NPI-year mix on By Provider, not the genetic-test line. Join `cms_puf_service` to `cms_puf_provider` on `Rndrng_NPI` and to `cms_leie` on NPI. Do not treat a 76-year average on a COVID-test HCPCS as a per-line age.

CMS minutes and work RVUs live in `cms_pfs_work_time` / `cms_pfs_rvu` (`docs/cms_clia_pfs.md`). Join `cms_puf_service.HCPCS_Cd` to `cpt_code` / `hcpcs`. CLIA labs are `cms_clia` (`PRVDR_NUM` is the CLIA id, no NPI).

Use the provider−service gap as a **hidden-volume** component: large suppressed units on a small published book, or a provider-only NPI (all lines ≤10 benes) with high `Tot_Srvcs`. Age-bucket nulls bound a young/old tail (1–10 people), which is a weak age-mismatch prior, not a line-level age.
