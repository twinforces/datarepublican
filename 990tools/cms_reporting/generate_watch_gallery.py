#!/usr/bin/env python3
"""HTML gallery for cms_puf_npi_score — a watch list, not a fraud determination.

  python3 -u cms_reporting/generate_watch_gallery.py \
      --db-path /Volumes/Data/final/irs990.duckdb
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import duckdb

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent
DOT_TEMPLATES = ROOT / "dot_reporting" / "templates"
DEFAULT_DB = "/Volumes/Data/final/irs990.duckdb"
# Fun extras and master_index read this tree (same root as Rogues).
DEFAULT_OUT = ROOT / "dot_reporting" / "reports" / "cms_puf_watch"
TZ = ZoneInfo("America/Los_Angeles")

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "spending_reporting") not in sys.path:
    sys.path.insert(0, str(ROOT / "spending_reporting"))
if str(ROOT / "dot_reporting") not in sys.path:
    sys.path.insert(0, str(ROOT / "dot_reporting"))

from generate_galleries import (  # noqa: E402
    dumps_table_json,
    leaflet_embed_html,
    tanstack_assets_html,
    ts_payload,
)
from html_format import esc, linkify_zip_codes  # noqa: E402

DISCLAIMER = (
    "This is a watch list for a human. It is not a fraud determination. "
    "Facility rounding, therapy units, and hospital campuses are why a raw "
    "hours sort is the wrong inbox."
)

# Hover copy for dotted terms. Same pattern as Rogues (`TERMS` + `th[title]`).
TERMS = {
    "people": (
        "People on the mill watch list at this pin: billed more than 2,080 "
        "office hours from a building under 3,000 square feet."
    ),
    "stack": (
        "How many people at this pin billed more than 2,080 office hours — "
        "one full-time clinician year of office work. Can be larger than "
        "People when someone in the stack is not mill-watch."
    ),
    "sqft": (
        "Largest nearby building footprint, in square feet, within a few "
        "geocode cells of this pin. Under 3,000 is the mill cut; under 4,000 "
        "is the solo cut."
    ),
    "hours": (
        "Timed hours billed with place of service office, not facility. "
        "Facility hours happened at a hospital or nursing home, not this pin."
    ),
    "priority": (
        "Rank for a human inbox. How far office hours sit above the specialty "
        "90th percentile, plus mill stack, plus a bonus for a small building. "
        "Higher is more worth a look, not a verdict."
    ),
    "vs_p90": (
        "This person's office hours divided by the 90th percentile of office "
        "hours for the same specialty. 2.0× means twice a busy peer."
    ),
    "npi": (
        "National Provider Identifier, the public CMS ID for this clinician. "
        "Opens the NPPES registry record."
    ),
    "type": "Specialty on the CMS 2024 Physician file.",
    "flag": (
        "mill = stacked at a small pin. solo = one person far above their "
        "specialty 90th percentile at a small site. Both can be true."
    ),
    "mill": (
        "Five or more people billed more than 2,080 office hours from a "
        "building under 3,000 square feet, at a pin that is one city."
    ),
    "solo": (
        "One person, mostly office, at least twice their specialty 90th "
        "percentile, in a building under 4,000 square feet. Therapy, "
        "optometry, and labs are dropped."
    ),
    "nppes": (
        "Practice location from the National Plan and Provider Enumeration "
        "System, rounded to about 11 meters."
    ),
    "colocator": (
        "LL:latitude:longitude at four decimal places (~11 meters). The pin "
        "NPPES practice addresses share when they round to the same cell."
    ),
    "pins": "Distinct LL: cells that qualify as mills.",
    "campus": (
        "OSM has a hospital within 80 meters, or a neighbor about six stories "
        "(20 m) tall. That is the Brigham / cancer-center scrap-footprint "
        "pattern. A one-story medical park stays unflagged."
    ),
}

PAGE_CSS = """
body { font-family: ui-sans-serif, system-ui, sans-serif; margin: 1.5rem auto;
       max-width: 1200px; color: #111; line-height: 1.45; }
nav a { margin-right: 1rem; }
table { border-collapse: collapse; width: 100%; font-size: 0.9rem; }
th, td { border: 1px solid #ddd; padding: 0.4rem 0.5rem; text-align: left;
         vertical-align: top; }
th { background: #f4f4f4; position: sticky; top: 0; }
th[title] { text-decoration: underline dotted; text-underline-offset: 0.18em;
            cursor: help; }
tr:nth-child(even) { background: #fafafa; }
.meta { color: #4b5563; font-size: 0.9rem; }
.num { text-align: right; font-variant-numeric: tabular-nums; }
.cards { display: flex; flex-wrap: wrap; gap: 0.75rem; margin: 1rem 0; }
.card { border: 1px solid #e5e7eb; border-radius: 8px; padding: 0.75rem 1rem;
        min-width: 8rem; }
.card strong { display: block; font-size: 1.25rem; }
.card span { color: #4b5563; font-size: 0.85rem; }
.callout { border-left: 4px solid #b45309; background: #fffbeb; padding: 0.75rem 1rem; }
.leaflet-map-section h2 { font-size: 1.05rem; margin: 1rem 0 0.35rem; }
.term { border-bottom: 1px dotted currentColor; cursor: help; }
.campus-flag { color: #9a3412; font-weight: 600; }
"""


def generated_stamp() -> str:
    return datetime.now(TZ).strftime("%Y-%m-%d %H:%M %Z")


def parse_ll(colocator: str | None) -> tuple[float | None, float | None]:
    raw = str(colocator or "")
    if not raw.upper().startswith("LL:"):
        return None, None
    parts = raw.split(":")
    if len(parts) < 3:
        return None, None
    try:
        return float(parts[1]), float(parts[2])
    except ValueError:
        return None, None


def mill_filename(colocator: str) -> str:
    slug = (
        str(colocator)
        .lower()
        .replace(":", "-")
        .replace(".", "-")
        .replace("/", "-")[:80]
    )
    return f"mill-{slug}.html"


def nppes_url(npi) -> str:
    digits = "".join(ch for ch in str(npi or "") if ch.isdigit())
    return f"https://npiregistry.cms.hhs.gov/provider-view/{digits}"


def maps_url(lat, lon) -> str:
    if lat is None or lon is None:
        return ""
    return f"https://www.google.com/maps/search/?api=1&query={lat},{lon}"


def fmt_num(v, *, digits: int = 0) -> str:
    if v is None:
        return "—"
    try:
        n = float(v)
    except (TypeError, ValueError):
        return "—"
    if digits == 0:
        return f"{n:,.0f}"
    return f"{n:,.{digits}f}"


def fmt_times(v) -> str:
    if v is None:
        return "—"
    try:
        return f"{float(v):.1f}×"
    except (TypeError, ValueError):
        return "—"


def person_name(row: dict) -> str:
    last = str(row.get("name") or "").strip()
    first = str(row.get("first_name") or "").strip()
    if last and first:
        return f"{last}, {first}"
    return last or first or "—"


def nav_html() -> str:
    return (
        "<nav>"
        '<a href="../master_index.html">All reports</a>'
        '<a href="index.html">Mills</a>'
        '<a href="solo.html">Solo outliers</a>'
        "</nav>"
    )


def term(label: str, key: str) -> str:
    return f'<span class="term" title="{esc(TERMS[key])}">{esc(label)}</span>'


def _col(
    col_id: str, header: str, *, key: str | None = None, sortable: bool = True
) -> dict:
    d: dict = {"id": col_id, "header": header}
    if key:
        d["title"] = TERMS[key]
    if not sortable:
        d["sortable"] = False
    return d


def page(
    title: str,
    blurb: str,
    out: Path,
    *,
    extra_html: str = "",
    payload: dict | None = None,
    noscript_rows: list[list] | None = None,
    headers: list[str] | None = None,
    header_keys: list[str | None] | None = None,
    cards: str = "",
) -> None:
    table = ""
    if headers and noscript_rows is not None:
        keys = header_keys or [None] * len(headers)
        ths = []
        for h, k in zip(headers, keys):
            if k:
                ths.append(f'<th title="{esc(TERMS[k])}">{esc(h)}</th>')
            else:
                ths.append(f"<th>{esc(h)}</th>")
        thead = "".join(ths)
        body = []
        for row in noscript_rows:
            tds = "".join(f"<td>{c}</td>" for c in row)
            body.append(f"<tr>{tds}</tr>")
        table = (
            f"<table><thead><tr>{thead}</tr></thead>"
            f"<tbody>{''.join(body)}</tbody></table>"
        )
    extra_foot = ""
    table_block = table
    if payload:
        extra_foot = (
            f"<script>window.__CLUSTER_TABLE__ = {dumps_table_json(payload)};</script>\n"
            + tanstack_assets_html()
        )
        table_block = (
            '<div id="ts-table-root"></div>\n'
            f"<noscript>\n{table}\n</noscript>"
        )
    html_out = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)}</title>
<style>{PAGE_CSS}</style></head><body>
{nav_html()}
<h1>{esc(title)}</h1>
<p class="callout">{esc(DISCLAIMER)}</p>
<p>{blurb}</p>
<p class="meta">Generated {esc(generated_stamp())}. CMS 2024 Physician file.
Click a column header to sort.</p>
{cards}
{extra_html}
{table_block}
{extra_foot}
</body></html>
"""
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html_out, encoding="utf-8")


def connect(db_path: str) -> duckdb.DuckDBPyConnection:
    return duckdb.connect(
        db_path,
        read_only=True,
        config={"memory_limit": "8GB", "threads": "2"},
    )


MILL_SQL = """
SELECT
    colocator,
    arg_max(street, n_mill) AS street,
    arg_max(city, n_mill) AS city,
    arg_max(state, n_mill) AS state,
    arg_max(zip, n_mill) AS zip,
    SUM(n_mill)::BIGINT AS n_mill,
    MAX(npi_high_at_cell)::BIGINT AS npi_high_at_cell,
    MIN(max_area_near) AS max_area_near,
    SUM(hours_office) AS hours_office,
    MAX(max_priority) AS max_priority
FROM cms_puf_watch_cells
WHERE n_mill > 0
GROUP BY 1
ORDER BY SUM(n_mill) DESC, MAX(max_priority) DESC
"""

SOLO_SQL = """
SELECT
    npi, name, first_name, provider_type, city, state, zip, street, colocator,
    hours_office, office_vs_p90, max_area_near, npi_high_at_cell,
    hours_em_office, watch_mill, priority
FROM cms_puf_npi_score
WHERE watch_solo
ORDER BY priority DESC
"""

CELL_NPI_SQL = """
SELECT
    npi, name, first_name, provider_type, city, state, zip, street,
    hours_office, hours_facility, office_share, office_vs_p90,
    max_area_near, watch_solo, watch_mill, priority
FROM cms_puf_npi_score
WHERE colocator = ?
  AND watch
ORDER BY priority DESC, hours_office DESC
"""


def fetch_dicts(con, sql: str, params=None) -> list[dict]:
    cur = con.execute(sql, params or [])
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def mill_map_points(mills: list[dict]) -> list[dict]:
    pts = []
    for m in mills:
        lat, lon = parse_ll(m.get("colocator"))
        if lat is None:
            continue
        href = mill_filename(m["colocator"])
        maps = maps_url(lat, lon)
        street = esc(m.get("street") or "")
        city = esc(m.get("city") or "")
        st = esc(m.get("state") or "")
        campus_bit = ""
        if m.get("campus"):
            campus_bit = (
                f"<br>{term('campus leftover', 'campus')}: "
                f"{esc(m.get('campus_why') or '')}"
            )
        pts.append(
            {
                "lat": lat,
                "lon": lon,
                "metric": float(m.get("n_mill") or 0),
                "kind": "ll",
                "href": href,
                "popup_html": (
                    f"<strong>{street}</strong><br>{city} {st}<br>"
                    f"{int(m.get('n_mill') or 0)} people · "
                    f"{fmt_num(m.get('max_area_near'))} sqft"
                    f"{campus_bit}"
                    f'<br><a href="{esc(href)}">Detail →</a>'
                    f' · <a href="{esc(maps)}" target="_blank" rel="noopener">map</a>'
                ),
            }
        )
    return pts


def mill_payload(mills: list[dict]) -> dict:
    columns = [
        _col("n_mill", "People", key="people"),
        _col("npi_high", "Office-year stack", key="stack"),
        _col("sqft", "Sqft", key="sqft"),
        _col("hours", "Office hours", key="hours"),
        _col("priority", "Priority", key="priority"),
        _col("campus", "Campus", key="campus"),
        _col("street", "Street"),
        _col("city", "City"),
        _col("state", "St"),
        _col("zip", "ZIP"),
        _col("detail", "Detail", sortable=False),
    ]
    rows = []
    noscript = []
    for m in mills:
        href = mill_filename(m["colocator"])
        lat, lon = parse_ll(m.get("colocator"))
        maps = maps_url(lat, lon)
        street = m.get("street") or ""
        street_html = linkify_zip_codes(street)
        if maps:
            street_html += (
                f' · <a href="{esc(maps)}" target="_blank" rel="noopener">map</a>'
            )
        detail = f'<a href="{esc(href)}">Detail →</a>'
        campus = bool(m.get("campus"))
        if campus:
            campus_sort = 1
            why = m.get("campus_why") or TERMS["campus"]
            campus_html = (
                f'<span class="campus-flag term" title="{esc(why)}">'
                "campus leftover</span>"
            )
        else:
            campus_sort = 0
            campus_html = "—"
        rows.append(
            {
                "n_mill": int(m.get("n_mill") or 0),
                "npi_high": int(m.get("npi_high_at_cell") or 0),
                "sqft": float(m.get("max_area_near") or 0),
                "sqft_html": fmt_num(m.get("max_area_near")),
                "hours": float(m.get("hours_office") or 0),
                "hours_html": fmt_num(m.get("hours_office")),
                "priority": float(m.get("max_priority") or 0),
                "priority_html": fmt_num(m.get("max_priority"), digits=1),
                "campus": campus_sort,
                "campus_html": campus_html,
                "street": street,
                "street_html": street_html,
                "city": m.get("city") or "",
                "state": m.get("state") or "",
                "zip": m.get("zip") or "",
                "detail": "Detail",
                "detail_html": detail,
            }
        )
        noscript.append(
            [
                int(m.get("n_mill") or 0),
                int(m.get("npi_high_at_cell") or 0),
                fmt_num(m.get("max_area_near")),
                fmt_num(m.get("hours_office")),
                fmt_num(m.get("max_priority"), digits=1),
                campus_html,
                street_html,
                esc(m.get("city") or ""),
                esc(m.get("state") or ""),
                esc(m.get("zip") or ""),
                detail,
            ]
        )
    return ts_payload(rows, columns, "n_mill"), noscript


def solo_payload(solos: list[dict]) -> dict:
    columns = [
        _col("priority", "Priority", key="priority"),
        _col("person", "Name"),
        _col("provider_type", "Type", key="type"),
        _col("hours", "Office hours", key="hours"),
        _col("vs_p90", "vs specialty p90", key="vs_p90"),
        _col("sqft", "Sqft", key="sqft"),
        _col("city", "City"),
        _col("state", "St"),
        _col("npi", "NPI", key="npi"),
    ]
    rows = []
    noscript = []
    for s in solos:
        lat, lon = parse_ll(s.get("colocator"))
        maps = maps_url(lat, lon)
        name = person_name(s)
        mill_href = mill_filename(s["colocator"]) if s.get("watch_mill") else ""
        name_html = esc(name)
        if mill_href:
            name_html = f'<a href="{esc(mill_href)}">{esc(name)}</a>'
        elif maps:
            name_html = f'<a href="{esc(maps)}" target="_blank" rel="noopener">{esc(name)}</a>'
        npi = s.get("npi")
        npi_html = (
            f'<a href="{esc(nppes_url(npi))}" target="_blank" rel="noopener">{esc(npi)}</a>'
        )
        rows.append(
            {
                "priority": float(s.get("priority") or 0),
                "priority_html": fmt_num(s.get("priority"), digits=1),
                "person": name,
                "person_html": name_html,
                "provider_type": s.get("provider_type") or "",
                "hours": float(s.get("hours_office") or 0),
                "hours_html": fmt_num(s.get("hours_office")),
                "vs_p90": float(s.get("office_vs_p90") or 0),
                "vs_p90_html": fmt_times(s.get("office_vs_p90")),
                "sqft": float(s.get("max_area_near") or 0),
                "sqft_html": fmt_num(s.get("max_area_near")),
                "city": s.get("city") or "",
                "state": s.get("state") or "",
                "npi": str(npi or ""),
                "npi_html": npi_html,
            }
        )
        noscript.append(
            [
                fmt_num(s.get("priority"), digits=1),
                name_html,
                esc(s.get("provider_type") or ""),
                fmt_num(s.get("hours_office")),
                fmt_times(s.get("office_vs_p90")),
                fmt_num(s.get("max_area_near")),
                esc(s.get("city") or ""),
                esc(s.get("state") or ""),
                npi_html,
            ]
        )
    return ts_payload(rows, columns, "priority"), noscript


def npi_payload(npis: list[dict]) -> dict:
    columns = [
        _col("priority", "Priority", key="priority"),
        _col("person", "Name"),
        _col("provider_type", "Type", key="type"),
        _col("hours", "Office hours", key="hours"),
        _col("vs_p90", "vs specialty p90", key="vs_p90"),
        _col("kind", "Flag", key="flag"),
        _col("street", "Street"),
        _col("npi", "NPI", key="npi"),
    ]
    rows = []
    noscript = []
    for s in npis:
        name = person_name(s)
        flags = []
        flag_html = []
        if s.get("watch_mill"):
            flags.append("mill")
            flag_html.append(term("mill", "mill"))
        if s.get("watch_solo"):
            flags.append("solo")
            flag_html.append(term("solo", "solo"))
        kind = " + ".join(flags) or "watch"
        kind_html = " + ".join(flag_html) or esc(kind)
        npi = s.get("npi")
        npi_html = (
            f'<a href="{esc(nppes_url(npi))}" target="_blank" rel="noopener">{esc(npi)}</a>'
        )
        rows.append(
            {
                "priority": float(s.get("priority") or 0),
                "priority_html": fmt_num(s.get("priority"), digits=1),
                "person": name,
                "person_html": esc(name),
                "provider_type": s.get("provider_type") or "",
                "hours": float(s.get("hours_office") or 0),
                "hours_html": fmt_num(s.get("hours_office")),
                "vs_p90": float(s.get("office_vs_p90") or 0),
                "vs_p90_html": fmt_times(s.get("office_vs_p90")),
                "kind": kind,
                "kind_html": kind_html,
                "street": s.get("street") or "",
                "npi": str(npi or ""),
                "npi_html": npi_html,
            }
        )
        noscript.append(
            [
                fmt_num(s.get("priority"), digits=1),
                esc(name),
                esc(s.get("provider_type") or ""),
                fmt_num(s.get("hours_office")),
                fmt_times(s.get("office_vs_p90")),
                kind_html,
                esc(s.get("street") or ""),
                npi_html,
            ]
        )
    return ts_payload(rows, columns, "priority"), noscript


def cards_html(items: list[tuple]) -> str:
    bits = []
    for item in items:
        value, label = item[0], item[1]
        key = item[2] if len(item) > 2 else None
        lab = term(label, key) if key else f"<span>{esc(label)}</span>"
        bits.append(f'<div class="card"><strong>{esc(value)}</strong>{lab}</div>')
    return '<div class="cards">' + "".join(bits) + "</div>"


def write_mills(mills: list[dict], out_dir: Path) -> None:
    payload, noscript = mill_payload(mills)
    points = mill_map_points(mills)
    extra = leaflet_embed_html(
        points,
        heading="Mill pins",
        caption=(
            "Marker size is people on the watch list at that pin. "
            "Double-click opens the detail page."
        ),
    )
    page(
        "Medicare physician mills",
        (
            "Five or more people billed more than 2,080 "
            f"{term('office hours', 'hours')} from a building under 3,000 "
            "square feet. "
            f"{term('Campus leftover', 'campus')} means OSM has a hospital "
            "within 80 meters, or a neighbor about six stories tall — the "
            "Brigham / cancer-center scrap-footprint pattern. A one-story "
            "medical park on Doctor Cir stays unflagged."
        ),
        out_dir / "index.html",
        extra_html=extra,
        payload=payload,
        noscript_rows=noscript,
        headers=[
            "People",
            "Office-year stack",
            "Sqft",
            "Office hours",
            "Priority",
            "Campus",
            "Street",
            "City",
            "St",
            "ZIP",
            "Detail",
        ],
        header_keys=[
            "people",
            "stack",
            "sqft",
            "hours",
            "priority",
            "campus",
            None,
            None,
            None,
            None,
            None,
        ],
        cards=cards_html(
            [
                (str(len(mills)), "pins", "pins"),
                (
                    str(sum(int(m.get("n_mill") or 0) for m in mills)),
                    "people",
                    "people",
                ),
                (
                    str(sum(1 for m in mills if m.get("campus"))),
                    "campus leftover",
                    "campus",
                ),
            ]
        ),
    )


def write_solos(solos: list[dict], out_dir: Path) -> None:
    payload, noscript = solo_payload(solos)
    page(
        "Medicare physician solo outliers",
        (
            "One person whose office hours are at least twice their specialty’s "
            f"{term('90th percentile', 'vs_p90')}, in a building under 4,000 "
            "square feet. Therapy, optometry, and labs are already dropped. "
            "A name on this list is a reason to look, not a verdict."
        ),
        out_dir / "solo.html",
        payload=payload,
        noscript_rows=noscript,
        headers=[
            "Priority",
            "Name",
            "Type",
            "Office hours",
            "vs specialty p90",
            "Sqft",
            "City",
            "St",
            "NPI",
        ],
        header_keys=[
            "priority",
            None,
            "type",
            "hours",
            "vs_p90",
            "sqft",
            None,
            None,
            "npi",
        ],
        cards=cards_html([(f"{len(solos):,}", "solo watch", "solo")]),
    )


def write_mill_detail(mill: dict, npis: list[dict], out_dir: Path) -> None:
    lat, lon = parse_ll(mill.get("colocator"))
    maps = maps_url(lat, lon)
    street = mill.get("street") or mill.get("colocator")
    title = f"{street}, {mill.get('city') or ''} {mill.get('state') or ''}".strip()
    payload, noscript = npi_payload(npis)
    points = []
    if lat is not None:
        points.append(
            {
                "lat": lat,
                "lon": lon,
                "metric": float(mill.get("n_mill") or 1),
                "kind": "ll",
                "href": maps,
                "popup_html": f"<strong>{esc(street)}</strong>",
            }
        )
    extra = leaflet_embed_html(
        points,
        heading="Pin",
        caption=(
            f"{term('NPPES practice pin', 'nppes')}. "
            "Double-click opens Google Maps."
        ),
    )
    map_link = (
        f'<p class="meta"><a href="{esc(maps)}" target="_blank" rel="noopener">Open in Google Maps</a></p>'
        if maps
        else ""
    )
    streets = sorted({str(n.get("street") or "") for n in npis if n.get("street")})
    variants = ""
    if len(streets) > 1:
        variants = "<p class='meta'>Street spellings at this pin: " + "; ".join(
            esc(s) for s in streets
        ) + "</p>"
    campus_blurb = ""
    campus_cards: list[tuple] = []
    if mill.get("campus"):
        campus_blurb = (
            f" {term('Campus leftover', 'campus')}: "
            f"{esc(mill.get('campus_why') or '')}."
        )
        campus_cards.append(("yes", "campus leftover", "campus"))
    page(
        title,
        (
            f"{int(mill.get('n_mill') or 0)} people on the mill watch list at "
            f"<code class='term' title=\"{esc(TERMS['colocator'])}\">"
            f"{esc(mill.get('colocator'))}</code>. Nearby building "
            f"{fmt_num(mill.get('max_area_near'))} square feet."
            f"{campus_blurb}"
        ),
        out_dir / mill_filename(mill["colocator"]),
        extra_html=extra + map_link + variants,
        payload=payload,
        noscript_rows=noscript,
        headers=[
            "Priority",
            "Name",
            "Type",
            "Office hours",
            "vs specialty p90",
            "Flag",
            "Street",
            "NPI",
        ],
        header_keys=[
            "priority",
            None,
            "type",
            "hours",
            "vs_p90",
            "flag",
            None,
            "npi",
        ],
        cards=cards_html(
            [
                (str(int(mill.get("n_mill") or 0)), "mill people", "people"),
                (fmt_num(mill.get("max_area_near")), "sqft nearby", "sqft"),
                (fmt_num(mill.get("hours_office")), "office hours", "hours"),
                (
                    str(int(mill.get("npi_high_at_cell") or 0)),
                    "office-year stack",
                    "stack",
                ),
                *campus_cards,
            ]
        ),
    )


def generate(
    db_path: str,
    out_dir: Path,
    *,
    osm: bool = True,
    height_tsv: Path | None = None,
) -> dict:
    from cms_reporting.campus_flag import (  # noqa: PLC0415
        attach_campus,
        fetch_overpass,
        load_height_tsv,
    )

    n_campus = 0
    con = connect(db_path)
    try:
        mills = fetch_dicts(con, MILL_SQL)
        solos = fetch_dicts(con, SOLO_SQL)
        height_by_ll = load_height_tsv(height_tsv) if height_tsv else {}
        osm_by_coloc: dict = {}
        if osm and mills:
            pins = []
            for m in mills:
                lat, lon = parse_ll(m.get("colocator"))
                if lat is None:
                    continue
                pins.append((str(m["colocator"]), lat, lon))
            try:
                osm_by_coloc = fetch_overpass(pins)
                print(
                    f"campus flag: OSM hits on {sum(1 for v in osm_by_coloc.values() if v)}/"
                    f"{len(pins)} pins",
                    flush=True,
                )
            except Exception as exc:  # noqa: BLE001 — gallery still builds
                print(f"campus flag: Overpass failed ({exc}); OSM hospital off", flush=True)
        attach_campus(
            mills,
            parse_ll=parse_ll,
            osm_by_coloc=osm_by_coloc,
            height_by_ll=height_by_ll,
        )
        n_campus = sum(1 for m in mills if m.get("campus"))
        write_mills(mills, out_dir)
        write_solos(solos, out_dir)
        for mill in mills:
            npis = fetch_dicts(con, CELL_NPI_SQL, [mill["colocator"]])
            write_mill_detail(mill, npis, out_dir)
    finally:
        con.close()
    meta = {
        "generated_at": generated_stamp(),
        "mills": len(mills),
        "solos": len(solos),
        "campus": n_campus,
        "disclaimer": DISCLAIMER,
    }
    (out_dir / "export_metadata.json").write_text(
        json.dumps(meta, indent=2) + "\n", encoding="utf-8"
    )
    print(
        f"cms_puf_watch: {len(mills)} mill pins, {len(solos)} solo, "
        f"{n_campus} campus leftover, out={out_dir}",
        flush=True,
    )
    return meta


def main() -> int:
    p = argparse.ArgumentParser(description="CMS PUF watch gallery (not a fraud determination)")
    p.add_argument("--db-path", default=DEFAULT_DB)
    p.add_argument("--output-dir", type=Path, default=DEFAULT_OUT)
    p.add_argument(
        "--no-osm",
        action="store_true",
        help="Skip Overpass; still apply Overture height TSV if present.",
    )
    p.add_argument(
        "--height-tsv",
        type=Path,
        default=ROOT / "_output" / "cms_puf_watch_height.tsv",
        help="Optional Overture height sidecar from the mill-pin probe.",
    )
    args = p.parse_args()
    generate(
        args.db_path,
        args.output_dir,
        osm=not args.no_osm,
        height_tsv=args.height_tsv,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
