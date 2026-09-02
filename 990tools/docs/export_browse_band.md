# export_browse_band.py

**What:** DuckDB → `/browse` TSV chunks at a dollar band (default **$10M**). Charities (latest year), BMF-only endpoints, GIN ghosts, per-org leftover stubs, all-year edges with `inferred` + `suggested_ein`.

**Why:** Full 910k-org zip is slow; spiderweb is unreadable; GINs must not ship as fake EINs. PR1 in `docs/PINS.md`.

**How:** Org is in-band if max-across-years receipts/govt/contribs/assets/grants-to-others **or one grant** ≥ T. Edges only between in-band keys. Ghost `to_key` is the GIN; phonebook sits in `suggested_ein`. Leftover stub id `etc`+filer EIN. Does not write `recipient_ein`.

```bash
cd 990tools
python export_browse_band.py --db-path irs990.duckdb --threshold 10000000
```

Writes `browse/tsv_chunks/*.tsv.zip`, `browse/data_files.js`, copies into `docs/browse/`.
