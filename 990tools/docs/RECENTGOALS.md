# RECENTGOALS.md — 990tools

This is the active scratchpad for current and recently completed work. Entries in What / Why / How + git hash format. Migrate older entries to CHANGELOG.md periodically.

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
