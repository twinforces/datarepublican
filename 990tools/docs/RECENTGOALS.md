# RECENTGOALS.md — 990tools

Active scratchpad (What / Why / How + hashes). Older detail lives in CHANGELOG.md.

---

## In flight: Hugging Face new tables + Insurrection DuckDB copy

**What:** Upload FEC / USASpending / CMS PUF / LEIE parquet to `piercewetter3/irs-990-parsed`, then CHECKPOINT+ditto `/Volumes/Data/final/irs990.duckdb` → `/Volumes/Insurrection/990data/irs990.duckdb`.

**Why:** Hub still has the May 990+FEC dump plus a later `ppp_loans` add. Insurrection's DuckDB is the 2026-10-30 09A copy (same 116,086,026,240 bytes, older mtime).

**How:** `python3 launch_overnight_hf.py` (`--tables` so the Hub README is not rewritten as a partial card). `--out` on Insurrection. After upload: CHECKPOINT Data, refuse dest>src, `ditto`, compare sizes. Doc: `docs/upload_to_hf.md`.

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
| CMS Physician PUF + LEIE | `6bef052c` — `docs/cms_puf.md` |
| Grok geocode | off until asked. Census TSV / Photon / maps.co / OpenCage first. |

Do not treat GIN, UEI, CAGE, or PPP borrower name as an IRS EIN. Do not ingest PECOS or EIDL unless asked. LEIE is in (`cms_leie`, 2026-11-03).
