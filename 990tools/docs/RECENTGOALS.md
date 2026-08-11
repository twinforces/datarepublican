# RECENTGOALS.md — 990tools

Active scratchpad (What / Why / How + hashes). Older detail lives in CHANGELOG.md.

---

## 2026-08-11: Grok pipeline closeout + grant_match (in flight)

**What:** Census/API/Grok geocode path closed for this cycle; pattern free-wins + smarter Grok prompt; **grant_match** re-run to fan new lat/lon into grants.

**Ops (prod `/Volumes/Data/final/irs990.duckdb`):**
1. API tail → `pending_api=0`; grant_match + OFAC once already (morning).
2. Pattern preprocess + pack: free PatternOwners before/after Grok.
3. `geolocate_grok` first pass ~39k @ **81.6%** match.
4. Prompt fix (no tool-refusal UNKN) → re-run **33,705** prior UNKN → **93.0%** match (**31,356**); residual UNKN **2,229** (mostly foreign).
5. **Next now:** `grant_match` to pick up new `Match:Grok-4` coords.

**Hashes (`grokrefactor3`):**  
`deef07d1` grant_match lat/lon · `aa252eb2` preprocess · `a8af7238` overnight · `e8c22f94` docs · `07f8513e` zips · `c5a72fd7` hygiene · `869a428d` pattern pack · `d85388cd` hash note · **`587f82ea` Grok prompt** · (this hygiene commit).

**Decisions:**
- `GEOCODING_GROK_MIN_ADDRESS_COUNT` default **10** = cost filter; full residual set **`=0`**.
- Complete US streets: Grok must geocode or VAGUE/AMBIG — never UNKN for missing tools.
- Live name rules = **`name_rules.json.gz` only** (ignore v19).

**Deferred:** owner colocator backfill / `geolocate_archive` bookend polish; optional OFAC regen after grant_match.

---

## 2026-08-10: Overnight census drain (DONE)

**What:** Chunked Census → `pending=0` on 16GB host.  
**Hashes:** `adb7b587`…`a846c269`. Doc: `docs/overnight_census_chunked.md`.

---

## 2026-08-01: Report suite (landed)

**What:** Focus admission, Medicare/FEC ranking, contractor `owner_id`. See CHANGELOG.
