#!/usr/bin/env python3
"""Microsoft US Building Footprints → sidecar DuckDB.

Polygons stay on disk only long enough to measure. The warehouse table is
centroid + area + colocator (same LL:round(lat,4) as Addresses). Join is
ATTACH buildings.duckdb then building_colocator.colocator = Addresses.colocator.

Not on --step all. Sidecar on Insurrection so the 108G irs990 file does not grow.
"""

from __future__ import annotations

import json
import os
import zipfile
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

import duckdb

from download_utils import ensure_download
from logging_utils import log_info, log_warning

DEFAULT_DATA_DIR = Path("/Volumes/Insurrection/990data/buildings")
DEFAULT_DB = Path("/Volumes/Insurrection/990data/buildings.duckdb")
BASE = "https://minedbuildings.z5.web.core.windows.net/legacy/usbuildings-v2"

# Tennessee first so Allcare-style pins are queryable before CA/TX finish.
# Slug is the Microsoft filename (DistrictofColumbia, NewHampshire, …).
STATE_SLUGS: Tuple[str, ...] = (
    "Tennessee",
    "Alabama",
    "Alaska",
    "Arizona",
    "Arkansas",
    "California",
    "Colorado",
    "Connecticut",
    "Delaware",
    "DistrictofColumbia",
    "Florida",
    "Georgia",
    "Hawaii",
    "Idaho",
    "Illinois",
    "Indiana",
    "Iowa",
    "Kansas",
    "Kentucky",
    "Louisiana",
    "Maine",
    "Maryland",
    "Massachusetts",
    "Michigan",
    "Minnesota",
    "Mississippi",
    "Missouri",
    "Montana",
    "Nebraska",
    "Nevada",
    "NewHampshire",
    "NewJersey",
    "NewMexico",
    "NewYork",
    "NorthCarolina",
    "NorthDakota",
    "Ohio",
    "Oklahoma",
    "Oregon",
    "Pennsylvania",
    "RhodeIsland",
    "SouthCarolina",
    "SouthDakota",
    "Texas",
    "Utah",
    "Vermont",
    "Virginia",
    "Washington",
    "WestVirginia",
    "Wisconsin",
    "Wyoming",
)

SQFT_PER_M2 = 10.76391041671
# v1 called ST_Area_Spheroid(geom). That function reads X as latitude, GeoJSON X
# is longitude, so |lon|>90 (west of 90°W) is NaN and east areas are too small.
INGEST_VERSION = 2


def default_data_dir() -> Path:
    return Path(os.environ.get("BUILDING_DATA_DIR", str(DEFAULT_DATA_DIR)))


def default_db_path() -> Path:
    return Path(os.environ.get("BUILDING_DB", str(DEFAULT_DB)))


def parse_states(raw: Optional[str] = None) -> List[str]:
    text = raw if raw is not None else os.environ.get("BUILDING_STATES", "")
    if not text.strip():
        return list(STATE_SLUGS)
    known = {s.lower(): s for s in STATE_SLUGS}
    out: List[str] = []
    seen = set()
    for part in text.split(","):
        item = part.strip()
        if not item:
            continue
        slug = known.get(item.lower().replace(" ", "").replace("_", ""))
        if not slug:
            raise ValueError(f"unknown BUILDING_STATES entry {item!r}")
        if slug not in seen:
            seen.add(slug)
            out.append(slug)
    return out


def zip_url(slug: str) -> str:
    return f"{BASE}/{slug}.geojson.zip"


def colocator_sql(lat_expr: str, lon_expr: str) -> str:
    """Match Addresses: Python round(x, 4) then f'LL:{{lat}}:{{lon}}'."""
    return f"printf('LL:%.4f:%.4f', ROUND({lat_expr}, 4), ROUND({lon_expr}, 4))"


def area_sql(geom: str) -> str:
    """Spheroid area. Flip so GeoJSON lon/lat matches ST_Area_Spheroid's lat/lon."""
    return f"ST_Area_Spheroid(ST_FlipCoordinates({geom}))"


def _say(msg: str) -> None:
    print(msg, flush=True)
    log_info(msg)


def _marker_path(data_dir: Path) -> Path:
    return data_dir / ".building_footprints_ingest.json"


def _load_marker(data_dir: Path) -> dict:
    path = _marker_path(data_dir)
    if not path.exists():
        return {"states": {}}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"states": {}}


def _save_marker(data_dir: Path, marker: dict) -> None:
    _marker_path(data_dir).write_text(json.dumps(marker, indent=2), encoding="utf-8")


def _unzip_geojson(zip_path: Path, dest_dir: Path) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as zf:
        names = [n for n in zf.namelist() if n.lower().endswith(".geojson") and not n.endswith("/")]
        if not names:
            raise RuntimeError(f"no .geojson in {zip_path.name}")
        name = names[0]
        target = dest_dir / Path(name).name
        if target.exists() and target.stat().st_size > 0:
            return target
        with zf.open(name) as src, target.open("wb") as out:
            while True:
                chunk = src.read(8 * 1024 * 1024)
                if not chunk:
                    break
                out.write(chunk)
        return target


class BuildingFootprintsProcessor:
    def __init__(
        self,
        db_path: Optional[str] = None,
        data_dir: Optional[Path] = None,
        *,
        download: bool = True,
    ):
        self.db_path = Path(db_path) if db_path else default_db_path()
        self.data_dir = Path(data_dir) if data_dir else default_data_dir()
        self.download = download

    def run(self, states: Optional[Iterable[str]] = None) -> Dict[str, int]:
        slugs = list(states) if states is not None else parse_states()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        mem = os.environ.get("DUCKDB_MEMORY_LIMIT", "12GB")
        _say(
            f"building footprints db={self.db_path} data={self.data_dir} "
            f"states={len(slugs)} mem={mem} ingest_version={INGEST_VERSION}"
        )
        conn = duckdb.connect(
            str(self.db_path),
            config={
                "memory_limit": mem,
                "threads": "1",
                "preserve_insertion_order": "false",
            },
        )
        stats: Dict[str, int] = {}
        try:
            conn.execute("INSTALL spatial")
            conn.execute("LOAD spatial")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS building_footprints (
                    state VARCHAR,
                    area_m2 DOUBLE,
                    area_sqft DOUBLE,
                    centroid_lat DOUBLE,
                    centroid_lon DOUBLE,
                    colocator VARCHAR
                )
                """
            )
            marker = _load_marker(self.data_dir)
            marker.setdefault("states", {})
            for slug in slugs:
                stats[slug] = self._ingest_state(conn, slug, marker)
            self._rebuild_colocator(conn)
            n = int(conn.execute("SELECT COUNT(*) FROM building_footprints").fetchone()[0])
            c = int(conn.execute("SELECT COUNT(*) FROM building_colocator").fetchone()[0])
            stats["building_footprints"] = n
            stats["building_colocator"] = c
            conn.execute("CHECKPOINT")
            bad = int(
                conn.execute(
                    "SELECT COUNT(*) FROM building_footprints "
                    "WHERE isnan(area_sqft) OR isinf(area_sqft) OR area_sqft IS NULL"
                ).fetchone()[0]
            )
            _say(
                f"building footprints {n:,} buildings, {c:,} colocator cells, "
                f"{bad:,} bad area_sqft"
            )
        finally:
            conn.close()
        return stats

    def download_zips(self, states: Optional[Iterable[str]] = None) -> None:
        slugs = list(states) if states is not None else parse_states()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        for slug in slugs:
            dest = self.data_dir / f"{slug}.geojson.zip"
            log_info(f"download {slug}")
            ensure_download(zip_url(slug), dest)

    def _ingest_state(self, conn: duckdb.DuckDBPyConnection, slug: str, marker: dict) -> int:
        zip_path = self.data_dir / f"{slug}.geojson.zip"
        if self.download:
            ensure_download(zip_url(slug), zip_path)
        if not zip_path.exists() or zip_path.stat().st_size == 0:
            raise RuntimeError(f"missing {zip_path}")
        zip_size = zip_path.stat().st_size
        prev = marker["states"].get(slug) or {}
        already = int(
            conn.execute(
                "SELECT COUNT(*) FROM building_footprints WHERE state = ?",
                [slug],
            ).fetchone()[0]
        )
        if (
            already > 0
            and prev.get("zip_size") == zip_size
            and prev.get("rows") == already
            and prev.get("version") == INGEST_VERSION
        ):
            _say(f"skip {slug}: {already:,} rows, zip unchanged, v{INGEST_VERSION}")
            return already
        geojson = _unzip_geojson(zip_path, self.data_dir)
        _say(f"ingest {slug} from {geojson.name} ({geojson.stat().st_size:,} bytes)")
        conn.execute("DELETE FROM building_footprints WHERE state = ?", [slug])
        last_err: Optional[Exception] = None
        for geom in ("geom", "geometry"):
            colocator = colocator_sql(
                f"ST_Y(ST_Centroid({geom}))",
                f"ST_X(ST_Centroid({geom}))",
            )
            area = area_sql(geom)
            try:
                conn.execute(
                    f"""
                    INSERT INTO building_footprints
                    SELECT
                        state,
                        area_m2,
                        area_m2 * {SQFT_PER_M2} AS area_sqft,
                        centroid_lat,
                        centroid_lon,
                        colocator
                    FROM (
                        SELECT
                            ? AS state,
                            {area} AS area_m2,
                            ST_Y(ST_Centroid({geom})) AS centroid_lat,
                            ST_X(ST_Centroid({geom})) AS centroid_lon,
                            {colocator} AS colocator
                        FROM ST_Read(?)
                    )
                    """,
                    [slug, str(geojson)],
                )
                last_err = None
                break
            except duckdb.Error as exc:
                last_err = exc
                continue
        if last_err is not None:
            raise last_err
        n = int(
            conn.execute(
                "SELECT COUNT(*) FROM building_footprints WHERE state = ?",
                [slug],
            ).fetchone()[0]
        )
        conn.execute("CHECKPOINT")
        try:
            geojson.unlink()
        except OSError as exc:
            log_warning(f"could not remove {geojson}: {exc}")
        marker["states"][slug] = {
            "rows": n,
            "zip_size": zip_size,
            "version": INGEST_VERSION,
        }
        _save_marker(self.data_dir, marker)
        _say(f"{slug}: {n:,} buildings")
        return n

    def _rebuild_colocator(self, conn: duckdb.DuckDBPyConnection) -> None:
        _say("rebuild building_colocator")
        conn.execute(
            """
            CREATE OR REPLACE TABLE building_colocator AS
            SELECT
                colocator,
                COUNT(*) AS n_buildings,
                MIN(area_sqft) AS min_area_sqft,
                AVG(area_sqft) AS avg_area_sqft,
                MAX(area_sqft) AS max_area_sqft,
                SUM(area_sqft) AS sum_area_sqft
            FROM building_footprints
            GROUP BY 1
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_building_colocator ON building_colocator(colocator)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_building_footprints_colocator "
            "ON building_footprints(colocator)"
        )


def main() -> None:
    import argparse

    p = argparse.ArgumentParser(description="Microsoft US Building Footprints sidecar")
    p.add_argument("--db", default=str(default_db_path()))
    p.add_argument("--data-dir", default=str(default_data_dir()))
    p.add_argument("--states", default=os.environ.get("BUILDING_STATES", ""))
    p.add_argument("--download-only", action="store_true")
    p.add_argument("--no-download", action="store_true")
    args = p.parse_args()
    proc = BuildingFootprintsProcessor(
        db_path=args.db,
        data_dir=Path(args.data_dir),
        download=not args.no_download,
    )
    slugs = parse_states(args.states)
    if args.download_only:
        proc.download_zips(slugs)
        return
    stats = proc.run(slugs)
    for k, v in stats.items():
        print(f"{k}: {v:,}")


if __name__ == "__main__":
    main()
