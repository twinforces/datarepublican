# export_browse_band.py

**What:** DuckDB → `/browse` TSV chunks at a dollar band (default **$10M**). Charities (latest year), BMF-only endpoints, GIN ghosts, per-org leftover stubs, all-year edges with `inferred` + `suggested_ein`. Name-suppressed grantees (`big_pharma_subsidy.json`: HIPAA, see-attach, patient rollups) share one **Patient Subsidies** node (`etc997777777`) instead of one ghost per 990 string. 9-digit EIN counterparties are not remapped.

**Why:** Full 910k-org zip is slow; spiderweb is unreadable; GINs must not ship as fake EINs. PR1 in `docs/PINS.md`.

**How:** Org is in-band if max-across-years receipts/govt/contribs/assets/grants-to-others **or one grant** ≥ T. Edges only between in-band keys. Ghost `to_key` is the GIN; phonebook sits in `suggested_ein`. Leftover stub id `etc`+filer EIN. Subsidy-pattern names (any amount, non-EIN keys) fold into `etc997777777` so they never sit in leftover See More. Charity/BMF rows carry BMF `street`/`city`/`state`/`zip` for the inspector Maps link. Does not write `recipient_ein`.

```bash
cd 990tools
python export_browse_band.py --db-path irs990.duckdb --threshold 10000000
```

Writes `browse/tsv_chunks/*.tsv.zip`, `browse/data_files.js`, copies into `docs/browse/`.

Browse `$10M / $1M / All` notches read `DATA_FILES.bands`. Default load is **$10M**. `$1M` and **All** are opt-in (new loader) once those subdir chunks exist (`chunkCount > 0`). Export Database in the UI is the Hugging Face warehouse (`https://www.grumpytechbro.com/irs990.html`), not an IndexedDB dump.

```bash
python export_browse_band.py --db-path irs990.duckdb --threshold 1000000 --dest ../browse/tsv_chunks/1m --skip-manifest
python export_browse_band.py --db-path irs990.duckdb --threshold 1 --dest ../browse/tsv_chunks/all --skip-manifest
```

`--skip-manifest` keeps the $10M `dbVersion` so existing IndexedDB does not refetch. Do not point `--dest` at `tsv_chunks/` for a deeper band — that glob-deletes the $10M zips. After export, set that band's `files[].chunkCount` and `nodes`/`edges` in `data_files.js`.
