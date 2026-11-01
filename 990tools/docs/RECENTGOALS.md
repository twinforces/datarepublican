# RECENTGOALS.md — 990tools

Active scratchpad (What / Why / How + hashes). Older detail lives in CHANGELOG.md.

---

## 2026-11-01: Bad address cross-links on PPP / federal pins

**What:** Pin pages that share NPPES / contractor / grant / charity / DOT / FEC rows with other Fun lanes get a Bad address panel. Live links only when that lane already has a detail page. Same-key PPP cuts and Federal spending link too.

**Why:** Address types were counts with nowhere to go. Combined roster lives on this page (loans / SAM / charities); other lanes keep their own pages.

**How:** `spending_reporting/geo_overlap.py`. Sidecar `types`. `refresh_geo_overlap` after Widen. Test: `test_generate_geo_reports.py`.

---

## 2026-11-01: PPP / federal geo matches trucking Widen

**What:** Geo suite indexes use breadcrumbs (`All reports › lane · slice`). Pin pages add **Widen:** address → colocator (all variants) → loose 0.5° → ZIP → state. The Building / Zip / State / Area lane bar is gone.

**Why:** Fun geography is a zoom-out from a pin, same as trucking.

**How:** `spending_reporting/geo_zoom.py`. `generate_geo_reports.py` wrap. Test: `test_generate_geo_reports.py`.

---

## 2026-11-01: TanStack sort on every PPP / federal table

**What:** Pin details, by-state ranking tables, leftover loan galleries, and federal cluster indexes use the same DOT TanStack widget as PPP-cut cluster indexes.

**Why:** Users change the sort on any column, on every Fun table.

**How:** `generate_geo_reports.py` `__TS_TABLES__` / `__CLUSTER_TABLE__`. `generate_galleries.py` payload on `page()`. `--national-only`, `--skip-details`. Test: `test_generate_geo_reports.py`.

---

## 2026-11-01: Federal $ / pop and $ / EV maps

**What:** Federal by-state Fun pages get two extra heatmaps: FY2025 $ per Census Vintage 2024 resident, and $ per 2024 electoral vote.

**Why:** Total $ is headquarters volume. Per-capita and per-EV are the political cuts.

**How:** `spending_reporting/state_weights.py`. `generate_geo_reports.py --heatmaps-only --only federal`. Wrap `--copy-focus federal`.

---

## 2026-11-01: PPP cut Fun rows + TanStack

**What:** Fun matrix rows PPP Charged Off / Not yet paid / Forgiven / Payroll / Zero jobs. Cluster indexes use DOT TanStack (sort leftover, forgiven %, jobs, status counts). Leftover domain `ppp` stays on disk; `--only ppp` does not match the cuts.

**Why:** Leftover>0 drops fully forgiven pins. Payroll and zero-jobs need different ORDER.

**How:** `python3 -u spending_reporting/generate_geo_reports.py --only ppp_charged_off,ppp_not_yet_paid,ppp_forgiven,ppp_payroll,ppp_zero_jobs`. Wrap `python3 scripts/build_site.py --copy-focus ppp,federal` from grumpytechbro.com. Test: `test_generate_geo_reports.py`. Slice TanStack assets after `</%doc>` so the Mako doc’s `<%text>` mention does not leak.

---

## 2026-11-01: PPP / federal geography (colocator, zip, loose, state)

**What:** DOT-style cluster reports for PPP leftover $ and FY2025 federal $ on SAM pins. Colocator / zip / loose 0.5° / clickable state heatmap. Detail pages list PPP loans, SAM dollars, other address types, charities.

**Why:** Galleries rank loans. Geography answers who else is on the pin. Same slices as DOT.

**How:** `python3 -u spending_reporting/generate_geo_reports.py --db-path /Volumes/Data/final/irs990.duckdb`. Writes `spending_reporting/reports/geo/` (gitignored). Doc: `docs/generate_geo_reports.md`. Test: `test_generate_geo_reports.py`.

---

## 2026-11-01: USASpending FY2025 slim COPY + PPP galleries

**What:** Land FY2025 contracts + assistance Award Data Archive ZIPs on Insurrection, slim-COPY into DuckDB (`usaspending_actions` / `usaspending_by_uei`), join later to SAM phy pins by UEI. PPP reports: charged off, Not yet paid (Exemption 4), forgiven-on-unfinished, payroll $/job, zero jobs. Federal $ pins after ingest.

**Why:** SAM has no dollars. Postgres dump stays parked. GOV $ to location even if not a fraud cut. Exemption 4 is unfinished FOIA status, labeled **Not yet paid**. Forgiveness already on those rows is the possible-fraud cut.

**How:** `USASPENDING_DATA_DIR=/Volumes/Insurrection/990data/cms_data/usaspending` + `./overnight_usaspending_run.sh`. Reports: `python3 -u spending_reporting/generate_galleries.py --db-path /Volumes/Data/final/irs990.duckdb`. Docs: `docs/usaspending.md`, `docs/generate_galleries.md`. Tests: `test_usaspending_processor.py`. Do not ingest the Postgres dump.

---

## 2026-10-17: Photon / maps.co / OpenCage on leftover pending_api

**What:** Chunked `geolocate_api` on ~608k `pending_api`. Public Photon first, then maps.co, then OpenCage. Misses → `grok_pending`. Grok off.

**Why:** Cheap parks and strip are done. This is the slow free pass.

**How:** `./overnight_geolocate_api_run.sh`. Unset `PHOTON_*`. Skip per-round Geocoding COUNT. Doc: `docs/overnight_geolocate_api.md`.

## 2026-10-17: tight MATCH + junk strip on leftover pending_api

**What:** Five tight MATCH rules plus street-only strip of trailing N/A, 0.0, and glued city/state/zip.

**Why:** Census then census_strip already retries C/O+suite. Leftover `pending_api` still had inverted PO, RR/HC, C/O-no-digit, unit-only streets, and foreign-state-without-US-ZIP. Strip is for Photon, not a park.

**How:** `geocoding_api_processor.py` preprocess + `_strip_for_census_strip`; `census_tsv_loop.strip_co_and_suite` for future TSV. Re-run `./overnight_preprocess_pending_api_run.sh`. Grok off. Photon next.

## 2026-10-17: pending_api pattern catch-up

**What:** Run preprocess on `pending_api` (Census TSV skipped the live first stage).

**Why:** ~96k of 704k would have been PatternOwners/PO before Census HTTP.

**How:** `./overnight_preprocess_pending_api_run.sh` → `preprocess_grok_pending.py --statuses pending_api --batch-size 400`. Grok off. Photon next.

---

## 2026-10-15: Census TSV geocode loop

**What:** Dump pending `Geocoding` to TSV, call Census at 2,500/HTTP with DuckDB closed, append results, one COPY-back. Free services only.

**Why:** The 500-row overnight was DuckDB-save bound (~12k/hour, weeks for 5.7M). Census itself wants 2,500. Heap stays out of the HTTP path.

**How:** `census_tsv_loop.py`, `overnight_census_tsv_run.sh`. Files in `/Volumes/Data/final/cms_data/census_tsv/`. Resume skips ids already in `results.tsv`. Apply leftover → `pending_api`. Tests: `test_census_tsv_loop.py`. Doc: `docs/census_tsv.md`. Stopped PGID 68557. Does not run Grok. Do not `--step address` until 09A unless asked.

---

## 2026-10-09: SAM.gov public entity ingest

**What:** `--step sam` loads the SAM Entity Registration Public V2 monthly extract into `sam_entities` and phy/mail streets into `Addresses` (`sam_entity_phy`, `sam_entity_mail`). No EIN/TIN in the public file.

**Why:** Bounded (~800–900k) federal-vendor registry with UEI + street, joinable later to 990/DOT/PPP by name+address and to USASpending by UEI. USASpending itself stays parked (disk). Do not treat UEI/CAGE as an IRS EIN.

**How:** `sam_processor.py`, `models/sam_entity.py`, `overnight_sam_run.sh`. Pipeline: after `ppp`, before `address`. Tests: `test_sam_processor.py`. Doc: `docs/sam.md`. Does not run `--step address`. Hash: `322b469e`.

---

## 2026-10-05: Blank-identity MCS-150 list + Leaflet map

**What:** `blanks.html` lists USDOTs whose legal name is `NO GIVEN NAME` / `NO NAME GIVEN` / `NONE NONE` or a doubled given name (`HAROLD HAROLD`). Leaflet map of physical pins. Drops `X & X LLC`.

**Why:** Danielle's chart of placeholder MCS-150 names. Harm rankings never show them (no SMS deaths).

**How:** `generate_safety_gallery.py --only blanks`. Nav + Fun wrap. Tests: `BlankIdentityTests`.

---

## 2026-10-04: Deadliest PO Boxes in the Rogues gallery

**What:** `poboxes.html` is the 100 PO Boxes with the most SMS crash deaths. Same death-first sort as Volume. Footprint is `PO:box:zip5` on phy or mail.

**Why:** Volume is MCS-150 `LL:` yards. Phy-as-PO has almost no deaths (3). The deadly mailboxes are mail drops (J.B. Hunt / fleet HQs and 1-truck killers using a box).

**How:** `generate_safety_gallery.py` `footprint=pobox`. Nav + master index + Fun wrap 4th link. Tests: `test_pobox_ranks_mail_po_not_yards`.

---

## 2026-10-02: SBA PPP FOIA ingest

**What:** `--step ppp` loads the SBA PPP FOIA CSVs into `ppp_loans` and borrower streets into `Addresses` (`ppp_borrower`). No EIN in the public file.

**Why:** ~$800B COVID payroll loans, joinable later by name + address. Same ingest-only pattern as DOT/OFAC. Do not treat borrower name as an IRS EIN.

**How:** `ppp_processor.py`, `models/ppp_loan.py`, `overnight_ppp_run.sh`. Pipeline: after `dot_safety`, before `address`. Tests: `test_ppp_processor.py`. Doc: `docs/ppp.md`.

---

## 2026-10-02: Ship DOT SMS on the whole stack + Rogues hub row

**What:** Master index has a featured Rogues galleries row for `unsafe_colocators` (Volume / Harm/PU / Identity farms). Gallery pages link All reports. `regen_dot_sms.sh` refreshes the gallery, then the four national DOT slices, then the four by-state DOT slices, against `irs990.duckdb`, rebuilding the hub after each.

**Why:** Existing colocator/zip/address detail pages already attach SMS; the HTML was stale. `parse_suite` cannot see `unsafe_colocators`, so the gallery never appeared in the matrix. Featured extras keep it on the hub without stealing the DOT/colocator cell.

**How:** `dot_reporting/build_master_index.py` `FEATURED` + `collect_featured`. Tests: `test_build_master_index.py`, `test_fmcsa_safety.py`. Regen: `dot_reporting/regen_dot_sms.sh`. Docs: `docs/build_master_index.md`, `docs/generate_safety_gallery.md`.

---

## 2026-10-02: FMCSA SMS + unsafe-address gallery

**What:** `dot_safety` ingests SMS crash / inspection / BASIC. Gallery has volume rank plus identity-farm / chameleon stack rank (shared census phone, paper new vinyl on a small-harm mill phone).

**Why:** Signal Hill is 535 one-truck LLCs on one phone. A 30-PU “fleet HQ” cap drops real farms; the fatality-only gate dropped Signal Hill (OOS/BASIC, 0 deaths). Vinyl is paper extras next to small harm, not agent shells around United Van Lines.

**How:** `fmcsa_safety_processor.py`, `dot_reporting/generate_safety_gallery.py`, `--step dot_safety`. Stack: ≥2 USDOTs on a 10-digit phone with SMS harm, and (≥2 paper extras when every harm DOT has PU ≤ 50, or ≥2 small fatality DOTs). Sort is Mill×harm = Apple/PU × physical USDOT records (vet-your-drivers stain on a legal mill). Rate rank: Yard/PU and Apple/PU on `rates.html`, weights universe-wide sum(fatalities)/sum(metric) (~0.030 per crash, ~0.0069 per OOS). Each BASIC flag has its own weight (unsafe ≈ 187, maint ≈ 23.5, HOS ≈ 12.7); lit flags add. Tests: `test_fmcsa_safety.py`.

---

## 2026-10-02: XML addresses go to TSV, not DuckDB

**What:** XML PDC save appends Address rows to `{final_dir}/xml_addresses.tsv`. DuckDB insert waits for the `address` step (indexes down, then COPY).

**Why:** Address COPY of ~3.5k rows sat at 100% CPU for 10–14h on the 105M-row heap. Queue frozen at 45,683.

**How:** `xml_address_tsv.py`, `pending_database_context.py`, `xml_processor.py`, `address_deduplication_processor.py`. Tests: `test_xml_address_tsv.py`. `XML_DEFER_ADDRESSES=1`.

---

## 2026-10-01: overnight wrapper restarts after XML OOM

**What:** `log` after `wait` uses `${start}->${stop}`. Default `XML_OOM_RESTARTS=120`.

**Why:** `set -u` treated `$start→$stop` as one unset name, so the wrapper exited 1 after `os._exit(75)` and never resumed. 08A is ~51k files at ~500–600 per OOM.

**How:** `overnight_ingest_run.sh`.

---

## 2026-10-01: XML checkpoint skips Geocoding histogram

**What:** Intermediate FORCE CHECKPOINT no longer `GROUP BY geocoding_status` unless `LOG_GEOCODING_STATUS_COUNTS=1`.

**Why:** 08A catch-up died at 567/51,530 files: that histogram OOM'd DuckDB at 7.4 GiB. Address COPY had already succeeded.

**How:** `pending_database_context.py` `_log_geocoding_status_counts`.

---

## 2026-10-01: Finger is Focus; + is Expand

**What:** Focus (hand, default) always isolates. Plus expands hops both ways and keeps the rest of the graph.

**Why:** Second Focus click was expanding. Uniparty seeds stayed on the graph. Expand is its own mode.

**How:** `clickNode` in `browse/models.js`. Tooltips/help. Tests in `browse/test/viewModel.test.js`.

---

## 2026-10-01: IndexedDB replaces itself on dbVersion mismatch

**What:** Stale `CharityDatabase` is cleared and re-downloaded when `DATA_FILES.dbVersion` moves, or when ingest never wrote `generated: true`.

**Why:** Version mismatch used to keep the old charities/grants stores. `put()` mixed rows. `generated: true` used to be written before TSV ingest, so a half-load looked valid. Users had to delete IndexedDB by hand.

**How:** `idbDataIsCurrent` in `browse/data_files.js`. `hasValidData` false → `clearGraphStores` (charities, grants, metadata). `generated: true` only after `ingestBandFromWeb` finishes. Bumped `dbVersion` to `2026-10-01T12:00:00Z`. Tests in `browse/test/data_files.test.js`.

---

## 2026-10-01: Uniparty Focus actually isolates

**What:** Focus click tunnels unless that org is already the only seed. Inspector has Focus (`tunnelNode`) plus Zoom ±1 hop.

**Why:** Uniparty seeds are `desiredVisible`. First Focus click was treated as “click again → expand,” so Freedom House grew and the rest of the preset stayed.

**How:** `browse/models.js` `clickNode`, `browse/script.js` inspector, `browse/test/viewModel.test.js`.

---

## 2026-10-01: executemany first, COPY fallback

**What:** `bulk_insert` uses `executemany` below 10k rows. Larger batches, or OOM, go TSV COPY. COPY OOM still `os._exit(75)`.

**Why:** XML-sized inserts were faster as `executemany`; 78k-grant files and PPP-scale heaps need COPY.

**How:** `BULK_INSERT_COPY_THRESHOLD`, `_copy_insert_chunk`.

---

## 2026-10-01: Skip XmlFiles whose xml_name is already in Charities

**What:** Before XML work, mark those files processed. Charity `bulk_insert` also drops duplicate `xml_name`s.

**Why:** Mid-PDC COPY committed Charities then aborted. Resume died on unique `xml_name`.

**How:** `mark_xml_processed_if_charity_exists`, always-on skip in `bulk_insert`.

---

## 2026-10-01: COPY is INSERT, not OR IGNORE

**What:** `insert_tsv_via_copy` uses `INSERT`. Models still `prep_for_insert` / UUID7 before the TSV is written.

**Why:** Address UUID7 did run. DuckDB `INSERT OR IGNORE` needs a UNIQUE/PK. Production Addresses is a heap, so 3872 Address rows failed even with IDs.

**How:** Same path as old executemany: serialize prepped objects, insert.

---

## 2026-10-01: Schedule L contractors dropped filer_ein

**What:** `parse_schedule_l` now uses `charity.build_contractor()` so `filer_ein` is the form EIN.

**Why:** Direct `Contractor(name, amount, tax_year, charity_id)` never set `filer_ein`. `executemany` stored `''`; COPY turned empty TSV into NULL and hit `Contractors.filer_ein NOT NULL`. Log `{0}` was rewritten to `[MISSING]` before `.format(*args)` — the charity EIN was there.

**How:** `base_parser.py`. Not a 990-EZ vs 990 path; Schedule L is on the root 990.

---

## 2026-10-01: bulk_insert is TSV + COPY

**What:** `DatabaseOperations.bulk_insert` writes a TSV then `INSERT OR IGNORE … SELECT FROM read_csv`. Indexes stay. `test_bulk_insert_copy.py`.

**Why:** XML `executemany` OOM at 7.4 GiB with ~78k grants in one PDC. PPP already proved heap COPY. Incremental XML cannot drop Addresses ART (rebuild is hours).

**How:** `database_operations.py` (`write_bulk_tsv`, `insert_tsv_via_copy`). Chunk size still `BULK_INSERT_BATCH_SIZE`.

**Next:** Kick XML-only overnight with this path.

---

## 2026-09-04: Sticky inspector + Zoom camera

**What:** Inspect opens a right drawer (kind, money, top-5 in/out, compact links). Graph click does not close it (Escape / ×). Click mode **Zoom** (and the card’s Zoom ±1 hop) is a camera: same `k`/`translate` as shift-drag box zoom. Leftover See more loads the next hosted band from the card (`$1M` wait scaled from this machine’s `$10M`). Graph surgery stays in click-mode, not the card. Map view later as a popup from the drawer (`openInspectorMap` stub).

**Why:** Overlay controls eat the Sankey. `$1M`/`All` are triggered from See more / inspect, not the top notches. Fitting every 1-hop neighbor (or `2×` column pitch) is the whole 3-column graph, so scale never moved.

**How:** Box is this node’s layout rect; height so the node is ~55% of the view; horizontal margin `min(pitch, 3×nodeH, 8×nodeW)`. `npm run test:browse` (42). Hash `8c18cf27`.

**Next:** Map view later. Overlay layout flyout still optional polish.

---

## 2026-09-03: $1M / All bands on grumpytechbro.com

**What:** `$10M` stays in the Vercel `docs/` deploy. `$1M` (~58 MB / 208 zips) and **All** (~363 MB / 823 zips) `baseFile` is `https://www.grumpytechbro.com/browse/tsv_chunks/{1m,all}/`. CORS `Access-Control-Allow-Origin: *` on that directory. Loader records last `$10M` unzip+IDB time and estimates deeper bands. Leftover “See more” offers the next *hosted* notch. Local zip trees stay untracked.

**Why:** GitHub/Vercel cannot ship All (~350 MB extra in every clone and deploy). Git LFS is for clones, not a CDN. Hugging Face as a free-account website origin is a TOS gray area (community reuse, not a private CDN) and the Hub just sold to Nvidia. DreamHost already rsyncs multi-GB `/fun/` trees.

**How:** `grumpytechbro.com/scripts/rsync-browse-bands.sh` (not `deploy.sh --delete`). After a deeper `export_browse_band.py`, rsync then bump `chunkCount` / `nodes` / `edges` / `zipBytes` in `browse/data_files.js`. Smoke: browser `fetch` of a `$1M` zip from `localhost:4000` (413 707 bytes) then `Loading $1M: 8/208 files`. `npm run test:browse` (40). Doc: `docs/export_browse_band.md`. Hash `58f0659f` (GTB `13e6689`).

**Next:** Re-export `$1M`/All when the DuckDB changes. All is still a 2.6M-node in-browser freeze risk.

---

## 2026-09-02: Biggest Pharma + Patient Subsidies sink

**What:** HIPAA / see-attach / patient-rollup grantee names fold into one **Patient Subsidies** node (`etc997777777`) at **export**, not in the browser. Preset **Biggest Pharma** is seven PAPs (Genentech, J&J, Sanofi, Boehringer, GSK, Pfizer, Otsuka) placing `~0~1` onto that shared edge. $10M chunks rebuilt (`dbVersion` `2026-09-03T05:52:46Z`): 4,777 filers, $194B into the sink. Client-side retarget was a miss (refresh restored the old URL seeds; JSON module import was fragile).

**Why:** Per-GIN “Individual Patient Programs” / leftover See More made a $95B graph with no sharing. Mapping belongs with `big_pharma_subsidy.json`, same as Python reports.

**How:** `export_browse_band.py` + `grant_suppress.is_suppressed_sql`; 9-digit EINs not remapped. `browse/big_pharma_subsidy.js` for identity tests. `python3 test_export_browse_band.py`; `npm run test:browse` (36). Doc: `docs/export_browse_band.md`. Hash `f9e5336e`.

**Next:** Refresh still restores URL seeds — use Replace + Biggest Pharma.

---

## 2026-09-03: Inspector, subtract mode, skip phonebook toggle

**What:** Inspector drops Guide Star / Charity Navigator; adds Google Maps (BMF mailing street). New **Subtract** mode (X / Shift). Focus on an already-desired node expands 3 up / 3 down instead of tunneling again. Modifiers in mode tooltips (⌘/Ctrl add, ⌥/Alt inspect, Shift subtract). Playwright `tests/browse_sankey.spec.js` (`--project=demo`) is a regression walk, not a demo camera — too fast / Gates clip starts by clearing the graph (white). **PR4 Trust-the-phone-book skipped:** ghosts stay dead-ends; a global collapse would merge the ~1/3 bad $100M guesses.

**Why:** Mode buttons should match the modifier set. Maps is mailing-of-record, not a physical site. Collapse without votes is dishonest.

**How:** BMF `street/city/state/zip` on $10M export (`dbVersion` `2026-09-03T17:26:39Z`). `npm run test:browse` (40). Hash `d1301a4c`.

**Next:** Map view (Leaflet) as an inspector popup, not a Sankey overlay.

---
