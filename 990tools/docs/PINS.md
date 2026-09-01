# PINS — Browse export, Sankey, inferred grants

Pinned decisions for the DuckDB → `/browse` rebuild on `grokrefactor3`. Implementer starts at PR1. Do not treat GINs as IRS matches.

Live DB: `/Volumes/Data/final/irs990.duckdb` (via `990tools/irs990.duckdb`).

---

## Columns (do not confuse)

| Column | What it is |
|---|---|
| `Grants.recipient_ein` | Graph **key**. XML 9-digit if the 990 had one; else colocator-copied 9-digit; else **GIN** (`70` + hex SHA256 of cleaned grantee name, length 66 or 130). Match **does** write this (`grant_match_processor.py`). |
| `Grants.recipient_ein_backfilled` | IRS **guess**. Phonebook / BMF / einless. Never a GIN. |
| Effective EIN for drawing | 9-digit `recipient_ein` if present, else 9-digit `recipient_ein_backfilled`. **Never export a GIN as an EIN.** |

**GIN purpose:** 990-PF (and other floaters) have no EIN and often no colocator. Hash the name so the same string is one node. It is not a match.

**Hygiene:** empty XML EIN is recoverable without reingest: GIN / `8686` / non-9-digit in `recipient_ein` means the form did not give an EIN.

**Reported vs inferred (export flag):**

- **Reported:** filer is 990/990EZ **and** `recipient_ein` is `^[0-9]{9}$`.
- **Inferred:** 990-PF filer, **or** `recipient_ein` is a GIN, **or** XML empty and backfill is 9-digit.
- **Unmatched:** no 9-digit in either column → leftover stub, not a fake charity.

Live snapshot (26.2M grants): ~40% have a 9-digit in `recipient_ein`; ~58% have a GIN there; ~96% have a 9-digit in *either* column (the extra is phonebook sitting next to the GIN). Quote **40%** as form-side; quote **96%** only as “we have a candidate EIN to draw.”

**Gates check:** Trust `911663695` → Foundation `562618866`. 2020–23 grantee `BILL & MELINDA GATES FOUNDATION`, GIN + backfill `562618866`. 2024 string `GATES FOUNDATION` (divorce rename), GIN only, backfill NULL. Duplicate grant rows exist (every amount twice) — separate hygiene.

---

## Who is in a dollar band

Org is in the **$T set** if **max across years** of any of (receipts, govt, contribs, assets, grants-to-others) **or a single grant** ≥ T.

Why max-across-years: catch a Biden-era $100M year even if the latest filing is quiet.

**Nodes in the TSV:** latest tax year only (name, assets, receipts, that 990).

**Edges:** all years between in-set orgs. A 2025 grant from A to B is allowed even if B’s card is 2024. Opening B’s 990 will not show that inflow. Accepted.

**Leftover:** one stub **per visible org** for grants to omitted counterparties (“see more”; display TBD). Not one global `.etc.` node.

Cutpoints (max-across-years, Charities only — grant-size union not folded in yet):

| T | Orgs |
|---|---:|
| $1M | 267k |
| **$10M (default load)** | **74k** |
| $100M | 14k |

Current full browse payload ~39MB zip / ~910k orgs; $10M should be ~10× fewer orgs.

---

## Browse behavior

1. **Default load = $10M band.** Fast path.
2. **Focus is the default interaction.** Plain click / cmd-click / Only This = `tunnelNode()` (this org + its up/down only). Preset **add** stays as a power switch so Uniparty can still seed several nodes. Dead code: `Charity.tunnelNode`, panel button `focusNode`.
3. **Jump to $1M** is user-initiated. Warn: you will wait. Then merge into the same IndexedDB so a return visit is already at that depth.
4. **Show inferred grants** toggle: hide/show inferred-class edges. Default **on** (so Trust→Foundation still draws when backfill exists) unless product says otherwise.
5. Do not ship the full 910k set as default.

---

## PR order

1. DuckDB → browse TSV exporter at **$10M**: orgs, inter-org grants, per-org leftover stub, `inferred` flag. Measure zip size / load time. No GINs in the TSV.
2. Browse: load that band; **focus default**; keep add as a switch.
3. “Load $1M” with explicit wait; IDB merge.
4. Inferred-grants toggle.
5. Org inspector: Google Maps from stored lat/lon; later **octopus** (Leaflet, same stack as OFAC/noblogs reports — tentacles = grants to geolocated NGOs).

Stretch stays stretch until PR1 works.

---

## Later: name-change phonebook (not PR1)

Orgs do rename (~11% of EINs have 2+ cleaned `filer_name`s across years; 98.5% of those cleaned strings map to exactly one EIN).

**Do later:** reuse the existing GIN function on **Charity name history** (`ein, cleaned_name, gin`). Join `Grants.recipient_ein` (GIN) → that table. Auto-backfill when GIN → 1 EIN; skip collisions (13k ambiguous names). Same `70||SHA256(...)` so current GIN rows join with no XML reingest. Would have caught 2024 `GATES FOUNDATION` because `562618866` already filed under that name.

Phonebook trust order when that ships: XML 9-digit → Charity-history GIN → BMF/cream phonebook → colocator → leave as GIN + leftover stub.

---

## Do not

- Reingest XML to recover “was this EIN on the form.” GINs/empties are the breadcrumb.
- Count GIN occupancy as a match rate.
- Replace `recipient_ein` GIN with the phonebook EIN (keep the two columns).
- Drop an edge because the grantee’s latest year is older than the filer’s.
