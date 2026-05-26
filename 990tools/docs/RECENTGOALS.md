# RECENTGOALS.md — 990tools

This is the active scratchpad for current and recently completed work. Entries in What / Why / How + git hash format. Migrate older entries to CHANGELOG.md periodically.

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
