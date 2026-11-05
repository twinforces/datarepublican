# cms_clia_pfs_processor.py

**What:** Latest CMS Provider of Services **Clinical Laboratories** file plus the CY 2026 Physician Fee Schedule **work RVU** file and **physician work-time** file, into the main DuckDB.

**Why:** A reasonableness grade that turns billed units into hours needs CMS’s own minutes and work RVUs. The PUF has volume; this is the conversion. CLIA is the lab roster (certificate, address, test volume) so a high-volume genetic-test NPI can be checked against an actual certified lab. CLIA has **no NPI** — `PRVDR_NUM` is the CLIA id (`01D0026356`).

**Tables** (CREATE OR REPLACE, CMS grain, not `--step all`):

| Table | Source | Grain |
|---|---|---|
| `cms_clia` | POS Clinical Laboratories, newest quarter (Q3 2026 as of 2026-10) | One lab. `PRVDR_NUM`, `FAC_NAME`, `ST_ADR`, `STATE_CD`, `ZIP_CD`, `CRTFCT_TYPE_CD`, `FORM_116_TEST_VOL_CNT` |
| `cms_pfs_rvu` | `PPRRVU2026_Oct_nonQPP.csv` inside RVU26D (skip 9 copyright lines) | HCPCS × modifier. `work_rvu`, facility/non-facility PE, MP, pre/intra/post fractions |
| `cms_pfs_work_time` | CY 2026 PFS Final Rule Physician Work Time | `cpt_code`. `intra_min`, `total_min`, `adj_total_min` (efficiency-adjusted). 99213 is 30 minutes |

`hcpcs_codes` stays the short-descriptor lookup (`load_hcpcs_from_cms.py` still skips RVU numbers). Modifier rows stay in `cms_pfs_rvu`.

**How:**

```bash
python3 launch_overnight_cms_clia_pfs.py
```

Files on Insurrection (`CMS_CLIA_PFS_DATA_DIR`, default `/Volumes/Insurrection/990data/cms_data/clia_pfs`). DuckDB on Data. Direct `duckdb.connect`. Overnight is a Python double-fork so a Grok 10h process-group kill misses it.

Smoke without overnight:

```bash
python3 -u cms_clia_pfs_processor.py --download-only
python3 -u cms_clia_pfs_processor.py --skip-download --db /Volumes/Data/final/irs990.duckdb
```

`--skip-discover` uses the hardcoded Q3 2026 / RVU26D / 01-08-2026 work-time URLs. Default is: `data.cms.gov/data.json` for the newest CLIA CSV, PFS relative-value index for the RVU zip, CMS-1832-F for the work-time zip.

Join later: `cms_puf_service.HCPCS_Cd = cms_pfs_work_time.cpt_code` for minutes × `Tot_Srvcs`. CLIA joins NPPES on name/street, not NPI. Do not treat T-MSIS `total_claims` as 15-minute units.

2026-11-04 ingest (wrapper **14844**): `cms_clia` 686,058 (TN 16,001) / `cms_pfs_rvu` 19,226 / `cms_pfs_work_time` 9,725. 99213 `work_rvu` 1.3, `adj_total_min` 30.
