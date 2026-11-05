# building_footprints_processor.py

**What:** Microsoft US Building Footprints (v2 state GeoJSON, ~130M polygons) into a **sidecar** DuckDB. Each row is centroid, area, and `colocator` (`LL:` + Python `round(lat, 4)`), the same key as `Addresses.colocator`. `building_colocator` rolls those up per cell (count, min/avg/max/sum sqft).

**Why:** “Is this pin a 736 sq ft house or a hospital plate?” Footprints give area. Floors wait (OSM `building:levels` / LIDAR). Photon/Docker is off; this does not run a geocoder.

**Where:** Not in `irs990.duckdb` (Data is tight). Files and DB on Insurrection:

- Zips: `/Volumes/Insurrection/990data/buildings/{State}.geojson.zip`
- DB: `/Volumes/Insurrection/990data/buildings.duckdb`
- Marker: `.building_footprints_ingest.json` (skip a state when zip size and row count match)

GeoJSON is unzipped, measured, then deleted. The zip stays. License: ODbL (Microsoft). Source: [USBuildingFootprints](https://github.com/microsoft/USBuildingFootprints) (`minedbuildings…/legacy/usbuildings-v2/`). Repo archived 2026-10-08; blobs still used.

Tennessee is first in the state list so East-TN pins are queryable before California/Texas.

**How:**

```bash
# All 51 (TN first). Double-fork overnight.
python3 launch_overnight_building_footprints.py

# Smoke
BUILDING_STATES=Delaware python3 -u building_footprints_processor.py
```

Not on `--step all`. Join with ATTACH (both volumes mounted):

```sql
ATTACH '/Volumes/Insurrection/990data/buildings.duckdb' AS bld;
SELECT a.name, a.address_line1, c.n_buildings, c.min_area_sqft, c.max_area_sqft
FROM Addresses a
JOIN bld.building_colocator c ON c.colocator = a.colocator
WHERE a.colocator = 'LL:36.2128:-83.3032';
```

A colocator cell is ~11 m. One house is usually one cell with `n_buildings` 1 and `max_area_sqft` in the hundreds-to-low-thousands. A medical strip is many buildings / large max. The geocode pin and the building centroid can round into adjacent cells (Allcare `LL:36.2128:-83.3032` is empty; 953 sqft sits at `LL:36.2128:-83.3029`).

2026-11-04 ingest (wrapper **10619**): 130,099,920 buildings / 129,303,024 cells / 22G sidecar / 51 zips. `ST_Area_Spheroid` is NaN for all of California and Texas (and several other western states); those rows still have centroid + colocator. Tennessee area is populated except 65,649 NaNs.

**Next (not this job):** OpenAddresses name→street; USASpending award description + `fts`. CLIA + PFS work time is `docs/cms_clia_pfs.md`. Repair western `area_sqft` (spheroid NaN).

**Test:** `python3 -m unittest test_building_footprints.py`
