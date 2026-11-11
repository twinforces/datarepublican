# Pipeline Overview for IRS 990 Name Matching

## Source TSV Generation SQL (from /Volumes/Data/final/irs990.duckdb.hermesex)

**distinct_grantee_names.tsv** (full version — the "source of truth" extract):
- Contains `grantee_name`, `grant_count`, `total_dollars`, and `recipient_ein` (when present).
- Produced from the Grants table (sometimes via joins that pull in BMF EINs).
- This is the file used by EIN-based resolvers such as `university_ein_resolver.py` (universities and some other well-covered entities have usable `recipient_ein` values from the BMF data).

```sql
COPY (
  SELECT 
    recipient_name as grantee_name,
    count(*) as grant_count,
    sum(amount) as total_dollars,
    recipient_ein
  FROM grants 
  WHERE recipient_name IS NOT NULL
  GROUP BY recipient_name, recipient_ein
  ORDER BY total_dollars DESC
) TO 'distinct_grantee_names.tsv' (DELIMITER '\t', HEADER);
```

**distinct_grantee_names_clean.tsv** (the practical daily driver):
- A reduced 3-column version (`grantee_name`, `grant_count`, `total_dollars`) with the EIN column dropped.
- Used for fast iteration with `splink_pattern_miner.py --use-clean-tsv`, `review_suggestions_tui.py`, `category_splitter.py`, and name-only resolvers (churches, most school districts, high schools, etc.).
- This is what almost all the 2026 Splink + TUI + category bucket work defaults to.

**bmf_analysis.tsv** (EIN, name, city, state, grant totals from BMF):
```sql
COPY (
  SELECT 
    ein,
    filer_name as name,
    city,
    state,
    sum(amount) as total_dollars
  FROM grants g
  JOIN irs_bmf b ON g.recipient_ein = b.ein
  GROUP BY ein, filer_name, city, state
) TO 'bmf_analysis.tsv' (DELIMITER '\t', HEADER);
```

**ein_name_variants.tsv** (authoritative charity names from IRS):
```sql
COPY (
  SELECT ein, name, sum(amount) as total_dollars
  FROM charities 
  GROUP BY ein, name
  ORDER BY total_dollars DESC
) TO 'ein_name_variants.tsv' (DELIMITER '\t', HEADER);
```

## Main Pipeline Step Order (`irs990processor.py`)

```
irsfetch → zip → bmf → xml → fec → medicare → sanctions → dot → dot_safety → ppp → sam → address → einless → match
  → geolocate_prev → geolocate_census → geolocate_api → geolocate_grok → geolocate_archive
  → grant_match → backfill → photos → ratios → percentiles → export
```

**XML `bulk_insert`:** models `prep_for_insert` (UUID7, Address canonicalize), then `executemany` for batches under 10k rows. At 10k+ or on DuckDB pin/OOM, TSV + `read_csv` `INSERT`. COPY is the fallback, not the default: small ART-index inserts are faster as `executemany`. **XML does not INSERT Addresses** — those rows append `{final_dir}/xml_addresses.tsv` and load at the `address` step with indexes down (`docs/xml_address_tsv.md`).

**Geolocate trilogy:**
| Step | Role |
|------|------|
| `geolocate_prev` | Load archive TSV; lat/lon + loose_colocator on Addresses/owners; drop empty shells |
| `geolocate_new` / census+api | Free-API geocoding (Grok deferred) |
| `geolocate_grok` | xAI batch for hard tail |
| `geolocate_archive` | Export `canonical_address` + colocator + geocoding_status TSV |

Large Census drains use `census_tsv_loop.py` (`docs/census_tsv.md`): dump pending → HTTP 2,500 with DuckDB closed → one COPY-back. Overnight: `./overnight_census_tsv_run.sh`. The 500-row `--max-files` loop is the DuckDB-save bound, not the Census bound.

After archive: **`grant_match`** (colocator + loose_colocator hail-mary) → **`backfill`** → **`photos`** (officer KG; after backfill so EIN graph is stable).

**`fec` step** (`fec_processor.py`): after XML, before medicare. Downloads FEC **bulk** zips from fec.gov (not the OpenFEC REST API; api.data.gov key is unused here). `FEC_CYCLES` env, default even years 2000–2026. A newer zip replaces that cycle in DuckDB instead of resume-append. Overnight zips/extracts land on Insurrection (`FEC_DATA_DIR`); DuckDB stays on Data. Doc: `docs/fec.md`.

```bash
FEC_CYCLES=2024,2026 ./overnight_fec_download.sh   # no DuckDB
FEC_CYCLES=2024,2026 ./overnight_fec_run.sh
# smoke: FEC_CYCLES=2026 python -u irs990processor.py --step fec --nostats -v
```

**`medicare` step** (`medicare_processor.py`): CMS NPPES + Medicaid provider spending (Parquet from opendata.hhs.gov). Promotes to `medicare_providers`, `medicare_provider_spending`, code lookups. Data under `{final_dir}/cms_data/medicare/`.

Run standalone: `python -u irs990processor.py --step medicare --nostats -v`

**Building footprints** (`building_footprints_processor.py`): Microsoft US v2 GeoJSON → sidecar `/Volumes/Insurrection/990data/buildings.duckdb` (`building_footprints`, `building_colocator`). Join `Addresses.colocator`. Not on `--step all`. Photon/Docker off. Doc: `docs/building_footprints.md`.

```bash
python3 launch_overnight_building_footprints.py
```

**CMS Physician PUF + LEIE** (`cms_puf_processor.py`): 2024 By Provider / By Provider and Service / By Geography plus OIG LEIE. Sidecar, not `--step all`. `Bene_Avg_Age` is NPI-year mix. CSVs on Insurrection (`CMS_PUF_DATA_DIR`). Direct `duckdb.connect`. Doc: `docs/cms_puf.md`.

```bash
python3 launch_overnight_cms_puf_download.py
python3 launch_overnight_cms_puf.py
```

**CMS CLIA + PFS work time** (`cms_clia_pfs_processor.py`): POS Clinical Laboratories (newest quarter) plus PPRRVU work RVUs and the CY PFS physician work-time file. `cms_clia` / `cms_pfs_rvu` / `cms_pfs_work_time`. CLIA has no NPI (`PRVDR_NUM` is the CLIA id). Not on `--step all`. Files on Insurrection (`CMS_CLIA_PFS_DATA_DIR`). Direct `duckdb.connect`. Doc: `docs/cms_clia_pfs.md`.

```bash
python3 launch_overnight_cms_clia_pfs.py
```

**USASpending award text + FTS** (`usaspending_text_processor.py`): descriptions / PSC / CFDA titles from the extracted archive CSVs → `usaspending_award_text` + DuckDB FTS. Slim `usaspending_actions` unchanged. Postgres dump parked. Not on `--step all`. Doc: `docs/usaspending_text.md`.

```bash
python3 launch_overnight_usaspending_text.py
```

**CMS PUF implied hours** (`cms_puf_hours_processor.py`): `cms_puf_service` × `cms_pfs_work_time` / `cms_pfs_rvu` → `cms_puf_service_hours` / `cms_puf_npi_hours`. Lower bound (≤10-bene lines omitted). Building sqft is a later ATTACH. Doc: `docs/cms_puf_hours.md`.

```bash
python3 launch_overnight_cms_puf_hours.py
```

**CMS PUF watch list** (`cms_puf_score_processor.py`): office vs facility, specialty p90, mill stack in a small building → `cms_puf_npi_score` / `cms_puf_watch_cells`. Not a fraud determination. Doc: `docs/cms_puf_score.md`.

```bash
python3 -u cms_puf_score_processor.py
```

**CMS PUF watch gallery** (`cms_reporting/generate_watch_gallery.py`): mill pins, solo outliers, mill detail pages under `dot_reporting/reports/cms_puf_watch/`. Campus leftover via Overpass / Overture. Fun extra `cms_puf_watch`. Doc: `docs/generate_watch_gallery.md`.

```bash
python3 -u cms_reporting/generate_watch_gallery.py
```

**`sanctions` step** (`sanctions_processor.py`): Treasury OFAC SDN list (advanced XML). Promotes to `sanctioned_entities`, `sanctioned_names`, `sanctioned_identifiers`, `sanctioned_programs`, plus street addresses in `Addresses` (`address_type=ofac_sanction`). Data under `{final_dir}/cms_data/treasury/`. Ingest only — grant-match flagging is a later consumer.

Run standalone (use `nohup` for CLI crash resilience):
```bash
nohup python -u irs990processor.py --step sanctions --nostats -v > sanctions_step.log 2>&1 &
```

Country-only OFAC locations use `FA:<iso>` colocators; blank/`undetermined` rows are skipped (not promoted to `Addresses`).

**`dot` step** (`dot_processor.py`): FMCSA Motor Carrier Census (4.45M carriers; CSV ~1.6 GB) from [data.transportation.gov](https://data.transportation.gov/Trucking-and-Motorcoaches/Company-Census-File/az4n-8mr2). Promotes to `dot_carriers` + `Addresses` (`dot_carrier_phy`, `dot_carrier_mail`). Data under `{final_dir}/cms_data/dot/`. FEC-style resume on interrupt; idempotent `.dot_census_ingest.json` marker. Production-validated 2026-06-20: 4,454,157 carriers; 4,450,675 phy + 977,160 mail addresses. Ingest only — same-building colocation is a later consumer (`grant_match` / `geolocate1`).

Run standalone (use `nohup` — long-running on large DBs):
```bash
nohup python -u irs990processor.py --step dot --nostats -v > dot_step.log 2>&1 &
```

**`dot_safety` step** (`fmcsa_safety_processor.py`): FMCSA SMS monthly public files (crash `4wxs-vbns`, inspection `rbkj-cgst`, BASIC PassProperty `4y6x-dmck`). Promotes to `fmcsa_sms_crashes` / `fmcsa_sms_inspections` / `fmcsa_sms_basics` with `dot_norm`. **Does not write Addresses.** Physical-yard gallery: `dot_reporting/generate_safety_gallery.py` (`docs/generate_safety_gallery.md`).

```bash
nohup python -u irs990processor.py --step dot_safety --nostats -v > dot_safety_step.log 2>&1 &
python3 -u dot_reporting/generate_safety_gallery.py --db-path /Volumes/Data/final/irs990.duckdb
```

**`ppp` step** (`ppp_processor.py`): SBA PPP FOIA (~11.8M loans, no EIN). Promotes to `ppp_loans` + `Addresses` (`ppp_borrower`). Data under `{final_dir}/cms_data/ppp/`. Ingest only — name+address join is a later consumer. Does not run the global `address` dedup. Doc: `docs/ppp.md`.

```bash
nohup python -u irs990processor.py --step ppp --nostats -v > ppp_step.log 2>&1 &
# or
./overnight_ppp_run.sh
```

**`sam` step** (`sam_processor.py`): SAM.gov Entity Registration Public V2 (~800–900k entities, no EIN). Promotes to `sam_entities` + `Addresses` (`sam_entity_phy`, `sam_entity_mail`). Data under `{final_dir}/cms_data/sam/`. Ingest only — name+address / UEI joins are later consumers. Does not run the global `address` dedup. Doc: `docs/sam.md`.

```bash
nohup python -u irs990processor.py --step sam --nostats -v > sam_step.log 2>&1 &
# or
./overnight_sam_run.sh
```

**`usaspending` step** (`usaspending_processor.py`): FY Award Data Archive CSVs (transaction grain). `--start-year`/`--end-year` (default 2022–2030, UEI; 990 2017 is raised). Monthly Full files by the 15th. Slim COPY into `usaspending_actions`, rollup `usaspending_by_uei` (sum of loaded FYs). Join later to SAM phy pins by `recipient_uei`. ZIPs on Insurrection by default. **Does not write Addresses.** Postgres database dump stays parked. Doc: `docs/usaspending.md`. Galleries: `spending_reporting/generate_galleries.py` (PPP charged off / Not yet paid / forgiven / payroll / zero jobs, plus federal $ pins).

```bash
USASPENDING_DATA_DIR=/Volumes/Insurrection/990data/cms_data/usaspending \
  ./overnight_usaspending_run.sh
```

**Production resume point (June 2026):** `--start-step match` (address dedup complete; `einless` already run — skip unless re-hygiene needed).

**Data separation on Grants:**
- `recipient_ein` — parsed from 990 XML (never overwritten)
- `recipient_ein_backfilled` — inferred EIN from einless phonebook, BMF pre-backfill, address/name match
- Effective EIN: `COALESCE(recipient_ein_backfilled, recipient_ein)`

**`einless` step** (`einless_processor.py`): runs after address dedup, before match.
1. Exports einless input TSVs via DuckDB COPY (see SQL below).
2. Builds exact-core phonebook from `bmf_analysis.tsv` + `ein_name_variants.tsv`.
3. Resolves no-EIN grantee names (DAF → skip big_pharma/implausible → phonebook cream).
4. Writes `recipient_ein_backfilled` + `Backfill` rows (`einless_phonebook`, `einless_daf`).

Run standalone: `python -u irs990processor.py --step einless --nostats -v`

**`pure_no_ein_by_dollars.tsv`** (names that never have a real EIN in source data):
```sql
SELECT grantee_name, COUNT(*)::BIGINT AS grant_count, SUM(grant_amt)::DOUBLE AS dollars, CAST(NULL AS VARCHAR) AS recipient_ein
FROM Grants
WHERE grantee_name IS NOT NULL AND TRIM(grantee_name) != ''
GROUP BY grantee_name
HAVING NOT BOOL_OR(recipient_ein IS NOT NULL AND TRIM(recipient_ein) != '' AND recipient_ein != '8686')
ORDER BY dollars DESC;
```

Offline hygiene (rebuild/rebucket/Grok on hard tail) lives in `einless/` — see `issue_bootstrap.md` and `docs/einless_grantee_resolution_architecture.md`.

## Overall Flow
1. Edit name_rule_constants.py (SIMPLES, GOOD_SINGLE_WORD_PRIORITIES, PROBLEM_SUFFIXES).
2. Run generator (clears cache for full run, uses pharma_siding for subsidy rows, outputs dict with variants).
3. v2 analyzer + reverse_coverage for diagnostics (raw/cleaned/best_canonical/similarity/reason).
4. Pipeline `einless` step phonebook-backfills no-EIN grantees; then `match` (address_matcher.py) and grant_match_processor.py apply name rules + address matching.
5. Visualization and fraud detection in DuckDB use the synthetic EIN for BIG PHARMA SUBSIDY rows.

## Fraud / Cross-Pollination Research (June 2026)

Ultimate goal: surface reportable fraud signals for USG review. Early ingest-only sources now in DB:

| Signal | Tables / join key | When useful |
|--------|-----------------|-------------|
| Same-building colocation | `Addresses.canonical_address` across `address_type` | Now (stats report `shared_canonical`) |
| Charity as contractor | `Charities`/`IrsBmf` ↔ `Contractors` (name/EIN) | After `match` |
| Grants to sanctioned entity | `Grants` ↔ `sanctioned_names` | After `match` + name rules |
| FEC ↔ NGO | `fec_*` ↔ `Charities` at shared address or EIN | After geolocate trilogy |
| Medicare provider grants | `medicare_providers` ↔ `Grants` (NPI/name) | After `match`; Planned Parenthood-class providers |
| DOT shell offices | Many `dot_carrier_*` rows per `canonical_address` | Standalone script + post-`loose_colocator` |

Stats report (`stats_processor.py`) now includes FEC/Medicare/sanctions/DOT table counts (0 if missing), address-type breakdown, and top multi-type canonicals + DOT-stacked addresses.

Pharma subsidy rows ($1B first-pill cost, tax deduction "charity") use synthetic EIN 99-7777777 to preserve $ volume without pretending they are normal charities.

See how_to_run_tools.md and pharma_subsidy.md for details.
