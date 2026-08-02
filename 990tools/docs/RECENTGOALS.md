# RECENTGOALS.md — 990tools

This is the active scratchpad for current and recently completed work. Entries in What / Why / How + git hash format. Migrate older entries to CHANGELOG.md periodically.

---

## 2026-08-01: Report suite — admission, ranking, contractor owner_id (in flight)

**What:** Fix contractor `Addresses.owner_id` (pipeline + prod backfill). Rework focus/DOT **admission** to domain membership only (`focus_count > 0` / phy carriers > 0) — drop default `min_multi` / `min_focus` density floors. Medicare rank = **paid/HCPCS types**; FEC rank = density + resolve committee names; state focus pages load entities. Regen v2 running for medicare/contractor/fec national + by-state (`2026-08-01` suites). Film script: `demo.md`.

**Why:** Film/review found empty contractor names (100% null `owner_id`), Medicare hospitals winning pure-$ sort, single-HCPCS single-address mills gated out by `min_focus=30`, and FEC mega-wires as bare committee IDs. Multi-type crossover floors were not useful.

**How:**
- `models/contractor.py`: `owner_id=self.id` (same pattern as Grant/Officer)
- `scripts/backfill_contractor_owner_id.py` — prod backfill: **984,680** joinable (was 0)
- `generate_focus_reports.py` / `generate_address_reports.py` / `generate_state_reports.py` / `state_research.py`: admission `focus>0`, optional floors only if `--min-* > 0`; Medicare `paid_per_hcpcs_type`; FEC contributors first + committee name join
- `domain_briefing.py` + index templates: self-document rank/admission
- Log: `dot_reporting/regen_focus_v2_*.log` (admission=focus>0)

**Git hashes** (`grokrefactor3`): `18082440` (contractor owner_id + backfill), `a30ba6af` (admission/rank/entities); docs hygiene on branch tip after those two.

**In progress:** full matrix regen v2 (national then by-state); master index refresh after each suite.

---

## 2026-07-10: grant_match kickoff + DOT multi-slice reports

**What:** Pipeline order: `geolocate_archive` → **`grant_match`** → `backfill` → **`photos`**. DOT cluster HTML generator accepts `--slice-by` address / colocator / zipcode / loose_colocator. Project hygiene commit; production `grant_match` started.

**Why:** Photos should not block grant_match / backfill. grant_match fills and uses loose_colocator for hail-mary EIN match. DOT fraud review needs slices beyond exact street address (shared LL grid, zip stacks).

**How:**
- `irs990processor.PIPELINE_STEPS` photos after backfill
- `dot_reporting/generate_address_reports.py --slice-by … --db-path /Volumes/Data/final/irs990.duckdb.geolocate`
- grant_match:
  ```bash
  nohup python3 -u irs990processor.py --start-step grant_match --stop-step grant_match \
    -v --final-dir /Volumes/Data/final --nostats >> grant_match_20260710.log 2>&1 &
  ```

---

## 2026-07-09: Geocoding victory + phase snapshot (next phase ready)

**What:** Declared geocoding victory (bulk Grok/API autopilot off; low-weight `pending_api` → `geocode_tail`; preprocess pass). Project hygiene committed on `grokrefactor3` (`85774d93`). Production DB snapshot frozen as phase marker before next work.

**Why:** Clean restore point after geolocate success so the next pipeline phase can mutate `irs990.duckdb` without losing the post-geocode state.

**How:**
- Live DB: `/Volumes/Data/final/irs990.duckdb` (~91 GB; **Data2 not mounted** as of this note).
- Snapshot: `cp -c` (APFS clone) → `/Volumes/Data/final/irs990.duckdb.geolocate`
- Verified: same size, readonly open OK, `Addresses` count **95,196,749**
- No writers / no active WAL at snapshot time
- Prior phase snapshots on same volume: `.dot`, `.address`, `.xml`, `.bmf`, `.siding`
- Victory tooling: `declare_geocoding_victory.py`, `preprocess_geocode_tail.py`, overnight loop victory guard (see CHANGELOG)

**Next / in progress:** `geolocate_archive` bookend polish (see entry below).

---

## 2026-07-09: geolocate_archive + geolocate_prev bookend (status in TSV)

**What:** Extend archive TSV so the next rebuild can restore **geocoding_status** with **colocator**, not only the colocator string. Correct model: `loose_colocator` lives on **Addresses** (and owners), is **not** stored on Geocoding or the archive. **`geolocate_prev` = FRONT** (archive load); **`geolocate_archive` = BACK** (finalize + export).

**Why:** Paid/free geocode results must round-trip without re-spend; status (Match:Census, Match:Grok-4, grok:UNKN, …) matters for analytics and for not collapsing everything to Match:Archive.

**How:**
- Archive columns: `canonical_address`, `colocator`, `geocoding_status`
- `geolocate_prev` restores status + colocator onto **Geocoding**; legacy 2-col TSV still works (infers Match:Archive / grok:*)
- After front load **and** at back archive: `finalize_colocators_from_geocoding` → Addresses + owners + lat/lon + **loose_colocator**
- Files: `geolocate_archive_processor.py`, `geolocate_prev_processor.py`

**Production run (2026-07-09):** succeeded.
- Fetched 13,904,584 from DB; kept 14,346 archive-only keys; **13,918,930** written
- Output: `/Volumes/Data/final/geocode_archive_distinct.tsv.gz` (~233 MB)
- Top statuses: Match:Census 9.47M, Match:Archive 2.54M, Match:PO 1.10M, Match:Grok-4 232k, …
- Log: `geolocate_archive_20260709.log`

**Empty officer address shells (not a geocode miss):**
- Officers 4.12M; ~97% have an Addresses row; only **38%** have real fields+`geocoding_id`
- **2.43M** officer rows are blank shells (also ~22k contractor); address-dedup requires
  `canonical_address != ''`, so blanks never share one Geocoding master (0 blank Geocoding rows)
- `geolocate_prev` now **deletes** empty shells (no line1/city/zip/po_box, null gid, no colocator)
  before archive load
- **Production delete-only (2026-07-09):** removed **2,452,704** shells (officer 2,431,021 + contractor 21,683);
  Addresses 95,196,749 → **92,744,045**; officer addrs left 1,581,654 (all with gid); 0 shells remain

**Run command:**
```bash
python3 -u irs990processor.py --start-step geolocate_archive --stop-step geolocate_archive \
  -v --final-dir /Volumes/Data/final --nostats
```

---

## 2026-07: Geolocate Trilogy — Production `geolocate_new` (v9; historical)

**What:** Geolocate trilogy refactor landed and ran in production. Early notes used `/Volumes/Data2/final`; **current live path is `/Volumes/Data/final/irs990.duckdb`**. Run **v9** was `geolocate_step_20260630_v9.log`. Adaptive monitor: `geolocate_monitor.sh`.

**Why:** Split monolithic geolocate into prev (archive/loose colocator) → new (Census/Photon/maps/OpenCage) → grok (xAI batch) → archive (round-trip cache). Classify Grok failures as terminal `grok:<CODE>` statuses for pattern mining instead of opaque `No_Match`. Self-hosted Photon on Kamatera VPS — no throttle needed.

**How:**
- **Pipeline:** `geolocate_prev` → `geolocate_new` → `geolocate_grok` → `geolocate_archive` (wired in `irs990processor.py`; legacy `geolocate`/`geolocate1` aliases preserved).
- **Grok failure taxonomy** (`constants.py`): `NOTA`, `VAGUE`, `AMBIG`, `REDACT`, `UNKN` → `grok:<CODE>` colocator; archive + prev load them as terminal; export `grok_failures_for_patterns.tsv.gz` when grok_pending drained.
- **Photon:** self-hosted `45.61.62.160:2322`, 32 workers, 0s delay (`GEOCODING_PHOTON_SELF_HOSTED_*`).
- **v9 status (2026-07-02 ~07:17):** ~38h uptime; session fed **50,000**/10,109,423; ~805k resolved; ~39k matches; hard-tail serial grind. Later: victory declared 2026-07-09 (see entry above).
- **After drain path (done in spirit):** Grok batch + pattern mining + victory/tail tiering; archive step may still polish round-trip cache.

**Key artifacts:** `geocoding_api_processor.py`, `geolocate_grok_processor.py`, `geolocate_archive_processor.py`, `geolocate_prev_processor.py`, `geolocate_monitor.sh`, `pipeline.py` (feed milestones + admission cap).

**Hygiene (2026-07-02):** Docs sync; old geolocate logs archived to `archive/geolocate_runs_2026-06/`; runtime logs/pid/monitor state gitignored. **2026-07-09:** victory commit + `.geolocate` DB snapshot.

---

## 2026-06: Address Step Production Validation (Incremental Dedup)

**What:** Production-validated `--step address` on `/Volumes/Data/final/irs990.duckdb`. Incremental SQL dedup assigned `master_id` to 69.3M new rows (DOT/sanctions/FEC) without resetting existing roots.

**Why:** DOT + sanctions added ~5.4M address rows with NULL `master_id`. Needed incremental merge (MIN `address_id` root) so charity dedup state was preserved. v1 failed silently — geocoding committed but table swap rolled back on index OOM.

**How:**
- `address_deduplication_processor.py`: phased transactions; `_commit_or_raise()` after step 2 geocoding and step 4 table swap; `_verify_dedup_applied()`; indexes one-at-a-time.
- Production: 95,196,749 addresses; **0** NULL `master_id`; 14,342,486 Geocoding rows; DB ~91 GB.
- Logs: `address_step_20260620_v2.log` (dedup); `address_step_20260620_v2_indexes.log` (indexes 7–9 + VACUUM after OOM on index 7).
- Commits: `09d5c50d` (logging), `7ea4a2a1` (phased commit fix).

**Resume:** `--start-step match` (skip `einless` — phonebook backfills already applied June 15).

**Next pipeline architecture (planned):**
```
… → match → geolocate_prev → geolocate_new → geolocate_archive → photos → grant_match → …
```
Rename/refactor current `geolocate1`/`geolocate` into trilogy; `grant_match` stays as colocator/loose_colocator hail-mary.

**Fraud research direction:** Cross-pollination signals (charity↔contractor, grants↔sanctions, FEC↔NGO, Medicare providers receiving grants, DOT address stacking). Early signal: shared `canonical_address` across `address_type`s — now in stats report. Deeper signals after geolocate trilogy + `loose_colocator`. Standalone `dot_fraud_signals.py` (or similar) for carrier stacking — not a pipeline step.

**Report UX (planned):** Fraud/colocation reports should render canonical addresses as clickable Google Maps links (`https://www.google.com/maps/search/?api=1&query=…`).

---

## 2026-06: Match Step Kickoff

**What:** Production `--start-step match` on `/Volumes/Data/final/irs990.duckdb` (skip `einless`).

**Why:** Name rules + address_matcher after incremental address dedup and external ingests; validates einless backfills.

**How:** `nohup python3 -u irs990processor.py --start-step match --stop-step match -v > match_step_20260620.log 2>&1`

**After:** geolocate_prev → geolocate_new → geolocate_archive → grant_match.

---

## 2026-06: DOT Step Production Validation (FMCSA Motor Carrier Census)

**What:** Production-validated `--step dot` on `/Volumes/Data/final/irs990.duckdb`. FMCSA Company Census CSV (4.45M carriers) → `dot_carriers` + `Addresses` (`dot_carrier_phy`, `dot_carrier_mail`).

**Why:** Same-building colocation signals — FEC committees, OFAC sanctions, and trucking carriers at one canonical address (shell offices, nominee agents). Ingest only; matching deferred to `grant_match` / `geolocate1`.

**How:**
- `dot_processor.py`: curl download (`company_census.csv`, ~1.6 GB), streaming `csv.DictReader`, FEC-style resume, idempotent `.dot_census_ingest.json` marker (`INGEST_VERSION = 1`).
- Production counts: `dot_carriers` 4,454,157; `dot_carrier_phy` 4,450,675; `dot_carrier_mail` 977,160.
- Data: `{final_dir}/cms_data/dot/company_census.csv`
- Log: `dot_step_20260620_v5.log` (final resume after laptop crash; v1–v4 partial runs)

**Ops notes:** OOM at 30k (v1) → 8GB + 5k batches; harness timeout at 545k (v2); OOM at 3.9M (v3); laptop crash at 4.36M (v4). Resume + 12GB/2.5k batches finished last 92k (v5).

**Success signal:** Committed `d43a902b` (impl) + `2fced65e`/`2b2399f3`/`a80c9d92` (OOM/resume fixes). Ingest marker written.

**Resume:** `--start-step address` on production DB.

---

## 2026-06: Sanctions Step Production Validation (OFAC SDN)

**What:** Production-validated `--step sanctions` on `/Volumes/Data/final/irs990.duckdb`. Treasury OFAC SDN advanced XML ingest → `sanctioned_*` tables + `Addresses` (`address_type=ofac_sanction`).

**Why:** Grant/export consumers need a sanctioned-entity reference list for later name-match flagging. Ingest-only in this step — no grant matching yet.

**How:**
- `sanctions_processor.py`: curl download (`sdn_advanced.xml`, ~125 MB), two-pass iterparse (reference data + DistinctParty promote), idempotent `.sdn_ingest.json` marker, schema bootstrap on existing DBs.
- `download_utils.discover_ofac_sdn_url()` — Treasury advanced XML endpoint with legacy fallback.
- Models: `SanctionedEntity`, `SanctionedName`, `SanctionedIdentifier`, `SanctionedProgram` + `build_address()` for street locations.
- Production counts: `sanctioned_entities` 19,073; `sanctioned_names` 49,607; `sanctioned_identifiers` 22,203; `sanctioned_programs` 41,776.
- Data: `{final_dir}/cms_data/treasury/sdn_advanced.xml`
- Logs: `sanctions_step_20260618_v3.log` (nohup)

**Success signal:** Committed `68eff39e` (ingest) + `f198fbcd` (address hygiene). Grant-match flagging deferred.

**Address hygiene (v3):** Country-only → `FA:<iso>` (10,004 rows); skip blank/`undetermined`. Final: 27,347 `ofac_sanction` addresses, 0 blank canonicals.

---

## 2026-06: FEC Step Production Validation (2024 cycle)

**What:** Production-validated `--step fec` on `/Volumes/Data/final/irs990.duckdb` for `FEC_CYCLES=2024`. Fixed OOM (Python CSV streaming vs DuckDB `read_csv`+`ORDER BY`), MMDDYYYY + MM/DD/YYYY date parsing, row-count resume across interrupted runs, and periodic CHECKPOINT during promote.

**Why:** FEC bulk data is huge (29M individual contributions alone). DuckDB full-file sorts exhausted 5.5 GiB RAM; streaming + resume lets the step survive multi-hour runs and agent restarts without re-ingesting completed tables.

**How:**
- `fec_processor.py`: `csv.DictReader` streaming promote, `_parse_fec_date()`, `_existing_cycle_rows()` resume skip, `PROMOTE_BATCH_SIZE=15_000`, checkpoint every 10 batches.
- Model address factories (`fec_*` models) + `bulk_insert` (no raw SQL address inserts).
- Production counts (report_year=2024): committees 6,980; individual_contributions 29,104,378; committee_transactions 18,667,435; candidate_spendings 703,597; operating_expenditures 2,249,158.
- Log: `fec_step_20260617_0635.log`

**Success signal:** All 5 FEC 2024 file types promoted; committed `ba28b194`.

---

## 2026-06: Medicare Step Production Validation

**What:** Production-validated `--step medicare` on `/Volumes/Data/final/irs990.duckdb`. NPPES streaming ingest (9.6M providers) + Medicaid spending Parquet (230M rows, 229M with NPPES billing names).

**Why:** NPPES CSV has mixed types and 10GB size — DuckDB `read_csv` auto-detect fails; streaming + row-count resume matches FEC pattern. Spending Parquet (238M rows) OOMs on single INSERT…JOIN — batched by NPI prefix.

**How:**
- `medicare_processor.py`: streaming NPPES via `csv.DictReader`, `_parse_nppes_date()`, spending batched `read_parquet` + NPI prefix (100 batches).
- `download_utils.py`: NPPES zip discovery from CMS NPI_Files.html; Medicaid spending Parquet from opendata.hhs.gov.
- Production counts: `medicare_providers` 9,606,683; `medicare_provider_spending` 230,154,264 (229,001,898 with `billing_provider_name`).
- Log: `medicare_step_20260617_0942.log`

**Success signal:** `optimize_database completed after medicare` in log. Committed `187b1bc0`.

---

## 2026-06: EINless Pipeline Integration Milestone

**What:** Integrated the offline einless phonebook hygiene into the main `irs990processor.py` pipeline as step `einless` (option C). Added `einless_processor.py` (self-contained: DuckDB TSV export + phonebook/DAF resolution) and `geolocate1_processor.py` (moved after full `geolocate`). Production-validated on local `irs990.duckdb`.

**Why:** ~89% of no-EIN grantee names resolve via exact-core phonebook cream. Wiring this into the main pipeline means every rebuild gets phonebook backfills before `match`/`address_matcher`, without a separate manual hygiene pass. Raw filed data stays in `recipient_ein`; inferred matches go to `recipient_ein_backfilled` (same separation as BMF pre-backfill in `address_matcher`).

**How:**
- New step order: `… → address → einless → match → geolocate → geolocate1 → …`
- `einless_processor.export_input_tsvs()` — DuckDB COPY for all einless toolchain inputs (SQL from `docs/pipeline_overview.md` + pure-no-EIN logic).
- `einless_processor.run()` — phonebook + DAF → `recipient_ein_backfilled` + `Backfill` (`einless_phonebook` / `einless_daf` sources).
- Ran `python -u irs990processor.py --step einless --nostats` (nohup, ~15 min, completed cleanly).
- Verified: 0 mutations to raw `recipient_ein`; 468,955 names / 3.7M grants backfilled this run; hard tail ~190k names remains for match/rules integration.

**Key artifacts:**
- `einless_processor.py`, `geolocate1_processor.py`, updated `irs990processor.py`, `schema_duckdb.sql`
- Offline hygiene still in `einless/` (option A); `issue_bootstrap.md` updated for next phase
- Production log: `einless_step_20260615_1349.log`

**Success signal:** User declared milestone reached; requested project hygiene + commit checkpoint.

**Next immediate goals:**
- Continue pipeline from `--start-step match` on production DB.
- Deepen integration: einless statuses/splits into `address_matcher`, `grant_match_processor`, `generate_name_rules.py` (hooks already stubbed).
- Optional: FEC/Treasury blacklist in reworked `geolocate1`.

---

## 2026-05: University/College Family Siding — Automatic in Generator + Hygiene + Coverage Measurement

**What:** Made university/college family detection + real-EIN siding automatic inside the repeatable `generate_name_rules.py` (using the BMF data it already loads from `bmf_analysis.tsv`). Created supporting tools for the "pre-matched siding" pattern and proper coverage math that excludes unmappables. Performed major project hygiene (archiving experimentals, cleaning root, updating docs).

**Why:** Universities/colleges are a core sub-goal (they rarely file 990s themselves but receive huge grant volume and have good EIN coverage via BMF). Previously this required manual pre-steps and noisy projections. The long-term architecture is: one main repeatable generator that automatically incorporates pre-matched families (pharma today, universities now, high schools/churches later) with real or synthetic EINs, plus clean measurement that reports "mappable" coverage separately from deliberately unmapped buckets. Hygiene ensures the repo stays usable as we move to the next bucket (churches via Splink).

**How:**
- Ported/adapted the algorithmic family grouping (core name recovery + satellite stripping for FOUNDATION/ALUMNI/FACULTY/CLASSIFIED/BOOKSTORE/NROTC etc.) directly into `generate_name_rules.py` as an automatic pass after BMF loading (right after the existing pharma siding block).
- When `UNIVERSITY_FAMILIES_ENABLED=True` (default), it groups satellites under best parent canonicals, preserves real BMF EINs, marks them priority, and rolls them into the final name_rules output. No separate pre-run required once you have a fresh `bmf_analysis.tsv` DB extract.
- Created `university_family_rules.py` (standalone explorer that produces `university_families.json` in big_pharma_subsidy.json-compatible format for tuning/override) and `group_bmf_university_entities.py`.
- Created `coverage_report_with_unmappables.py` — the proper measurement tool that reports overall vs. mappable-only $/# coverage, treating sided buckets (pharma today) as a distinct "deliberately unmapped" category.
- Project hygiene: archived per-state BMF university slices, all Oregon lab files, intermediate grouped TSVs, old university/high-school miner logs & suggestions into `archive/university_experiments_2026-05/` and `archive/high_school_experiments_2026-05/`. Cleaned obvious root temp junk. Updated this file + CHANGELOG discipline.
- Verified the main generator still compiles cleanly after the integration.

**Key artifacts (kept in root as repeatable tools):**
- `university_family_rules.py`, `group_bmf_university_entities.py`, `coverage_report_with_unmappables.py`
- `university_families.json` (1,500 families, 9k+ entities, 1,455 real EINs, all with satellites)
- `university_priority_additions.txt`
- Updated `generate_name_rules.py` (automatic university family consolidation + existing pharma siding)

**Success signal:** University handling now "just happens" as part of the standard generator run off the DB extract (exactly as the user clarified). Coverage math now correctly separates unmappables. Repo is clean for the next phase (Splink on churches).

**Next immediate goals (per user):**
- Project hygiene complete (this entry + commit).
- Return to Splink for churches/temples (category bucket, seeds, targeted mining with the improved TUI, etc.).

---

## 2026-05 (Category Buckets + Targeted Splink + TUI Hardening)

**What:** Delivered repeatable category bucketing (churches + pharma) + supporting resolvers/extractors + major improvements to splink_pattern_miner.py (new --blocking-strategy selector including "none" for redaction clustering) and review_suggestions_tui.py (global normalized deduplication, smart early cleanup, safety rescan on M modify with absorption, pre-bless integration).

**Why:** The master name list is millions of rows. One giant Splink run + review is slow and noisy. Category slices (especially churches, which are ~10%+ of names and have high-value networks) let us run targeted, high-quality Splink, review with the TUI (with pre-bless seeds), and produce curated seeds for the main pipeline. The TUI dedup and modify-rescan fixes eliminated the persistent "<name> / THE <name>" and collapse failures that were blocking real progress. "None" blocking strategy unlocked perfect redaction canonicals (ATTACH variants etc.).

**How:**
- `category_splitter.py` + `church_major_resolver.py` + `extract_pharma_no_eins.py` created as repeatable tools (modeled on university_ein_resolver, using TUI normalization).
- `splink_pattern_miner.py`: added `--blocking-strategy {sig_name,redaction,loose,none}` + redaction_sig column + length-bucket fallback.
- `review_suggestions_tui.py`: global normalized-key grouping for dedup (instead of adjacent-only), early THE/geo/singular cleanup + UPPER forcing in clean_proposed_pattern, immediate full safety rescan + absorption on "M" modify.
- Output: category_buckets/{churches,pharma}.tsv + *_seeds.json; pharma_no_eins*.tsv; greatly improved suggestion quality and review ergonomics.
- All changes follow the new Ringmaster hygiene (docs/ + commits at success points).

**Success signal:** User explicitly called this a "big success point with the category and inspect stuff" and requested docs + git checkpoint so a new session can bootstrap from the docs/.

**Next immediate goals:**
- Curate major_churches.json and pharma suggestions.
- Continue Splink dives on the new buckets with the improved TUI.
- Keep the docs/ discipline (this file + per-script .md files) and commit at logical points.

---

## 2026-05: Pharma Bucket Completion (Redaction Patterns + High-Value No-EIN Review)

**What:** Completed the dedicated pharma redaction/subsidy bucket work. Significantly hardened and compressed `big_pharma_subsidy.json` (from bloated literal list to ~40 compact, regex-aware patterns). Added strong anchors including "UPON REQUEST", "AVAILABLE UPON REQUEST", "GRANTS? (PAID|APPROVED)", "20\\d\\d GRANTS", "SEE (DETAIL|LIST)", etc. Ran multiple targeted Splink experiments on the high-value pure no-EIN slice (`pure_no_ein_high_value_1M.tsv` and derivatives) using different blocking strategies (`sig_name`, `none`). Used the improved TUI (with `--redaction-patterns` + bucket-isolated `approved_pure_no_ein_high_value_*.json` outputs) to curate. Moved generic redaction phrases ("VARIOUS ... ORGANIZATIONS", "VOLUNTEERS", year-grants variants, etc.) into the subsidy patterns. Added "STICHTING" as a simple canonical in `priority_canonicals.json`.

**Why:** Pharma foundations produce massive volumes of high-dollar, no-EIN "patient assistance / privacy" redaction rows. These must be reliably caught and rolled up under the synthetic BIG PHARMA SUBSIDY (99-7777777) so they don't pollute real grantee graphs or name rules. The previous pattern list was too verbose and still leaking variants. Completing this bucket gives a much stronger, maintainable redaction list and demonstrates the full "category slice → targeted Splink → TUI review with redaction filter → curated patterns" workflow.

**How:**
- Iterative refinement of `big_pharma_subsidy.json` patterns (compact regex forms that work in the generator + good substring anchors for extractor/TUI pre-filters).
- Created and iterated on high-value pure no-EIN extracts (`pure_no_ein_high_value_1M.tsv`, `_minus_known_redaction.tsv`, 10M/100M slices).
- Multiple `splink_pattern_miner.py` runs (`--blocking-strategy sig_name` and `none`) + review with `review_suggestions_tui.py` (using `--redaction-patterns big_pharma_subsidy.json` and bucket-specific output files).
- Added "STICHTING" to simple canonicals.
- Consistent hygiene: updated this file, CHANGELOG.md, and related docs at the success point.

**Key artifacts:**
- Hardened `big_pharma_subsidy.json` (42 patterns as of completion).
- `priority_canonicals.json` now includes "STICHTING".
- Bucket-isolated approved files: `approved_pure_no_ein_high_value_sig_name.json`, `approved_pure_no_ein_high_value_none.json`.
- Fresh suggestions files from the final runs.
- `uncovered_redaction_candidates.txt` (temporary diagnostic, deleted at hygiene checkpoint).

**Success signal:** User explicitly declared "we've successfully completed the pharma bucket!"

**Hygiene performed:** This entry + corresponding CHANGELOG update + clean commit at the logical completion point.

---

## Education Categories (Universities / Colleges / High Schools / School Districts)

**Status (as of this entry):** User is actively considering running the pattern miner + TUI workflow on education-related names, parallel to the church/pharma success.

**Key realization:** The original `university_ein_resolver.py` worked because universities frequently have usable `recipient_ein` values in the full `distinct_grantee_names.tsv` (populated via BMF joins). This is why we could do proper EIN-level canonicalization for them. The clean TSV drops the EIN column, which is fine for name-only flows (churches, most K-12) but loses that power for well-covered entities.

**Parallel work underway while decision is mulled:**
- Documentation gaps closed in `splink_pattern_miner.py` (clear explanation of clean vs full TSV + when to use each).
- `docs/pipeline_overview.md` updated with accurate column differences and the university/EIN vs name-only distinction.
- Data sampling performed (in first 200k of full file: ~17k education-ish rows, ~56% with non-null EIN; strong university coverage, much weaker on school districts).

**Open questions the user is considering:**
- Granularity: one "education" bucket, or separate `universities_colleges`, `high_schools`, `school_districts`?
- For higher-ed: lean on EIN-based resolver (like original university work) or treat more like churches (name-only + normalization + targeted Splink)?
- How to handle the very repetitive "XXX School District", "XXX ISD", "XXX Unified" patterns (new blocking strategies should help here).

**Parallel work completed (while mulling):**
- Scaffolding added to `category_splitter.py` (keywords for universities/colleges, high schools, school districts + broad "education" handler + seeds + argparse/docs updates).
- New `docs/education_name_patterns.md` created with volume numbers, real naming pattern examples, data observations, and next-step recommendations.
- `docs/pipeline_overview.md` and `splink_pattern_miner.py` documentation improved around clean vs full TSV and the university/EIN vs name-only distinction.
- Targeted data samples pulled (school districts ~18k rows, colleges ~35k, high schools ~33k in clean file; strong repetitive patterns visible).

**2026-05-25: Ran the splitter on education categories**
- Executed: `python category_splitter.py --categories school_districts,high_schools,universities,colleges`
- Results (after improved detectors + explicit foundation noise drop for school_districts):
  - `school_districts.tsv`: 18,000 rows
  - `high_schools.tsv`: 30,912 rows
  - `universities.tsv`: 722 rows
  - `colleges.tsv`: 28,689 rows
- Corresponding `_seeds.json` files generated with data-driven examples.
- Files written to `category_buckets/`.

Note: First run had a bug where "universities" and "colleges" used the same detection function (identical counts). Re-ran after splitting `is_university_name` / `is_college_name` properly. This aligns with the hybrid system's goal of generating more, higher-quality seeds rather than over-collapsing.

**Handoff artifacts created for sleep:**
- `quick_tuned.sh` — rewritten in zsh; launches miners for all current buckets with tuned per-bucket `--blocking-strategy` values (uses nohup).
- `session_bootstrap.md` — file a new/fresh session should read to get context quickly (current state, what was just done, where things live, references to Obsidian Ringmaster prompt).

**2026-05-25: Blocking strategy tuning**
Quick sampled tests were run on the new education buckets + legacy ones to choose per-bucket `--blocking-strategy`.

Findings & recommendations:
- universities: `none` (excellent, clean high-metric clusters — user was right)
- high_schools: `none` (very good on the large religious/private names)
- school_districts: `sig_name` (default). "none"/"loose" frequently surfaced the literal suffix "SCHOOL DISTRICT" as a high-metric suggestion.
- colleges: `sig_name`
- churches: `sig_name`
- pharma: `none` (confirmed again)

Created `quick_tuned.sh` that launches all current buckets with the above strategies baked in.

Also created `session_bootstrap.md` as a dedicated handoff document for new/fresh sessions.

(Reference: coding-bootstrap.md + the 2026-05 category success arc.)

**End of session (2026-05-25 evening)**
User is going to sleep. `quick_tuned.sh` is ready to launch the miners with per-bucket blocking strategies (universities + high_schools on `none`, school_districts on default, etc.).

Next session (fresh) should:
1. Read `session_bootstrap.md`
2. Read the latest section of this file
3. Run `./quick_tuned.sh` if the miners haven't finished
4. Review suggestions with the TUI using the matching `_seeds.json` files

Good handoff in place.

---

## 2026-05: High Schools Bucket Completion (Iterative Mining + TUI Grinding)

**What:** Completed the high schools category bucket. Used an iterative "chip away" workflow (custom `chip_high_schools.sh` + supporting Python helpers) to repeatedly:
- Prefilter the master high schools slice against the growing approved list.
- Run targeted Splink (mostly `--blocking-strategy none` on the shrinking remaining TSV).
- Review with `review_suggestions_tui.py` using `--auto-approve "HIGH SCHOOL"`, `--exclude-approved`, and the seeds for pre-blessing.
- Merge newly approved items back into the master seeds list.

Reached a final curated set of 1259 high school canonicals (including a manual addition of "YESHIVA" as a high school at the end).

**Why:** High schools are a high-volume, high-repetition category with strong repetitive naming patterns ("XXX HIGH SCHOOL", "XXX ISD", religious/private powerhouses, etc.). A single-pass miner + review leaves too much on the table. The grinding approach (repeated prefilter + mine 1000 + auto + manual review on the shrinking unseen slice) allowed systematic coverage until the miner was only surfacing a handful of edge-case / non-literal patterns. This established a reusable pattern and tooling for future buckets.

**How:**
- Created `chip_high_schools.sh` (shell orchestrator) + `high_school_chip.py` for the full cycle: prefilter → launch miner (foreground or background) → auto-launch TUI with correct flags → post-review merge into seeds + next prefilter.
- Heavy use of `--auto-approve "HIGH SCHOOL"` + the seeds list for pre-blessing during reviews.
- Maintained `approved_high_schools_full.json` (cumulative) alongside the TUI's session-only `approved_high_schools.json`.
- Final seeds file: `category_buckets/high_schools_seeds.json` at 1259 entries.
- Many iterative passes on successively smaller `high_schools_remaining_v*.tsv` files until only low-value / sticky patterns remained.

**Key artifacts:**
- Final `category_buckets/high_schools_seeds.json` (1259 entries).
- `approved_high_schools_full.json` (1259 entries) — the authoritative cumulative list.
- `chip_high_schools.sh` and helpers (reusable pattern for other buckets).
- Many historical `high_schools_remaining_v*.tsv` and `suggestions_high_schools_pass*.json` (cleaned at hygiene checkpoint).

**Success signal:** User explicitly declared the high schools bucket complete ("we've found 1258... approved YESHIVA... 1259, so we're done with that bucket").

**Hygiene performed:** Added this entry. Synced full approved list. Considered cleanup of historical remaining/pass files. Transition to next bucket + general `chip_bucket.sh` tooling.

**Next:** Move to universities or colleges (or other pending buckets). Generalize the chip script into a reusable `chip_bucket.sh <bucket>` for future work.
