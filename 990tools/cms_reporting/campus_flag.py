#!/usr/bin/env python3
"""Campus leftover flag for mill pins: OSM hospital or a tall neighbor.

A small Microsoft footprint on a hospital campus (75 Francis, Hackensack
cancer center) is the leftover mill pattern. A one-story medical park on
Doctor Cir is not. Height under ~20 m without a hospital tag stays quiet
(parking garages sit in that band).
"""

from __future__ import annotations

import csv
import json
import math
import re
import urllib.request
from pathlib import Path
from typing import Any, Callable, Iterable

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
RADIUS_M = 80
TALL_M = 20.0
TALL_LEVELS = 8
USER_AGENT = "990tools mill campus flag"

_HOSPITAL_BUILDING = {"hospital"}
_HOSPITAL_AMENITY = {"hospital"}
_HOSPITAL_HEALTHCARE = {"hospital"}
_HOSPITAL_CLASS = {"hospital"}

_HEIGHT_RE = re.compile(
    r"^\s*([0-9]+(?:\.[0-9]+)?)\s*(m|meter|meters|ft|feet|')?\s*$",
    re.I,
)


def dist_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    dy = (lat1 - lat2) * 111000
    dx = (lon1 - lon2) * 111000 * math.cos(math.radians((lat1 + lat2) / 2))
    return math.hypot(dx, dy)


def parse_height_m(raw: Any) -> float | None:
    if raw is None:
        return None
    text = str(raw).strip().split(";")[0].strip()
    if not text:
        return None
    m = _HEIGHT_RE.match(text)
    if not m:
        return None
    n = float(m.group(1))
    unit = (m.group(2) or "m").lower()
    if unit in ("ft", "feet", "'"):
        return n * 0.3048
    return n


def parse_levels(raw: Any) -> float | None:
    if raw is None:
        return None
    text = str(raw).strip().split(";")[0].strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _is_hospital_tags(tags: dict) -> bool:
    bldg = str(tags.get("building") or "").lower()
    amenity = str(tags.get("amenity") or "").lower()
    health = str(tags.get("healthcare") or "").lower()
    return (
        bldg in _HOSPITAL_BUILDING
        or amenity in _HOSPITAL_AMENITY
        or health in _HOSPITAL_HEALTHCARE
    )


def _is_tall_tags(tags: dict) -> bool:
    levels = parse_levels(tags.get("building:levels") or tags.get("levels"))
    if levels is not None and levels >= TALL_LEVELS:
        return True
    h = parse_height_m(tags.get("height"))
    return h is not None and h >= TALL_M


def _ll_key(lat: float, lon: float) -> tuple[float, float]:
    return (round(float(lat), 4), round(float(lon), 4))


def _nearest_height(
    lat: float,
    lon: float,
    height_by_ll: dict[tuple[float, float], dict],
    max_m: float = RADIUS_M,
) -> dict | None:
    exact = height_by_ll.get(_ll_key(lat, lon))
    if exact:
        return exact
    best = None
    best_d = 1e9
    for (hlat, hlon), row in height_by_ll.items():
        d = dist_m(lat, lon, hlat, hlon)
        if d < best_d:
            best_d = d
            best = row
    if best is not None and best_d <= max_m:
        return best
    return None


def load_height_tsv(path: Path) -> dict[tuple[float, float], dict]:
    """Index cms_puf_watch_height.tsv by rounded LL: cell."""
    if not path.exists():
        return {}
    out: dict[tuple[float, float], dict] = {}
    with path.open(encoding="utf-8") as f:
        reader = csv.DictReader(f, delimiter="\t")
        for row in reader:
            coloc = row.get("colocator") or ""
            parts = coloc.split(":")
            if len(parts) < 3:
                continue
            try:
                lat = float(parts[1])
                lon = float(parts[2])
            except ValueError:
                continue
            hmax = row.get("height_max_m") or ""
            hnear = row.get("height_m") or ""
            try:
                height_max = float(hmax) if hmax else None
            except ValueError:
                height_max = None
            try:
                height = float(hnear) if hnear else None
            except ValueError:
                height = None
            out[_ll_key(lat, lon)] = {
                "height_m": height,
                "height_max_m": height_max,
                "class": (row.get("class") or "").strip().lower(),
                "name": (row.get("name") or "").strip(),
            }
    return out


def classify_campus(
    *,
    osm_near: list[dict],
    height_row: dict | None = None,
) -> dict:
    """Return campus leftover flag plus a one-line why.

    ``osm_near`` items: dist_m, name, tags (dict).
    """
    reasons: list[str] = []
    hospital_hit = None
    tall_hit = None
    for hit in osm_near:
        tags = hit.get("tags") or {}
        d = hit.get("dist_m")
        name = (hit.get("name") or tags.get("name") or "").strip()
        label = name or "unnamed"
        if _is_hospital_tags(tags) and hospital_hit is None:
            hospital_hit = hit
            d_s = f"{d:.0f} m" if d is not None else "nearby"
            reasons.append(f"OSM hospital {d_s}: {label}")
        if _is_tall_tags(tags) and tall_hit is None:
            tall_hit = hit
            levels = parse_levels(tags.get("building:levels") or tags.get("levels"))
            h = parse_height_m(tags.get("height"))
            bit = f"{levels:.0f} floors" if levels else f"{h:.0f} m"
            d_s = f"{d:.0f} m" if d is not None else "nearby"
            reasons.append(f"tall OSM neighbor {d_s}: {label} ({bit})")

    hr = height_row or {}
    cls = str(hr.get("class") or "").lower()
    hmax = hr.get("height_max_m")
    if cls in _HOSPITAL_CLASS:
        nm = hr.get("name") or "hospital"
        reasons.append(f"Overture class hospital: {nm}")
    if hmax is not None:
        try:
            hmax_f = float(hmax)
        except (TypeError, ValueError):
            hmax_f = None
        if hmax_f is not None and hmax_f >= TALL_M:
            reasons.append(f"Overture neighbor {hmax_f:.0f} m tall")

    campus = bool(reasons)
    why = "; ".join(reasons) if reasons else ""
    return {"campus": campus, "why": why}


def overpass_ql(pins: Iterable[tuple[float, float]], radius_m: int = RADIUS_M) -> str:
    parts = []
    for lat, lon in pins:
        parts.append(f'way(around:{radius_m},{lat},{lon})["building"];')
        parts.append(f'relation(around:{radius_m},{lat},{lon})["building"];')
    return (
        "[out:json][timeout:90];\n(\n"
        + "\n".join(parts)
        + "\n);\nout tags center;"
    )


def _element_ll(el: dict) -> tuple[float, float] | None:
    if "center" in el:
        return float(el["center"]["lat"]), float(el["center"]["lon"])
    if "lat" in el and "lon" in el:
        return float(el["lat"]), float(el["lon"])
    return None


def assign_osm_hits(
    pins: list[tuple[str, float, float]],
    elements: list[dict],
    radius_m: int = RADIUS_M,
) -> dict[str, list[dict]]:
    """Map colocator -> OSM hits within radius_m."""
    out: dict[str, list[dict]] = {c: [] for c, _, _ in pins}
    parsed = []
    for el in elements:
        ll = _element_ll(el)
        if ll is None:
            continue
        tags = el.get("tags") or {}
        parsed.append((ll[0], ll[1], tags, el.get("type"), el.get("id")))
    for coloc, lat, lon in pins:
        hits = []
        for elat, elon, tags, typ, eid in parsed:
            d = dist_m(lat, lon, elat, elon)
            if d <= radius_m:
                hits.append(
                    {
                        "dist_m": d,
                        "tags": tags,
                        "name": tags.get("name") or "",
                        "type": typ,
                        "id": eid,
                    }
                )
        hits.sort(key=lambda h: h["dist_m"])
        out[coloc] = hits
    return out


def fetch_overpass(
    pins: list[tuple[str, float, float]],
    *,
    opener: Callable[[str], dict] | None = None,
    radius_m: int = RADIUS_M,
) -> dict[str, list[dict]]:
    """Live Overpass pull. ``opener(ql) -> parsed JSON`` is injectable."""
    ll = [(lat, lon) for _, lat, lon in pins]
    ql = overpass_ql(ll, radius_m=radius_m)
    if opener is not None:
        data = opener(ql)
    else:
        req = urllib.request.Request(
            OVERPASS_URL,
            data=ql.encode(),
            headers={"User-Agent": USER_AGENT, "Content-Type": "text/plain"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.load(resp)
    return assign_osm_hits(pins, data.get("elements") or [], radius_m=radius_m)


def attach_campus(
    mills: list[dict],
    *,
    parse_ll: Callable[[Any], tuple[float | None, float | None]],
    osm_by_coloc: dict[str, list[dict]] | None = None,
    height_by_ll: dict[tuple[float, float], dict] | None = None,
) -> list[dict]:
    """Set campus / campus_why on each mill dict (in place) and return it."""
    osm_by_coloc = osm_by_coloc or {}
    height_by_ll = height_by_ll or {}
    for mill in mills:
        coloc = mill.get("colocator")
        lat, lon = parse_ll(coloc)
        hrow = None
        if lat is not None and lon is not None:
            hrow = _nearest_height(lat, lon, height_by_ll)
        flag = classify_campus(
            osm_near=osm_by_coloc.get(str(coloc), []),
            height_row=hrow,
        )
        mill["campus"] = flag["campus"]
        mill["campus_why"] = flag["why"]
    return mills
