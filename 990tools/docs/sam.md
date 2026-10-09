# sam_processor.py

**What:** Ingest the SAM.gov Entity Registration Public V2 monthly extract into DuckDB (`sam_entities`) and physical + mailing streets into `Addresses` (`address_type=sam_entity_phy` / `sam_entity_mail`).

**Why:** ~800–900k firms that can receive federal awards, public, joinable to 990s / DOT / PPP by **name + street**. The extract has **UEI + CAGE**, not EIN/TIN. UEI is the later USASpending join key. Same ingest-only pattern as PPP/DOT/OFAC: load first, colocator later. Do not treat UEI or CAGE as an IRS EIN.

**Source:** [SAM Entity Management Public V2](https://open.gsa.gov/api/sam-entity-extracts-api/v1/SAM_Entity_Management_Public_V2_Extract_Layout.pdf) (GSA). Monthly UTF-8 ZIP (~150 MB compressed). Active registrations plus expired in the last 6 months. List + download via sam.gov `fileextractservices` (no API key). `SAM_API_KEY` is not required. Exclusions (debarments) are a separate daily file and are **not** in this step.

**How:**

```bash
# Full ingest (download ~150 MB, unzip nested ZIP, COPY). Does not run address-dedup.
./overnight_sam_run.sh

# Smoke: first 1,000 DAT rows
python -u irs990processor.py --step sam --max-files 1000 --nostats -v
```

Files land in `{final_dir}/cms_data/sam/`. Idempotent `.sam_entity_ingest.json` marker (path + size + `--max-files`). Re-run after a new monthly ZIP. COPY uses `db_ops.acquire_write_conn()` (there is no `db_ops.conn`).

The DAT is pipe-delimited with `BOF PUBLIC` / `EOF PUBLIC` header/trailer. Repeating NAICS/PSC strings are huge; the processor streams a slim CSV of the columns we keep, then DuckDB COPY. Entities are a **heap** COPY (unique `uei` **after**). `--max-files` caps DAT rows (smoke).

Addresses: dump `sam_entities` street columns to `sam_entity_addr_src.csv`, run `SamEntity.build_addresses()` / `Address.py` into `sam_entity_addresses.csv` (phy + mail), then `COPY` into `Addresses`. Index rebuild is **one CREATE INDEX per write session**, `threads=1`. OOM on an index skips it (16GB host). Mailing layout puts **state last** (column 46); physical puts state before ZIP.

**Not in this step:** EIN backfill, USASpending awards, SAM exclusions, grant-match, browse UI, `--step address`. Hold address-dedup until XML 09A and the other address-producing sources are in.
