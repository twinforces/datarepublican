# 990tools Issue Bootstrap (Minimal Restart)

**Purpose**: Fast, low-token restart for a fresh session after CLI crashes (127/137 kills) or context resets.

**Last major work**: June 2026 — **Address step** complete (`7ea4a2a1`). 95.2M addresses, 0 NULL `master_id`. Prior: DOT + sanctions ingests.

## Current State

### Address (production-validated 2026-06-20)
- 95,196,749 addresses; **0** NULL `master_id` (69.3M incremental merge)
- 14,342,486 Geocoding canonical groups; all 9 indexes rebuilt
- DB: `/Volumes/Data/final/irs990.duckdb` (~91 GB)
- Logs: `address_step_20260620_v2.log`, `address_step_20260620_v2_indexes.log`

### Sanctions (production-validated 2026-06-18)
- `sanctioned_entities` 19,073; `sanctioned_names` 49,607; `ofac_sanction` addresses 27,347
- Country-only → `FA:<iso>`; blank/`undetermined` skipped
- Data: `/Volumes/Data/final/cms_data/treasury/sdn_advanced.xml`
- Log: `sanctions_step_20260618_v3.log`

### DOT (production-validated 2026-06-20)
- `dot_carriers` 4,454,157; `dot_carrier_phy` 4,450,675; `dot_carrier_mail` 977,160
- Data: `/Volumes/Data/final/cms_data/dot/company_census.csv` (~1.6 GB, 4.45M rows)
- Log: `dot_step_20260620_v5.log`; marker: `.dot_census_ingest.json`

### Prior milestones
- **FEC 2024**: 51.7M rows (`ba28b194`)
- **Medicare**: 9.6M NPPES + 230M spending (`187b1bc0`)
- **Einless**: 468,955 names → 3.7M grant backfills (`einless_step_20260615_1349.log`)

## Pipeline Step Order

```
irsfetch → zip → bmf → xml → fec → medicare → sanctions → dot → address → einless → match → geolocate_prev → geolocate_new → geolocate_archive → photos → grant_match → backfill → ratios → percentiles → export
```

**Planned rename** (not wired yet): current `geolocate1` → `geolocate_prev` (archive cache), current `geolocate` → `geolocate_new` (Census API), new `geolocate_archive` (export successful geocodes).

**Resume from here:** `--start-step match` (address complete; skip `einless` — already run 2026-06-15)

## DOT Step (complete)

**Source:** FMCSA Company Census — 4,454,157 carriers. Ingest marker at `{final_dir}/cms_data/dot/.dot_census_ingest.json`.

**Consumer (later):** `grant_match_processor`, `geolocate1_processor` — same-building colocation at shared `canonical_address`.

## Core Philosophy
- Raw `recipient_ein` = filed data; `recipient_ein_backfilled` = inferred. Never mix them.
- External reference ingests (FEC, Medicare, sanctions, DOT) are ingest-only; matching deferred.
- Use `nohup` for long steps (`dot`, `einless`, `match`, `geolocate`).

## Known Ops Problems
- CLI kills with 127/137 (OOM). Use `nohup` + log redirection.
- Default logging is WARNING — add `-v` for progress lines.

## Immediate Next Work
1. Run `--start-step match` on production DB (watch how einless backfills interact with name rules).
2. Refactor geolocate into trilogy (`geolocate_prev` / `geolocate_new` / `geolocate_archive`).
3. Update stats after match (`python stats_command.py` or drop `--nostats`).
4. Fraud research: shared-canonical queries in stats report; deeper colocation after geolocate trilogy.
5. Standalone DOT stacking script (many carriers / one address) — build alongside, not a pipeline step.