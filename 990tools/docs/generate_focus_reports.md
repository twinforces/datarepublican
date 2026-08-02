# generate_focus_reports (and state / DOT siblings)

## What

Static HTML cluster suites for Medicare, FEC, contractors, grants (and DOT via `generate_address_reports.py`). National top-N plus by-state packs.

## Why

Investigators need **domain-specific** ranking at a place key — not one generic “busy address” score.

| Focus | Question | Rank (default) |
|---|---|---|
| Medicare | Mill-shaped billing (narrow codes × high $)? | `paid / HCPCS types` |
| FEC | Many contribution/spend rows at one key? | focus row count |
| Contractor | Shell / multi-street or payee stack? | distinct streets |
| Grants | Where does grant $ land? | grant $ |
| DOT | Physical carrier farm? | active power units (`dot_carrier_phy` only) |

## Admission (2026-08)

- **Only** “this key has at least one focus row” (`focus_count > 0`).
- **Not** multi-type floors or density mins — those blocked single-address / single-HCPCS Medicare mills and were a poor substitute for ranking.
- Suite size = **`max_clusters`** after `ORDER BY` rank (default 100).
- Optional `--min-multi-type` / `--min-focus` only if set **> 0** (legacy).

```bash
python3 -u dot_reporting/generate_focus_reports.py \
  --focus medicare --slice-by address --max-clusters 100 --top-n 50
```

Slices: `address` | `colocator` | `zipcode` | `loose_colocator` (0.5°).

## Contractor names

Requires `Addresses.owner_id` → `Contractors.contractor_id`. See `docs/backfill_contractor_owner_id.md` if names are empty.
