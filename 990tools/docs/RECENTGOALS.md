# RECENTGOALS.md — 990tools

Active scratchpad (What / Why / How + hashes). Older detail lives in CHANGELOG.md.

---

## 2026-11-05: CMS PUF implied hours

**What:** Wrapper **39329**, 13:04. `cms_puf_service_hours` 9,781,673 (9,236,202 timed). `cms_puf_npi_hours` 1,207,473. Building join: 7,475 exact / 434,429 within ±3 cells. 5,816 individuals with >2080h and max nearby sqft <4000 (labs/pathology excluded).

**Why:** PUF units × 99213=30 min is the conversion. Hours are a lower bound (≤10-bene lines omitted). Labs sit at the top of a raw sort.

**How:** `python3 launch_overnight_cms_puf_hours.py`. Doc: `docs/cms_puf_hours.md`. Test: `test_cms_puf_hours.py`. Hash: `897d9d8b`.

---

## 2026-11-05: building footprint area v2 (FlipCoordinates)

**What:** Wrapper **97867**, 13:20. 130,099,920 buildings, 0 bad `area_sqft`. CA/TX median ~2,210 sqft. Allcare exact cell empty; nearest 6,539 sqft (v1 was 953).

**Why:** v1 treated longitude as latitude.

**How:** `INGEST_VERSION=2`, `--no-download`. Doc: `docs/building_footprints.md`. Hash: `0839098f`.

---

## 2026-11-04: USASpending award text + FTS

**What:** Descriptions / PSC / CFDA titles → `usaspending_award_text` + DuckDB FTS. Wrapper **50770**, 18:46. 51,082,389 awards (26.6M contract / 24.5M assistance), all with description. Slim actions still 63,616,013 with no description column.

**Why:** Keyword search over federal awards. Postgres dump stays parked.

**How:** `python3 launch_overnight_usaspending_text.py`. Doc: `docs/usaspending_text.md`. Test: `test_usaspending_text.py`. First merge (**46442**) OOM'd; 32 hash partitions fixed it. Hash: `e2149ece`.

---

## 2026-11-04: CMS CLIA + PFS work RVU/time

**What:** POS Clinical Laboratories Q3 2026 + PPRRVU work RVUs + CY 2026 physician work-time into `/Volumes/Data/final/irs990.duckdb`. Wrapper **14844**, 15:33. `cms_clia` 686,058 (TN 16,001); `cms_pfs_rvu` 19,226; `cms_pfs_work_time` 9,725. 99213 = 1.3 work RVU / 30 minutes.

**Why:** PUF volume × CMS minutes/RVU is the conversion a reasonableness grade needs. CLIA is the lab roster; it has no NPI (`PRVDR_NUM` is the CLIA id).

**How:** `python3 launch_overnight_cms_clia_pfs.py`. Doc: `docs/cms_clia_pfs.md`. Test: `test_cms_clia_pfs.py`. Hash: `c3f8fd3b`.

---

## 2026-11-04: Microsoft building footprints sidecar

**What:** US v2 GeoJSON into `/Volumes/Insurrection/990data/buildings.duckdb`. Wrapper **10619**, 17:01. `building_footprints` 130,099,920 (51 states); `building_colocator` 129,303,024. Sidecar 22G.

**Why:** Pin area (house vs hospital plate) without a geocoder. Photon/Docker is off.

**How:** `python3 launch_overnight_building_footprints.py`. Doc: `docs/building_footprints.md`. Exact Allcare cell is empty; nearest building 953 sqft one cell east. Area NaN for CA/TX and several western states (`ST_Area_Spheroid`); centroids still there. Item 3 (USASpending descriptions) is unblocked. Hash: `7549fa11`.

---

## 2026-11-03: Hugging Face full discovery dump

**What:** Every public DuckDB table on `piercewetter3/irs-990-parsed` (wrapper **86819**, 13:36, 48/48). README/manifest rewritten. Overnight default is discovery.

**Why:** House rule: the Hub is the public warehouse. The 07:00 run was `--tables` for the new ingest only; SAM, FMCSA SMS, and census TSV were the gaps.

**How:** `python3 launch_overnight_hf.py`. `--out` on Insurrection. Raw/ops stay skipped (`medicare_raw_spending`, `Zips_raw`, `fec_raw_committees`, pipeline scratch). Doc: `docs/upload_to_hf.md`.

---

## 2026-11-03: Hugging Face new tables + Insurrection DuckDB copy

**What:** Eleven FEC / USASpending / CMS PUF / LEIE tables on `piercewetter3/irs-990-parsed` (HF wrapper **20175**, 07:00). CHECKPOINT+ditto Data → Insurrection `irs990.duckdb` (copy wrapper **30855**, 07:50). Both files 116,086,026,240 bytes, mtime 2026-11-03 05:48:56.

**Why:** Hub still had the May 990+FEC dump plus `ppp_loans`. Insurrection was the 2026-10-30 09A copy.

**How:** `python3 launch_overnight_hf.py` (`--tables`, README left in place). Then `python3 launch_overnight_duckdb_copy.py`. Hash: `363b6ef3`.

---

## In flight: PPP cut by-state Fun cells

**What:** Generate the twenty `{cut}_{slice}_by_state` suites (Charged Off / Not yet paid / Forgiven / Payroll / Zero jobs × address / colocator / zip / loose), wrap onto Fun, rsync to DreamHost.

**Why:** Fun matrix By state is a live suite dir. The last regen used `--national-only` for the cuts, then `--only ppp,federal` for by-state. Token `ppp` is leftover only, so leftover `ppp_by_state` exists off-matrix and the five Fun rows stayed "—".

**How:** Chain `/Users/pierce/.grok/long-running-background-tasks/ppp_cut_bystate_chain.sh` (log `/tmp/ppp_cut_bystate.log`). `--only` lists the twenty by-state dirnames so national pages are not rewritten. Wrap `--copy-focus ppp,federal --force-copy`. Hash of the generator: `f8896005`. Last rsync died `Connection reset by peer` on DreamHost (2026-11-01).

---

## 2026-11-03: FEC 2024+2026, USASpending FY2022–2027, CMS Physician PUF + LEIE

**What:** FEC bulk 2024+2026 (wrapper **98751**, 10 cycle-files, 103,002,959 rows). USASpending Award Data Archive FY2022–FY2027 stamp 20261006 (wrapper **91044**, 57 files, 63.6M actions, 340,396 UEIs; FY2028 unpublished skip; FY2025 marker skip). CMS 2024 Physician PUF + OIG LEIE (wrapper **11290**): `cms_puf_provider` 1,296,739 (`Bene_Avg_Age` mean 73), `cms_puf_service` 9,781,673, `cms_puf_geo` 268,350, `cms_leie` 84,093.

**Why:** Campaign-finance streets next to 990/SAM/PPP; federal $ on SAM pins for UEI years; Part B utilization + exclusion list for a later reasonableness grade. `Bene_Avg_Age` is NPI-year mix, not per-HCPCS.

**How:** `python3 launch_overnight_fec.py`; `python3 launch_overnight_usaspending.py`; `python3 launch_overnight_cms_puf.py`. Docs: `docs/fec.md`, `docs/usaspending.md`, `docs/cms_puf.md`. Hashes: `edd86a52` FEC, `41716877` USASpending, `6bef052c` CMS PUF, `363b6ef3` HF.

---

## 2026-11-01: USASpending + PPP/federal Fun geography

**What:** FY2025 Award Data Archive slim COPY (`usaspending_actions` / `usaspending_by_uei`). Five PPP cut rows plus Federal spending on existing Fun with Geography. Trucking Widen, TanStack everywhere, Bad address cross-links when sibling Fun pages exist. Exemption 4 displays as **Not yet paid**. Forgiven gallery includes Paid in Full leftover $0; leftover can go negative because SBA remits 1% interest on top of principal.

**Why:** SAM has no dollars. Loan galleries rank rows; geography answers who else sits on the pin. Leftover>0 cannot rank the other four cuts. Postgres dump stays parked.

**How:** `./overnight_usaspending_run.sh`. `python3 -u spending_reporting/generate_geo_reports.py --only ppp_charged_off,ppp_not_yet_paid,ppp_forgiven,ppp_payroll,ppp_zero_jobs,federal`. Wrap from grumpytechbro.com: `python3 scripts/build_site.py --copy-focus ppp,federal --force-copy`. `--only ppp` is leftover only. Docs: `docs/usaspending.md`, `docs/generate_galleries.md`, `docs/generate_geo_reports.md`. Tests: `test_usaspending_processor.py`, `test_generate_geo_reports.py`. Hash: `f8896005`. Fun wrap (GTB, no remote): `5934eef`.

---

## Standing pipeline (hashes on the ingest commits)

| | |
|---|---|
| SAM public entities | `322b469e` — `docs/sam.md` |
| PPP FOIA loans | `667043d0` — `docs/ppp.md` |
| DOT SMS + Rogues | `917e643c` — `docs/generate_safety_gallery.md` |
| XML Addresses → TSV | `15b785fb` |
| FEC bulk 2024+2026 | `edd86a52` — `docs/fec.md` |
| USASpending FY2022–2027 | `41716877` — `docs/usaspending.md` |
| USASpending award text + FTS | `e2149ece` — `docs/usaspending_text.md` |
| CMS Physician PUF + LEIE | `6bef052c` — `docs/cms_puf.md` |
| Building footprints sidecar | `7549fa11` ingest, `0839098f` area v2 — `docs/building_footprints.md` |
| CMS CLIA + PFS work time | `c3f8fd3b` — `docs/cms_clia_pfs.md` |
| CMS PUF implied hours | `897d9d8b` — `docs/cms_puf_hours.md` |
| Grok geocode | off until asked. Census TSV / Photon / maps.co / OpenCage first. |

Do not treat GIN, UEI, CAGE, or PPP borrower name as an IRS EIN. Do not ingest PECOS or EIDL unless asked. LEIE is in (`cms_leie`, 2026-11-03).
