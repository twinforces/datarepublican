# ppp_processor.py

**What:** Ingest the SBA Paycheck Protection Program FOIA loan file into DuckDB (`ppp_loans`) and borrower streets into `Addresses` (`address_type=ppp_borrower`).

**Why:** ~$800B COVID payroll loans, public, joinable to 990s by **name + address** (the FOIA dump has **no EIN**). `NonProfit` is a Y/N flag on the loan. Same ingest-only pattern as DOT/OFAC/FEC: load first, colocator / grant-match later. Do not treat borrower name as an IRS EIN.

**Source:** [data.sba.gov/dataset/ppp-foia](https://data.sba.gov/dataset/ppp-foia) (U.S. Government Works / public domain). One CSV for loans ≥ $150k plus twelve CSVs for ≤ $150k. Vintage in the filename (`240930`); objects were re-uploaded 2026-06-26 (~452 MB + ~12×414 MB). Landing page: [sba.gov PPP data](https://www.sba.gov/funding-programs/loans/covid-19-relief-options/paycheck-protection-program/ppp-data).

**How:**

```bash
# Full ingest (download ~5 GB then DuckDB COPY). Does not run address-dedup.
./overnight_ppp_run.sh

# Smoke: first CSV only
python -u irs990processor.py --step ppp --max-files 1 --nostats -v
```

CSVs land in `{final_dir}/cms_data/ppp/`. Idempotent `.ppp_foia_ingest.json` marker (size + path). Re-run after a new FOIA drop replaces files. COPY uses `db_ops.acquire_write_conn()` (there is no `db_ops.conn`). Already-downloaded CSVs are skipped on retry.

Loans are a **heap** DuckDB COPY (unique `loan_number` **after** all 13 files). Borrower streets are **not** `INSERT…SELECT`. `PppLoan.build_address()` → `Address.prep_for_insert()` / `canonicalize_address()` (PO Box colocator, ZIP split, street suffix, HTML unescape), then `bulk_insert` like DOT/OFAC.

Addresses: dump `ppp_loans` street columns to `ppp_loan_addr_src.csv`, run `PppLoan.build_address()` / `Address.py` into `ppp_borrower_addresses.csv` (no DuckDB during canonicalize), then `COPY` that file into `Addresses`. Index rebuild is **one CREATE INDEX per write session**, `threads=1`, temp dir set **once**. Already-loaded `ppp_borrower` skips CSV/COPY. OOM on an index skips it (16GB host).

`--max-files` limits how many CSVs to fetch/COPY (smoke). After ingest, run `address` so PPP streets get colocators like everyone else.

**Not in this step:** EIN backfill, grant-match, browse UI. Nonprofit loans are `WHERE nonprofit` on `ppp_loans`.
