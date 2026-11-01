#!/usr/bin/env python3
"""PPP leftover / not-yet-paid / forgiven / payroll galleries, plus federal $ on SAM pins.

  python3 -u spending_reporting/generate_galleries.py \
      --db-path /Volumes/Data/final/irs990.duckdb
"""

from __future__ import annotations

import argparse
import html
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import duckdb

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_DB = "/Volumes/Data/final/irs990.duckdb"
DEFAULT_OUT = SCRIPT_DIR / "reports"
NOT_YET_PAID_STATUS = "Exemption 4"
NOT_YET_PAID_LABEL = "Not yet paid"
TOP_N = 200
# Table: SBA's large-loan public-file line. Map is the same ranked rows (DOT leaflet widget).
FORGIVEN_MIN_APPROVED = 150_000
TZ = ZoneInfo("America/Los_Angeles")
DOT_TEMPLATES = SCRIPT_DIR.parent / "dot_reporting" / "templates"
PPP_GEO_JOIN = """
LEFT JOIN Addresses a
  ON a.owner_id = p.id AND a.address_type = 'ppp_borrower'
LEFT JOIN Geocoding g ON g.geocoding_id = a.geocoding_id
"""


def esc(s) -> str:
    return html.escape("" if s is None else str(s), quote=True)


def money(v) -> str:
    if v is None:
        return "—"
    try:
        n = float(v)
    except (TypeError, ValueError):
        return "—"
    if abs(n) >= 1e9:
        return f"${n/1e9:,.2f}B"
    if abs(n) >= 1e6:
        return f"${n/1e6:,.1f}M"
    return f"${n:,.0f}"


def generated_stamp() -> str:
    return datetime.now(TZ).strftime("%Y-%m-%d %H:%M %Z")


def maps_url(lat, lon) -> str:
    if lat is None or lon is None:
        return ""
    return f"https://www.google.com/maps/search/?api=1&query={lat},{lon}"


def in_us(lat, lon) -> bool:
    try:
        lat_f, lon_f = float(lat), float(lon)
    except (TypeError, ValueError):
        return False
    return 17.0 <= lat_f <= 72.0 and -180.0 <= lon_f <= -64.0


def named(r, name_i, lat_i, lon_i) -> str:
    name = esc(r[name_i])
    href = maps_url(r[lat_i], r[lon_i]) if in_us(r[lat_i], r[lon_i]) else ""
    if href:
        return f'<a href="{esc(href)}">{name}</a>'
    return name


def map_points_from_rows(
    rows,
    *,
    name_i: int,
    city_i: int,
    st_i: int,
    metric_i: int,
    lat_i: int,
    lon_i: int,
    line2=None,
) -> list[dict]:
    pts = []
    for r in rows:
        lat, lon = r[lat_i], r[lon_i]
        if not in_us(lat, lon):
            continue
        try:
            metric = float(r[metric_i] or 0)
        except (TypeError, ValueError):
            metric = 0.0
        extra = line2(r) if line2 else ""
        pts.append({
            "lat": round(float(lat), 4),
            "lon": round(float(lon), 4),
            "metric": metric,
            "kind": "ll",
            "href": maps_url(lat, lon),
            "popup_html": (
                f"<strong>{esc(r[name_i])}</strong><br>"
                f"{esc(r[city_i])} {esc(r[st_i])}{extra}"
            ),
        })
    return pts


def leaflet_embed_html(points: list[dict], *, heading: str, caption: str,
                       height: int = 440) -> str:
    """Same Leaflet widget as DOT blanks / cluster indexes."""
    if not points:
        return "<p class='meta'>No geocoded pins for this cut.</p>"
    from mako.lookup import TemplateLookup

    payload = {
        "rootId": "leaflet-map",
        "height": int(height),
        "points": points,
        "maxZoom": 12,
    }
    js = json.dumps(payload, ensure_ascii=True, default=str).replace("</", "<\\/")
    lookup = TemplateLookup(
        directories=[str(DOT_TEMPLATES)],
        input_encoding="utf-8",
    )
    boot = lookup.get_template("partials/leaflet_map.mako").render()
    return (
        '<section class="leaflet-map-section">'
        f"<h2>{esc(heading)}</h2>"
        f'<p class="meta">{caption}</p>'
        '<div id="leaflet-map" class="leaflet-map-wrap"></div>'
        "</section>\n"
        f"<script>window.__LEAFLET_MAP__ = {js};</script>\n"
        f"{boot}\n"
    )


MAP_CAPTION = (
    "Borrower physical pin. Marker size/color = the rank metric on this page. "
    "Double-click opens Google Maps. Same Leaflet widget as the DOT galleries."
)


PAGE_CSS = """
body { font-family: Georgia, serif; margin: 1.5rem auto; max-width: 1100px;
       color: #222; line-height: 1.45; }
nav a { margin-right: 1rem; }
table { width: 100%; border-collapse: collapse; font-size: 0.9rem; }
th, td { border: 1px solid #ddd; padding: 0.4rem 0.5rem; text-align: left;
         vertical-align: top; }
th { background: #f4f4f4; position: sticky; top: 0; }
tr:nth-child(even) { background: #fafafa; }
.meta { color: #666; font-size: 0.9rem; }
.num { text-align: right; font-variant-numeric: tabular-nums; }
.leaflet-map-section h2 { font-size: 1.05rem; margin: 1rem 0 0.35rem; }
"""


def status_label(s) -> str:
    if s == NOT_YET_PAID_STATUS:
        return NOT_YET_PAID_LABEL
    return "" if s is None else str(s)


def pct_label(forgiven, approved) -> str:
    if approved in (None, 0) or forgiven is None:
        return "—"
    try:
        return f"{100.0 * float(forgiven) / float(approved):,.1f}%"
    except (TypeError, ValueError, ZeroDivisionError):
        return "—"


def tanstack_assets_html() -> str:
    """CSS+JS from the DOT Mako partial, without the Mako wrapper.

    The doc comment mentions ``<%text>`` in prose; search after ``</%doc>``
    so that mention is not treated as the open tag.
    """
    p = DOT_TEMPLATES / "partials" / "tanstack_table_assets.mako"
    text = p.read_text(encoding="utf-8")
    doc_end = text.find("</%doc>")
    search_from = doc_end + len("</%doc>") if doc_end >= 0 else 0
    start = text.find("<%text>", search_from)
    end = text.rfind("</%text>")
    if start < 0 or end <= start:
        raise RuntimeError(f"tanstack_table_assets.mako missing <%text> block: {p}")
    return text[start + len("<%text>"): end]


def dumps_table_json(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False, default=str).replace("</", "<\\/")


def ts_payload(rows: list[dict], columns: list[dict], sort_id: str,
               page_size: int = 50) -> dict:
    return {
        "rows": rows,
        "columns": columns,
        "initialSort": [{"id": sort_id, "desc": True}],
        "pageSize": int(page_size),
    }


def leftover_gallery_payload(records, *, lat_i: int = 9, lon_i: int = 10) -> dict:
    columns = [
        {"id": "borrower", "header": "Borrower"},
        {"id": "city", "header": "City"},
        {"id": "state", "header": "St"},
        {"id": "zip", "header": "ZIP"},
        {"id": "jobs_n", "header": "Jobs"},
        {"id": "approved", "header": "Approved"},
        {"id": "forgiven", "header": "Forgiven"},
        {"id": "leftover", "header": "Leftover"},
        {"id": "lender", "header": "Lender"},
    ]
    rows = []
    for r in records:
        jobs = r[4]
        rows.append(
            {
                "borrower": r[0] or "",
                "borrower_html": named(r, 0, lat_i, lon_i),
                "city": r[1] or "",
                "state": r[2] or "",
                "zip": r[3] or "",
                "jobs_n": 0 if jobs is None else int(jobs),
                "approved": float(r[5] or 0),
                "approved_html": money(r[5]),
                "forgiven": float(r[6] or 0),
                "forgiven_html": money(r[6]),
                "leftover": float(r[7] or 0),
                "leftover_html": money(r[7]),
                "lender": r[8] or "",
            }
        )
    return ts_payload(rows, columns, "leftover")


def page(title: str, blurb: str, headers: list, rows: list[list], out: Path,
         numeric=None, extra_html: str = "", payload: dict | None = None) -> None:
    numeric = set(numeric or [])
    nav = (
        '<nav><a href="index.html">Index</a>'
        '<a href="charged_off.html">Charged off</a>'
        f'<a href="not_yet_paid.html">{esc(NOT_YET_PAID_LABEL)}</a>'
        '<a href="forgiven.html">Forgiven</a>'
        '<a href="payroll.html">Payroll</a>'
        '<a href="zero_jobs.html">Zero jobs</a>'
        '<a href="federal_pins.html">Federal $ pins</a></nav>'
    )
    thead = "".join(f"<th>{esc(h)}</th>" for h in headers)
    body = []
    for row in rows:
        tds = []
        for i, cell in enumerate(row):
            cls = ' class="num"' if i in numeric else ""
            tds.append(f"<td{cls}>{cell}</td>")
        body.append("<tr>" + "".join(tds) + "</tr>")
    table = (
        f"<table><thead><tr>{thead}</tr></thead><tbody>\n"
        f"{''.join(body)}\n</tbody></table>"
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
<html lang="en"><head><meta charset="utf-8"><title>{esc(title)}</title>
<style>{PAGE_CSS}</style></head><body>
{nav}
<h1>{esc(title)}</h1>
<p>{blurb}</p>
<p class="meta">Generated {esc(generated_stamp())}. Top {len(rows):,} rows.
Click a column header to sort. Search filters the list.</p>
{extra_html}
{table_block}
{extra_foot}
</body></html>
"""
    out.write_text(html_out, encoding="utf-8")


def connect(db_path: str) -> duckdb.DuckDBPyConnection:
    return duckdb.connect(db_path, read_only=True)


def ppp_charged_off(con, n: int):
    return con.execute(
        f"""
        SELECT p.borrower_name, p.borrower_city, p.borrower_state, p.borrower_zip,
               p.jobs_reported, p.current_approval_amount,
               COALESCE(p.forgiveness_amount, 0) AS forgiven,
               p.current_approval_amount - COALESCE(p.forgiveness_amount, 0) AS leftover,
               p.originating_lender, g.latitude, g.longitude
        FROM ppp_loans p
        {PPP_GEO_JOIN}
        WHERE p.loan_status = 'Charged Off'
          AND COALESCE(p.current_approval_amount, 0) > 0
        ORDER BY leftover DESC
        LIMIT {int(n)}
        """
    ).fetchall()


def ppp_not_yet_paid(con, n: int):
    return con.execute(
        f"""
        SELECT p.borrower_name, p.borrower_city, p.borrower_state, p.borrower_zip,
               p.jobs_reported, p.current_approval_amount,
               COALESCE(p.forgiveness_amount, 0) AS forgiven,
               p.current_approval_amount - COALESCE(p.forgiveness_amount, 0) AS leftover,
               p.originating_lender, g.latitude, g.longitude
        FROM ppp_loans p
        {PPP_GEO_JOIN}
        WHERE p.loan_status = '{NOT_YET_PAID_STATUS}'
          AND COALESCE(p.current_approval_amount, 0) > 0
        ORDER BY leftover DESC
        LIMIT {int(n)}
        """
    ).fetchall()


def ppp_forgiven(con, n: int):
    """Any status with forgiveness, ranked by %. Floor keeps $1k 200% rows off the top."""
    return con.execute(
        f"""
        SELECT p.borrower_name, p.borrower_city, p.borrower_state, p.borrower_zip,
               p.jobs_reported, p.current_approval_amount,
               p.forgiveness_amount AS forgiven,
               p.current_approval_amount - COALESCE(p.forgiveness_amount, 0) AS leftover,
               p.originating_lender,
               p.forgiveness_amount / NULLIF(p.current_approval_amount, 0) AS pct,
               p.loan_status, g.latitude, g.longitude
        FROM ppp_loans p
        {PPP_GEO_JOIN}
        WHERE COALESCE(p.forgiveness_amount, 0) > 0
          AND COALESCE(p.current_approval_amount, 0) >= {int(FORGIVEN_MIN_APPROVED)}
        ORDER BY pct DESC NULLS LAST, forgiven DESC
        LIMIT {int(n)}
        """
    ).fetchall()


def ppp_payroll(con, n: int):
    return con.execute(
        f"""
        SELECT p.borrower_name, p.borrower_city, p.borrower_state, p.borrower_zip,
               p.jobs_reported, p.current_approval_amount,
               p.current_approval_amount / NULLIF(p.jobs_reported, 0) AS per_job,
               p.loan_status, g.latitude, g.longitude
        FROM ppp_loans p
        {PPP_GEO_JOIN}
        WHERE p.jobs_reported >= 1
          AND p.current_approval_amount >= 50000
        ORDER BY per_job DESC
        LIMIT {int(n)}
        """
    ).fetchall()


def ppp_zero_jobs(con, n: int):
    return con.execute(
        f"""
        SELECT p.borrower_name, p.borrower_city, p.borrower_state, p.borrower_zip,
               p.jobs_reported, p.current_approval_amount,
               COALESCE(p.forgiveness_amount, 0) AS forgiven,
               p.loan_status, g.latitude, g.longitude
        FROM ppp_loans p
        {PPP_GEO_JOIN}
        WHERE COALESCE(p.jobs_reported, 0) = 0
          AND COALESCE(p.current_approval_amount, 0) > 0
        ORDER BY p.current_approval_amount DESC
        LIMIT {int(n)}
        """
    ).fetchall()


def federal_pins(con, n: int):
    return con.execute(
        f"""
        SELECT
            a.canonical_address,
            g.latitude,
            g.longitude,
            SUM(u.obligation) AS dollars,
            COUNT(*)::BIGINT AS uei_n,
            SUM(u.award_n) AS award_n
        FROM usaspending_by_uei u
        JOIN sam_entities s ON s.uei = u.uei
        JOIN Addresses a
          ON a.owner_id = s.id
         AND a.address_type = 'sam_entity_phy'
        JOIN Geocoding g ON g.geocoding_id = a.geocoding_id
        WHERE g.latitude IS NOT NULL AND g.longitude IS NOT NULL
        GROUP BY 1, 2, 3
        ORDER BY dollars DESC
        LIMIT {int(n)}
        """
    ).fetchall()


def write_index(out_dir: Path, has_federal: bool, counts: dict, geo_counts: dict | None = None) -> None:
    items = [
        ("charged_off.html", "PPP charged off",
         "Loans SBA wrote off. Ranked by leftover (approved minus forgiven)."),
        ("not_yet_paid.html", NOT_YET_PAID_LABEL,
         "FOIA Exemption 4: SBA has not published a finished status. Ranked by leftover."),
        ("forgiven.html", "Forgiven",
         "Any status with forgiveness, ranked by %. Fully forgiven leftover $0 stays in."),
        ("payroll.html", "PPP payroll",
         "Highest dollars per reported job (approved ≥ $50k)."),
        ("zero_jobs.html", "PPP zero jobs",
         "Approved dollars with jobs_reported 0 or blank."),
    ]
    if has_federal:
        items.append(
            ("federal_pins.html", "Federal $ on SAM pins",
             "FY2025 USASpending obligations rolled to SAM physical colocators.")
        )
    lis = "".join(
        f"<li><a href='{esc(href)}'><strong>{esc(title)}</strong></a> — {esc(blurb)}</li>"
        for href, title, blurb in items
    )
    bits = " · ".join(
        f"{esc(k)} {v:,}" if isinstance(v, int) else f"{esc(k)} {esc(v)}"
        for k, v in counts.items()
    )
    html_out = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><title>Spending galleries</title>
<style>{PAGE_CSS}</style></head><body>
<h1>Spending galleries</h1>
<p>PPP leftover cuts and federal pins. Geography for these dollars is on
the Fun with Geography matrix (PPP Charged Off / Not yet paid / Forgiven /
Payroll / Zero jobs / Federal spending rows).</p>
<ul>{lis}</ul>
<p class="meta">{esc(bits)}<br>Generated {esc(generated_stamp())}</p>
</body></html>
"""
    (out_dir / "index.html").write_text(html_out, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db-path", default=DEFAULT_DB)
    parser.add_argument("--output-dir", default=str(DEFAULT_OUT))
    parser.add_argument("--top", type=int, default=TOP_N)
    args = parser.parse_args()
    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    con = connect(args.db_path)
    counts = {}

    charged = ppp_charged_off(con, args.top)
    counts["charged_off"] = len(charged)
    page(
        "PPP charged off",
        "SBA wrote these off. Leftover is approved amount minus any forgiveness on the row. "
        "Map is the same ranked loans, DOT Leaflet widget, size/color = leftover $.",
        ["Borrower", "City", "St", "ZIP", "Jobs", "Approved", "Forgiven", "Leftover", "Lender"],
        [
            [named(r, 0, 9, 10), esc(r[1]), esc(r[2]), esc(r[3]),
             f"{r[4]:,}" if r[4] is not None else "—",
             money(r[5]), money(r[6]), money(r[7]), esc(r[8])]
            for r in charged
        ],
        out / "charged_off.html",
        numeric={4, 5, 6, 7},
        extra_html=leaflet_embed_html(
            map_points_from_rows(
                charged, name_i=0, city_i=1, st_i=2, metric_i=7, lat_i=9, lon_i=10,
                line2=lambda r: f"<br>leftover {money(r[7])}",
            ),
            heading="Physical pins",
            caption=MAP_CAPTION,
        ),
        payload=leftover_gallery_payload(charged),
    )

    opened = ppp_not_yet_paid(con, args.top)
    counts["not_yet_paid"] = len(opened)
    page(
        NOT_YET_PAID_LABEL,
        "FOIA Exemption 4. SBA has not published a finished status "
        "(paid in full or charged off). Leftover is approved minus any forgiveness "
        "already on the row. Map is the same ranked loans, size/color = leftover $.",
        ["Borrower", "City", "St", "ZIP", "Jobs", "Approved", "Forgiven", "Leftover", "Lender"],
        [
            [named(r, 0, 9, 10), esc(r[1]), esc(r[2]), esc(r[3]),
             f"{r[4]:,}" if r[4] is not None else "—",
             money(r[5]), money(r[6]), money(r[7]), esc(r[8])]
            for r in opened
        ],
        out / "not_yet_paid.html",
        numeric={4, 5, 6, 7},
        extra_html=leaflet_embed_html(
            map_points_from_rows(
                opened, name_i=0, city_i=1, st_i=2, metric_i=7, lat_i=9, lon_i=10,
                line2=lambda r: f"<br>leftover {money(r[7])}",
            ),
            heading="Physical pins",
            caption=MAP_CAPTION,
        ),
        payload=leftover_gallery_payload(opened),
    )

    forgiven = ppp_forgiven(con, args.top)
    counts["forgiven"] = len(forgiven)
    page(
        "Forgiven",
        "Every published status, including Paid in Full leftover $0. "
        f"Approved ≥ ${FORGIVEN_MIN_APPROVED:,.0f}, ranked by forgiven ÷ approved "
        "(then by forgiven $). Map is the same ranked loans, size/color = forgiven $.",
        ["Borrower", "City", "St", "ZIP", "Jobs", "Approved", "Forgiven", "%", "Leftover", "Status", "Lender"],
        [
            [named(r, 0, 11, 12), esc(r[1]), esc(r[2]), esc(r[3]),
             f"{r[4]:,}" if r[4] is not None else "—",
             money(r[5]), money(r[6]), pct_label(r[6], r[5]), money(r[7]),
             esc(status_label(r[10])), esc(r[8])]
            for r in forgiven
        ],
        out / "forgiven.html",
        numeric={4, 5, 6, 7, 8},
        extra_html=leaflet_embed_html(
            map_points_from_rows(
                forgiven, name_i=0, city_i=1, st_i=2, metric_i=6, lat_i=11, lon_i=12,
                line2=lambda r: (
                    f"<br>{pct_label(r[6], r[5])} forgiven · {money(r[6])}<br>"
                    f"{esc(status_label(r[10]))}"
                ),
            ),
            heading="Physical pins",
            caption=MAP_CAPTION,
        ),
        payload=ts_payload(
            [
                {
                    "borrower": r[0] or "",
                    "borrower_html": named(r, 0, 11, 12),
                    "city": r[1] or "",
                    "state": r[2] or "",
                    "zip": r[3] or "",
                    "jobs_n": 0 if r[4] is None else int(r[4]),
                    "approved": float(r[5] or 0),
                    "approved_html": money(r[5]),
                    "forgiven": float(r[6] or 0),
                    "forgiven_html": money(r[6]),
                    "pct": float(r[9] or 0),
                    "pct_html": pct_label(r[6], r[5]),
                    "leftover": float(r[7] or 0),
                    "leftover_html": money(r[7]),
                    "status": status_label(r[10]),
                    "lender": r[8] or "",
                }
                for r in forgiven
            ],
            [
                {"id": "borrower", "header": "Borrower"},
                {"id": "city", "header": "City"},
                {"id": "state", "header": "St"},
                {"id": "zip", "header": "ZIP"},
                {"id": "jobs_n", "header": "Jobs"},
                {"id": "approved", "header": "Approved"},
                {"id": "forgiven", "header": "Forgiven"},
                {"id": "pct", "header": "%"},
                {"id": "leftover", "header": "Leftover"},
                {"id": "status", "header": "Status"},
                {"id": "lender", "header": "Lender"},
            ],
            "pct",
        ),
    )

    pay = ppp_payroll(con, args.top)
    counts["payroll"] = len(pay)
    page(
        "PPP dollars per reported job",
        "Approved amount ÷ jobs_reported. Floor $50k approved so tiny loans do not dominate. "
        "Map is the same ranked loans, size/color = $ / job.",
        ["Borrower", "City", "St", "ZIP", "Jobs", "Approved", "$ / job", "Status"],
        [
            [named(r, 0, 8, 9), esc(r[1]), esc(r[2]), esc(r[3]),
             f"{r[4]:,}" if r[4] is not None else "—",
             money(r[5]), money(r[6]),
             NOT_YET_PAID_LABEL if r[7] == NOT_YET_PAID_STATUS else esc(r[7])]
            for r in pay
        ],
        out / "payroll.html",
        numeric={4, 5, 6},
        extra_html=leaflet_embed_html(
            map_points_from_rows(
                pay, name_i=0, city_i=1, st_i=2, metric_i=6, lat_i=8, lon_i=9,
                line2=lambda r: f"<br>{money(r[6])} / job",
            ),
            heading="Physical pins",
            caption=MAP_CAPTION,
        ),
        payload=ts_payload(
            [
                {
                    "borrower": r[0] or "",
                    "borrower_html": named(r, 0, 8, 9),
                    "city": r[1] or "",
                    "state": r[2] or "",
                    "zip": r[3] or "",
                    "jobs_n": 0 if r[4] is None else int(r[4]),
                    "approved": float(r[5] or 0),
                    "approved_html": money(r[5]),
                    "per_job": float(r[6] or 0),
                    "per_job_html": money(r[6]),
                    "status": (
                        NOT_YET_PAID_LABEL if r[7] == NOT_YET_PAID_STATUS
                        else (r[7] or "")
                    ),
                }
                for r in pay
            ],
            [
                {"id": "borrower", "header": "Borrower"},
                {"id": "city", "header": "City"},
                {"id": "state", "header": "St"},
                {"id": "zip", "header": "ZIP"},
                {"id": "jobs_n", "header": "Jobs"},
                {"id": "approved", "header": "Approved"},
                {"id": "per_job", "header": "$ / job"},
                {"id": "status", "header": "Status"},
            ],
            "per_job",
        ),
    )

    zero = ppp_zero_jobs(con, args.top)
    counts["zero_jobs"] = len(zero)
    page(
        "PPP zero reported jobs",
        "Approved dollars with jobs_reported 0 or blank. Status Exemption 4 is labeled "
        f"{NOT_YET_PAID_LABEL}. Map is the same ranked loans, size/color = approved $.",
        ["Borrower", "City", "St", "ZIP", "Jobs", "Approved", "Forgiven", "Status"],
        [
            [named(r, 0, 8, 9), esc(r[1]), esc(r[2]), esc(r[3]),
             "0" if r[4] is None else f"{r[4]:,}",
             money(r[5]), money(r[6]),
             NOT_YET_PAID_LABEL if r[7] == NOT_YET_PAID_STATUS else esc(r[7])]
            for r in zero
        ],
        out / "zero_jobs.html",
        numeric={4, 5, 6},
        extra_html=leaflet_embed_html(
            map_points_from_rows(
                zero, name_i=0, city_i=1, st_i=2, metric_i=5, lat_i=8, lon_i=9,
                line2=lambda r: f"<br>approved {money(r[5])}",
            ),
            heading="Physical pins",
            caption=MAP_CAPTION,
        ),
        payload=ts_payload(
            [
                {
                    "borrower": r[0] or "",
                    "borrower_html": named(r, 0, 8, 9),
                    "city": r[1] or "",
                    "state": r[2] or "",
                    "zip": r[3] or "",
                    "jobs_n": 0 if r[4] is None else int(r[4]),
                    "approved": float(r[5] or 0),
                    "approved_html": money(r[5]),
                    "forgiven": float(r[6] or 0),
                    "forgiven_html": money(r[6]),
                    "status": (
                        NOT_YET_PAID_LABEL if r[7] == NOT_YET_PAID_STATUS
                        else (r[7] or "")
                    ),
                }
                for r in zero
            ],
            [
                {"id": "borrower", "header": "Borrower"},
                {"id": "city", "header": "City"},
                {"id": "state", "header": "St"},
                {"id": "zip", "header": "ZIP"},
                {"id": "jobs_n", "header": "Jobs"},
                {"id": "approved", "header": "Approved"},
                {"id": "forgiven", "header": "Forgiven"},
                {"id": "status", "header": "Status"},
            ],
            "approved",
        ),
    )

    has_federal = False
    try:
        n_act = con.execute("SELECT COUNT(*) FROM usaspending_by_uei").fetchone()[0]
        has_federal = int(n_act) > 0
    except Exception:
        has_federal = False

    if has_federal:
        pins = federal_pins(con, args.top)
        counts["federal_pins"] = len(pins)
        rows = []
        for r in pins:
            href = maps_url(r[1], r[2])
            loc = (
                f'<a href="{esc(href)}">{esc(r[0])}</a>'
                if href
                else esc(r[0])
            )
            rows.append(
                [loc, money(r[3]), f"{r[4]:,}", f"{int(r[5]):,}" if r[5] is not None else "—"]
            )
        fed_pts = []
        for r in pins:
            if not in_us(r[1], r[2]):
                continue
            fed_pts.append({
                "lat": round(float(r[1]), 4),
                "lon": round(float(r[2]), 4),
                "metric": float(r[3] or 0),
                "kind": "ll",
                "href": maps_url(r[1], r[2]),
                "popup_html": (
                    f"<strong>{esc(r[0])}</strong><br>{money(r[3])} obligated"
                ),
            })
        page(
            "Federal $ on SAM physical pins",
            "FY2025 USASpending obligations summed to SAM.gov physical addresses. "
            "Recipient HQ, not place of performance. Map is the same ranked pins, "
            "size/color = obligated $.",
            ["Address", "Obligated", "UEIs", "Awards"],
            rows,
            out / "federal_pins.html",
            numeric={1, 2, 3},
            extra_html=leaflet_embed_html(
                fed_pts,
                heading="Physical pins",
                caption=(
                    "SAM physical pin. Marker size/color = obligated dollars. "
                    "Double-click opens Google Maps. Same Leaflet widget as the DOT galleries."
                ),
            ),
            payload=ts_payload(
                [
                    {
                        "address": r[0] or "",
                        "address_html": (
                            f'<a href="{esc(maps_url(r[1], r[2]))}">{esc(r[0])}</a>'
                            if maps_url(r[1], r[2]) else esc(r[0])
                        ),
                        "dollars": float(r[3] or 0),
                        "dollars_html": money(r[3]),
                        "uei_n": int(r[4] or 0),
                        "award_n": 0 if r[5] is None else int(r[5]),
                    }
                    for r in pins
                ],
                [
                    {"id": "address", "header": "Address"},
                    {"id": "dollars", "header": "Obligated"},
                    {"id": "uei_n", "header": "UEIs"},
                    {"id": "award_n", "header": "Awards"},
                ],
                "dollars",
            ),
        )

    stale = out / "still_open.html"
    if stale.exists():
        stale.unlink()

    write_index(out, has_federal, counts)
    meta = {
        "generated_at": generated_stamp(),
        "counts": counts,
        "not_yet_paid_label": NOT_YET_PAID_LABEL,
        "not_yet_paid_status": NOT_YET_PAID_STATUS,
    }
    (out / "export_metadata.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(f"wrote {out} counts={counts} federal={has_federal}", flush=True)


if __name__ == "__main__":
    main()
