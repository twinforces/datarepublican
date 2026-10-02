# xml_address_tsv.py — defer XML addresses off DuckDB

**What:** During XML ingest, Address rows append to `{final_dir}/xml_addresses.tsv` instead of `INSERT` into production `Addresses`. The `address` step heap-loads that TSV with secondary indexes down, then runs the existing SQL dedup.

**Why:** `Addresses` is a ~105M-row heap with seven ART indexes. XML `executemany` OOM'd ~3k rows; TSV COPY then ran 100% CPU for 10h+ and never finished. Charities/Grants/Officers were blocked behind that COPY.

**How:** `XML_DEFER_ADDRESSES=1` (set for the xml step). Override path with `XML_ADDRESS_TSV`. Append is flocked + fsync. Duplicate TSV rows on XML retry are fine (heap, no PK). Do not load this TSV until XML (and other address-producing steps) are done.
