# 990tools Issue Bootstrap (Minimal Restart)

**Purpose**: Fast, low-token restart for a fresh session after CLI crashes (127/137 kills) or context resets.

**Last major work**: June 2026 — **Match step** kicked off. Prior: address dedup (`7ea4a2a1`), geolocate trilogy wired (`7d9fe8ab`), stats/fraud precursors (`60fdc659`).

## Current State

### Address cluster reports (ready to generate)
- Script: `generate_address_reports.py` (spec: `address_cluster_report.md`)
- Physical notes: `physical_notes.json` (OKC + top shells seeded)
- Run when DB unlocked: `python generate_address_reports.py --min-dot-carriers 50 --max-clusters 50`
- Addresses render as **Google Maps** links in HTML output

### Match (in progress)
- **Resume command used:** `--start-step match --stop-step match` (skip `einless`)
- **Log:** `match_step_20260620.log`
- **Watch for:** einless backfill (`recipient_ein_backfilled`) interaction with name rules / `address_matcher`
- **After match:** `geolocate_prev` → `geolocate_new` → `geolocate_archive` → `photos` → `grant_match`

### Address (production-validated 2026-06-20)
- 95,196,749 addresses; **0** NULL `master_id`
- 14,342,486 Geocoding canonical groups; **8/9** indexes (covering index OOM'd — non-blocking)
- DB: `/Volumes/Data/final/irs990.duckdb` (~91 GB)
- Logs: `address_step_20260620_v2.log`, `address_step_20260620_v2_indexes.log`

### Stats / fraud precursors (2026-06-20)
- Report: `stats_after_address.md` — generate via `python stats_command.py after_<step>`
- **1,345,109** multi-type canonical addresses; DOT stacking + shell-office hubs documented
- **Future report UX:** render canonical addresses as clickable Google Maps links (`https://www.google.com/maps/search/?api=1&query=<encoded address>`)

### External ingests (complete)
- **FEC 2024**: 51.7M rows (`ba28b194`)
- **Medicare**: 9.6M NPPES + 230M spending (`187b1bc0`)
- **Sanctions**: 19,073 entities, 27,347 `ofac_sanction` addresses
- **DOT**: 4,454,157 carriers (`dot_step_20260620_v5.log`)
- **Einless**: 468,955 Backfill rows — **do not re-run** unless re-hygiene (`einless_step_20260615_1349.log`)

## Pipeline Step Order

```
irsfetch → zip → bmf → xml → fec → medicare → sanctions → dot → address → einless → match → geolocate_prev → geolocate_new → geolocate_archive → photos → grant_match → backfill → ratios → percentiles → export
```

**Geolocate trilogy** (`7d9fe8ab`): wired in `irs990processor.py`. Legacy aliases: `geolocate` → `geolocate_new`, `geolocate1` → `geolocate_prev`.

## Fraud research arc

| Phase | What |
|-------|------|
| Done | Shared `canonical_address` colocation in stats report |
| After match | EIN/name rules + charity↔contractor, grants↔sanctions name joins |
| After geolocate trilogy | `colocator` + `loose_colocator` → `grant_match` hail-mary |
| Later | Standalone DOT stacking script; USG-reportable case reports (Maps links) |

## Core Philosophy
- Raw `recipient_ein` = filed data; `recipient_ein_backfilled` = inferred. Never mix them.
- External reference ingests are ingest-only until match/geolocate/grant_match consumers run.
- Use `nohup` for long steps (`match`, `geolocate_*`, `grant_match`).

## Known Ops Problems
- CLI kills with 127/137 (OOM). Use `nohup` + log redirection.
- Default logging is WARNING — add `-v` for progress lines.

## Immediate Next Work (after match completes)
1. `python stats_command.py after_match`
2. `--start-step geolocate_prev` through `geolocate_archive`
3. `grant_match` — colocator / loose_colocator matching
4. DOT fraud standalone script; fraud report template with Google Maps address links