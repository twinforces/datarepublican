# 990tools Issue Bootstrap (Minimal Restart)

**Purpose**: Fast, low-token restart for a fresh session after CLI crashes (127/137 kills) or context resets.

**Last major work**: June 2026 — **FEC + Medicare steps** wired after `xml`; FEC 2024 production-validated (51.7M rows). Medicare test pending/in progress.

## Current State (Pipeline Integration Complete)

### Offline hygiene (June 2026, option A in `einless/`)
- Rebuild cream: **88.87%** on `pure_no_ein_by_dollars.tsv` (1,186,989 cream / 148,726 non-cream).
- Rebucket hard tail: 125,404 hard rows classified (foreign, plausible, flow, etc.).
- Artifacts: `einless/{data,docs,code}/`, `einless_traditional_progress_report.md`.

### Production pipeline run (2026-06-15)
- Command: `python -u irs990processor.py --step einless --nostats`
- **468,955** distinct names resolved → **3,697,976** grant rows backfilled ($110B).
- **190,141** names / **1.7M** grants still unresolved (hard tail for match/rules).
- Raw `recipient_ein` untouched; backfills in `recipient_ein_backfilled`.
- Log: `einless_step_20260615_1349.log`

**User verdict**: Milestone reached — 89% phonebook cream integrated into production pipeline. Hygiene checkpoint committed.

## Pipeline Step Order

```
irsfetch → zip → bmf → xml → fec → medicare → address → einless → match → geolocate → geolocate1 → photos → grant_match → backfill → ratios → percentiles → export
```

**Resume from here:** `--start-step medicare` (FEC 2024 complete) or `--start-step match` after medicare/address/einless

## Core Philosophy
- Phonebook/cream (exact sig-core after guards + cleaning) is the hero (~89%).
- Post-phonebook tail is structural (foreign, for-profit, pass-through flows) — not missed BMF variants.
- Raw `recipient_ein` = filed data; `recipient_ein_backfilled` = inferred. Never mix them.
- Offline `einless/` hygiene remains for hard-tail classification, Grok diagnostics, pattern mining.

## Critical Artifacts
1. `einless_processor.py` — production einless step (export TSVs + phonebook + DAF).
2. `docs/einless_grantee_resolution_architecture.md` — architecture, data model, integration plan.
3. `docs/pipeline_overview.md` — step order, SQL exports, data separation.
4. `einless/` — offline hygiene artifacts (option A tie-off).
5. `bmf_fuzzy_candidate_matcher.py` — shared phonebook/DAF/plausible logic.

Integration hooks (stubbed, next phase) in:
- `address_matcher.py`, `grant_match_processor.py`, `generate_name_rules.py`

## Known Ops Problems
- CLI kills with 127/137 (OOM). Use `nohup` for long steps (`einless`, `match`, `geolocate`).
- Default logging is WARNING — add `-v` for progress lines.
- Heavy steps: einless name scan (~15 min), match (~hours), geolocate.

## Immediate Next Work
1. Run `--start-step match` on production DB.
2. Wire einless hard-tail statuses/splits into `address_matcher`, `grant_match_processor`, `generate_name_rules.py`.
3. Optional: FEC/Treasury blacklist in `geolocate1_processor.py`.