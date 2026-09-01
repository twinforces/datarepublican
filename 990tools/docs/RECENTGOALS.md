# RECENTGOALS.md — 990tools

Active scratchpad (What / Why / How + hashes). Older detail lives in CHANGELOG.md.

---

## 2026-09-01: Browse export / Sankey rebuild (pinned)

**What:** $10M default band, focus-default Sankey, inferred-grant flag, leftover stubs. Instructions: `docs/PINS.md`.

**Why:** Full-NGO download is ~3 min; spiderweb is unreadable; 990-PF “98% match” was mostly GINs in `recipient_ein`.

**Later (not PR1):** Charity name-history GIN → phonebook (`PINS.md` § Later). Would catch Gates 2024 rename.

---

## 2026-08-19: DOT records·types live on grumpytechbro.com

**What:** DOT suites rank physical carrier count, then address types. Active PUs stay a column.

**Why:** PU rank was fleet size (FedEx yards), not identity farms.

**How:** National 8/17; by-state 8/18 `failed=0`; local `fun/` then `deploy.sh --with-fun` 8/19 (~7 min, 571 MB / 16 GB already on box). Live `/fun/address_clusters/` shows Signal Hill `699 · 2 types`.

**Failure:** `build_site.py` `copytree` skipped existing dirs; then `rsync --update` skipped national HTML because injected nav made dest newer than source. Fix in site repo: suite stamp vs source `index.html` mtime.

---

## 2026-08-17: HuggingFace dump script (Hub refresh unverified)

**What:** `upload_to_hf.py` discovers live DuckDB tables (was hard-coded 18 + `.address` DB).

**Why:** May dump is 990+FEC only; live has Medicare, DOT, OFAC, BMF streets, name maps.

**How:** Skip ops/raw; UUID→VARCHAR; shard ≥2M-row tables. **Doc:** `docs/upload_to_hf.md`. Did not confirm Hub contents this pass.

---

## 2026-08-16: Public /fun/ matrix (landed)

**What:** Undated DOT / Medicare / contractor / grants / grants_out / USG × 4 slices. Bulk FEC off. Extra: `fec_committee_colocator_clusters`.

**Out:** Employer / intra-committee FEC still a different product.

---

## 2026-08-28: No dead links on /fun/

**What:** Every Medicare table NPI has a dossier; Widen never 404s (address → colocator → loose → ZIP, index fallback).

**Why:** By-state lists linked ~90k NPIs; only ~17k pages existed. Contractor/grants Widen pointed at missing top-N colocator files.

**How:** Fast provider backfill (skip 230M spend grain). `widen_links.py --reports-dir reports` then `build_site.py` → `grumpytechbro.com/fun/`. Deploy when ready, not urgent.

---

## Parked

- HF full parquet refresh with `HF_TOKEN` (script updated; Hub contents not checked).
- Owner colocator backfill / `geolocate_archive` polish.

**Not parked:** `grant_match`. Morning 8/11 already matched **145,396**. Post-Grok re-run 13:09: GIN floaters **0**, parallel feeder **0** (`recipient_ein IS NULL AND loose_colocator IS NOT NULL`). Empty work was rc=1 until `c133142f` (`process_parallel` returns 0). Further grant_match is a no-op on the hail-mary tier.
