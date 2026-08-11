# preprocess_grok_pending.py

## Why

Rows reach **`grok_pending`** after Census + API stages fail. Over time, preprocess short-circuits (FA foreign, military APO, vendor city-only, PARTIAL junk, privacy placeholders, etc.) improved, but **already-parked** `grok_pending` rows do not automatically re-enter preprocess.

Re-running preprocess **before** `geolocate_grok` converts structural non-geocodeables into `Match:PatternOwners` (or related statuses) at **zero** model cost. Remaining rows are mostly full US street addresses — the set worth paying Grok for.

## What it does

- Scans `Geocoding` where `geocoding_status = 'grok_pending'`
- Runs the same `_preprocess_handler` / `apply_preprocess_batch` path as the live pipeline
- Matched → `Match:PatternOwners` (+ colocator on Geocoding/Addresses as the handler defines)
- Unmatched stay `grok_pending`

Does **not** call Photon, maps.co, OpenCage, or Grok.

## How to run

```bash
# Prefer public Photon env for any accidental probe in GeocodingAPIProcessor init
env -u PHOTON_DOMAIN -u PHOTON_SCHEME \
  DUCKDB_MEMORY_LIMIT=12GB \
  python3 -u preprocess_grok_pending.py \
    --db-path /Volumes/Data/final/irs990.duckdb \
    --final-dir /Volumes/Data/final \
    --batch-size 400
```

| Flag | Notes |
|------|--------|
| `--batch-size` | Prefer **≤400** on 16GB; larger batches OOM’d mid-save (~5.5–11 GiB) |
| `--max-rows` | Cap for smoke tests |
| `--dry-run` | Count only |

Mirror of `preprocess_geocode_tail.py` (same idea for `geocode_tail`).

## Rule fix bundled with this work

Highway / county-road / RR short-circuits must **not** fire when the street has a house number. Otherwise real addresses like `2418 E HWY 66 SUITE 110` become `PARTIAL` and never reach a real geocoder or Grok.

## Production result (2026-08-11)

| Metric | Value |
|--------|------:|
| Scanned | 48,661 |
| Matched free | **9,624** |
| Remaining `grok_pending` | **39,037** |
| Residual character | ~full US street+city+state+zip |

Export for offline mining: `/Volumes/Data/final/grok_pending_for_patterns.tsv`.

## Next

`geolocate_grok` (batch API) on the residual, optionally with `GEOCODING_GROK_EXPORT_ROWS` / cost caps from `constants.py`.
