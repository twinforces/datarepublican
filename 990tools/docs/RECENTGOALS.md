# RECENTGOALS.md — 990tools

Active scratchpad (What / Why / How + hashes). Older detail lives in CHANGELOG.md.

---

## 2026-09-02: $10M browse export (PR1)

**What:** DuckDB → `/browse` chunks at $10M. 74,151 Charities + 477 BMF-only + 1,402 ghosts + 28,390 leftover stubs; 555k edges. Zip **12.6 MB** (was ~39 MB full). Gates Trust → GIN `$42.6B` inferred, suggested `562618866`.

**Why:** Default load must be the $10M band; GINs are ghosts not fake EINs.

**How:** `export_browse_band.py`. Loader accepts GIN / `etc`+EIN keys and extra grant columns. `docs/export_browse_band.md`. Zip 12.6 MB in `browse/tsv_chunks` + `docs/browse/tsv_chunks`. Hash `ab4feeca`.

**Next:** PR2 focus-default Sankey + distinct ghost rendering.

---

## 2026-09-01: Repair phonebook before $10M export

**What:** Cream/DAF no longer first-writer-wins or default-DAF. Legal suffixes stay in the key. Digit-repair only for EINs missing from BMF. Script `repair_phonebook_backfill.py` fixes existing GIN backfill (does not touch `recipient_ein`).

**Why:** $100M thumbs showed the phonebook, not the graph, was the bad data.

**How:** `test_phonebook_guards.py`; dry-run then `--apply`. Docs: `docs/repair_phonebook_backfill.md`, `docs/gin_phonebook_100m_ai.md`.

**Next:** Apply repair, then PR1 $10M export.

---

## 2026-09-01: $100M ghost→EIN thumbs (done)

**What:** 663 pairs ≥ $100M judged yes/no. Result: **449 yes ($187B) / 214 no ($65B)**. Table: `gin_phonebook_100m_votes.json`. Gates 2024 `BILL & MELINDA GATES FOUNDATION` → `562618866` is yes.

**Why:** Default-collapse only the expensive ghosts we trust. Phonebook still suggests junk EINs (JHU duplicate, Fidelity INC copycat, Harvard/MIT EINs not in BMF).

**How:** Rule exact-name; AI on 263 remainder; 11 parent overrides for name collisions. Docs: `docs/gin_phonebook_100m_ai.md`. Did **not** write votes into DuckDB.

**Next:** PR1 $10M DuckDB → browse TSV (`PINS.md`).

---

## 2026-09-01: Browse export / Sankey rebuild (pinned)

**What:** $10M default band, focus-default Sankey, inferred-grant flag, leftover stubs. Instructions: `docs/PINS.md`.

**Why:** Full-NGO download is ~3 min; spiderweb is unreadable; 990-PF “98% match” was mostly GINs in `recipient_ein`.

**Done before PR1:** Charity name-history GIN phonebook (`7620b956`) then $100M thumbs (this file).

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
