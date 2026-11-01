# RECENTGOALS.md — 990tools

Active scratchpad (What / Why / How + hashes). Older detail lives in CHANGELOG.md.

---

## In flight: PPP cut by-state Fun cells

**What:** Generate the twenty `{cut}_{slice}_by_state` suites (Charged Off / Not yet paid / Forgiven / Payroll / Zero jobs × address / colocator / zip / loose), wrap onto Fun, rsync to DreamHost.

**Why:** Fun matrix By state is a live suite dir. The last regen used `--national-only` for the cuts, then `--only ppp,federal` for by-state. Token `ppp` is leftover only, so leftover `ppp_by_state` exists off-matrix and the five Fun rows stayed "—".

**How:** Chain `/Users/pierce/.grok/long-running-background-tasks/ppp_cut_bystate_chain.sh` (log `/tmp/ppp_cut_bystate.log`). `--only` lists the twenty by-state dirnames so national pages are not rewritten. Wrap `--copy-focus ppp,federal --force-copy`. Hash of the generator: `f8896005`.

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
| Grok geocode | off until asked. Census TSV / Photon / maps.co / OpenCage first. |

Do not treat GIN, UEI, CAGE, or PPP borrower name as an IRS EIN. Do not ingest PECOS/EIDL/LEIE unless asked.
