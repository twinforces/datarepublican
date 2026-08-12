# OFAC SDN co-location reports

Isolated package for **Treasury / enforcement triage**: locations where an OFAC-listed entity shares a physical footprint with IRS Form 990 organizations, DOT motor carriers, FEC political money, or Medicare / NPPES providers.

This is **not** an official OFAC product. It is an analytic HTML package built from:

| Source | Role |
|--------|------|
| Treasury OFAC advanced SDN XML | `sanctioned_entities`, names, programs, identifiers |
| `Addresses` (`address_type = ofac_sanction`) | Street / colocator / ZIP for SDN entities |
| IRS Form 990 | Charities, grants, officers, contractors |
| FMCSA | DOT carriers |
| FEC | Contributor / committee / expenditure addresses |
| CMS NPPES / Medicare | Provider practice & mailing addresses |
| `Zips` catalog | Valid US ZIP filter (no report-time REGEXP) |

## Open the package

```bash
# After generate
open ofac_reporting/reports/index.html
# or
python3 -m http.server 8001 --directory ofac_reporting/reports
```

## Regenerate (read-only on the DB)

```bash
cd /path/to/990tools
python3 -u ofac_reporting/generate_ofac_reports.py --all-slices \
  --db-path /Volumes/Data/final/irs990.duckdb
```

Suite dirs are **stable** (no date in path) for public deploy — e.g. `ofac_colocator_clusters/`.  
Regenerate overwrites in place; `Generated …` timestamps still appear in page metadata.

Options:

- `--slice-by colocator|address|zipcode` — one suite only  
- `--max-clusters N` — default 200 (full stack counts are small for address/colocator)  
- `--top-n N` — entities per detail table  

## How to read the suites

1. **`ofac_colocator_clusters/`** — tight colocator keys only: `LL:lat:lon` or `PO:box:zip5`. Excludes `FA:` / `VENDOR:` / geocode junk, and **city-only shells** (Dubai, London, “Miami, Fl, 33102” with no street number) even when they carry an LL: from bad geocoding. **Start here.**  
2. **`ofac_loose_colocator_clusters/`** — half-degree `LL:` grid (~neighborhood). Same key family as grant_match / DOT **loose_colocator**. Wider than building LL, tighter than ZIP.  
3. **`ofac_address_clusters/`** — exact `canonical_address` match (strongest “same building / mail drop”). City-only strings (no street number) are dropped.  
4. **`ofac_zipcode_clusters/`** — same valid US ZIP (`INNER JOIN Zips`). **Widen / context only**; high false-positive rate.

**Links:** 990 filer EINs → ProPublica Nonprofit Explorer  
`https://projects.propublica.org/nonprofits/organizations/<ein>`.  
BMF-only EINs → `reports/eins/<ein>.html`. SDN tax IDs not in BMF/Charities → plain text.  
DOT# → SearchCarriers (SC) + MOTUS.

**Pass rule (all suites):** ≥1 `ofac_sanction` address row **and** ≥1 co-tenant type (charity, grant, officer, contractor, DOT, FEC, NPPES, BMF).

Detail pages include OFAC UID, primary name, list type, program codes, aliases, **US FEIN / Tax ID** from `sanctioned_identifiers`, optional **EIN → Form 990** filer match, and co-tenant tables. Suite indexes use **TanStack Table** (sortable columns, search, pagination). Confirm UIDs on [sanctionssearch.ofac.treas.gov](https://sanctionssearch.ofac.treas.gov/).

## Important caveats (share with reviewers)

**Do not assume.** Co-location is a **shared footprint** in the data, not guilt, control, or the same conduct.

> Jeffrey Dahmer was a cannibal. His neighbors were not cannibals—just neighbors.  
> My father-in-law lived in a bordello; he was not a whore—he was a teenage runaway hiding from his stepfather until he could join the Air Force.  
> **Same address ≠ same role.**

- Co-location is by **shared address key**, not by corporate control or name identity.  
- Same ZIP is **not** same building.  
- Many SDN rows are foreign or country-level (`FA:`) and never appear in the colocator suite.  
- Name-match of grantees to `sanctioned_names` is a **separate** product (not this package).  
- Presence of a 990 filer at an SDN address is a **lead**, not a finding of violation.

## Layout

```
ofac_reporting/
  README.md
  generate_ofac_reports.py
  reports/
    index.html                 # master
    ofac_colocator_clusters/
    ofac_loose_colocator_clusters/
    ofac_address_clusters/
    ofac_zipcode_clusters/
    eins/                      # BMF-only EIN detail pages
```

JSON sidecars under each suite’s `data/` folder support offline review and spreadsheet export.

## Snapshot DB (optional)

A research snapshot of the main DuckDB with medicare rollups promoted:

`/Volumes/Data/final/irs990.duckdb.with_medicare_rollup_20260713`

Reports only need the live path with `sanctioned_*` + `Addresses` populated (`--step sanctions` already run in production).
