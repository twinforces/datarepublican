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

(Reference: coding-bootstrap.md in Obsidian for the full Ringmaster process that produced this entry.)
