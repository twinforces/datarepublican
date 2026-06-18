# 990tools Issue Bootstrap (Minimal Restart)

**Purpose**: Fast, low-token restart for a fresh session after CLI crashes (127/137 kills) or context resets.

**Last major work**: June 2026 — **DOT step** implemented (`d43a902b`), production run in progress. **Sanctions** complete (`68eff39e`, `f198fbcd`): 19,073 entities, 27,347 addresses.

## Current State

### Sanctions (production-validated 2026-06-18)
- `sanctioned_entities` 19,073; `sanctioned_names` 49,607; `ofac_sanction` addresses 27,347
- Country-only → `FA:<iso>`; blank/`undetermined` skipped
- Data: `/Volumes/Data/final/cms_data/treasury/sdn_advanced.xml`
- Log: `sanctions_step_20260618_v3.log`

### Prior milestones
- **FEC 2024**: 51.7M rows (`ba28b194`)
- **Medicare**: 9.6M NPPES + 230M spending (`187b1bc0`)
- **Einless**: 468,955 names → 3.7M grant backfills (`einless_step_20260615_1349.log`)

## Pipeline Step Order

```
irsfetch → zip → bmf → xml → fec → medicare → sanctions → dot → address → einless → match → geolocate → geolocate1 → photos → grant_match → backfill → ratios → percentiles → export
```

**Resume from here:** `--start-step dot` (sanctions complete) or `--start-step address` after dot

## DOT Step (production run in progress)

**Source:** FMCSA Company Census File — `https://data.transportation.gov/api/views/az4n-8mr2/rows.csv?accessType=DOWNLOAD` (~1M carriers; CSV ~900 MB on disk).

**Target tables:** `dot_carriers`, `Addresses` (`dot_carrier_phy`, `dot_carrier_mail`).

**Implementation:** `dot_processor.py`, `models/dot_carrier.py` — committed `d43a902b`.

**Log:** `dot_step_20260618.log` (nohup). Idempotent marker: `{final_dir}/cms_data/dot/.dot_census_ingest.json`.

**Consumer (later):** `grant_match_processor`, `geolocate1_processor` — not in ingest step.

## Core Philosophy
- Raw `recipient_ein` = filed data; `recipient_ein_backfilled` = inferred. Never mix them.
- External reference ingests (FEC, Medicare, sanctions, DOT) are ingest-only; matching deferred.
- Use `nohup` for long steps (`dot`, `einless`, `match`, `geolocate`).

## Known Ops Problems
- CLI kills with 127/137 (OOM). Use `nohup` + log redirection.
- Default logging is WARNING — add `-v` for progress lines.

## Immediate Next Work
1. Wait for DOT production run to finish; verify `dot_carriers` + `dot_carrier_%` address counts.
2. Run `--start-step address` on production DB.
3. Wire colocation consumers: sanctions names, DOT carriers, FEC at shared `canonical_address`.