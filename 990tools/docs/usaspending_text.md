# usaspending_text_processor.py

**What:** Award descriptions, PSC / NAICS / CFDA titles from the Award Data Archive CSVs already on Insurrection, collapsed to one row per `award_key`, plus a DuckDB FTS index.

**Why:** Slim `usaspending_actions` is dollars and UEI. A keyword search (“personal care”, “sickle cell”, a PSC) needs the transaction description. Collapsing modifications keeps the longest text. The Postgres dump stays parked.

**Tables:** `usaspending_award_text` (`award_key` PK, `description`, `psc`, `psc_description`, `program_code`, `program_title`, UEI/name). Join `usaspending_actions.award_key`. FTS: `fts_main_usaspending_award_text.match_bm25(award_key, 'query')`.

**How:** Uses extracted `FY*_All_*_Full_*.csv` (no re-download). Per-CSV collapse writes parquet under `USASPENDING_DATA_DIR/text_parts/` so the 108G warehouse only receives unique awards. Resume via `.usaspending_text_ingest.json`. Warehouse merge is 32 hash partitions of `award_key` (a single GROUP BY of all parts OOM'd at 12GB). Temp spills to `text_parts/_duckdb_tmp`. `--step usaspending_text` after `usaspending`.

```bash
python -u irs990processor.py --step usaspending_text --nostats -v
```

Smoke: `python3 -u usaspending_text_processor.py --start-year 2027 --end-year 2027`.

2026-11-04 ingest (wrapper **50770**): 51,082,389 awards (26.6M contract / 24.5M assistance), every row has a description. Slim `usaspending_actions` is 63,616,013 and still has no description column.

```sql
LOAD fts;
SELECT award_key, recipient_name, description, score
FROM (
  SELECT *, fts_main_usaspending_award_text.match_bm25(award_key, 'personal care') AS score
  FROM usaspending_award_text
)
WHERE score IS NOT NULL
ORDER BY score DESC
LIMIT 20;
```
