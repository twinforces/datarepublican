#!/usr/bin/env python3
"""DOT-style geography for PPP leftover $ / five PPP cuts and FY2025 federal $.

Slices: address, colocator, zipcode, loose_colocator (0.5°). Each slice has a
national list and a by-state heatmap + per-state cluster list. Detail pages
list PPP loans, SAM/USASpending, other address types, and charities at that pin.
Pins that also have NPPES / contractor / grant / charity / DOT / FEC rows get a
Bad address panel with Fun sibling links when those detail pages exist.

PPP domains: leftover (``ppp``) plus Charged Off / Not yet paid / Forgiven /
Payroll / Zero jobs. Fun publishes the five cuts as matrix rows; leftover
suites stay on disk. Cluster indexes, by-state ranking tables, and pin detail tables use the DOT TanStack table.

  python3 -u spending_reporting/generate_geo_reports.py \
      --db-path /Volumes/Data/final/irs990.duckdb
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import duckdb

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent
DOT = ROOT / "dot_reporting"
if str(DOT) not in sys.path:
    sys.path.insert(0, str(DOT))
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from generate_address_reports import (  # noqa: E402
    fmt_money,
    google_maps_url,
    slugify_address,
)
from generate_galleries import (  # noqa: E402
    FORGIVEN_MIN_APPROVED,
    MAP_CAPTION,
    NOT_YET_PAID_LABEL,
    NOT_YET_PAID_STATUS,
    dumps_table_json,
    leaflet_embed_html,
    status_label,
    tanstack_assets_html,
    ts_payload,
)
from map_points import clusters_to_map_points  # noqa: E402
from state_weights import (  # noqa: E402
    CENSUS_POP_SOURCE,
    EV_SOURCE,
    attach_federal_weights,
)
from us_state_map import render_heatmap_svg  # noqa: E402
from breadcrumbs import (  # noqa: E402
    crumbs_by_state_detail,
    crumbs_by_state_state,
    crumbs_by_state_us,
    crumbs_national_detail,
    crumbs_national_index,
)
from geo_overlap import (  # noqa: E402
    bad_address_html,
    build_overlap_hits,
    extra_overlap_roots,
    refresh_geo_overlap,
    type_hrefs,
)
from geo_zoom import breadcrumbs_html, refresh_geo_widen, widen_html  # noqa: E402

DEFAULT_DB = "/Volumes/Data/final/irs990.duckdb"
DEFAULT_OUT = SCRIPT_DIR / "reports" / "geo"
TZ = ZoneInfo("America/Los_Angeles")
MAX_N = 100
SHOW_CAP = 40
LOAN_TOP = 200

SLICES = {
    "address": {
        "label": "canonical address",
        "key": "NULLIF(TRIM(a.canonical_address), '')",
        "where": "a.canonical_address IS NOT NULL AND TRIM(a.canonical_address) != ''",
        "join": "",
    },
    "colocator": {
        "label": "colocator",
        "key": "a.colocator",
        "where": "a.colocator IS NOT NULL AND TRIM(a.colocator) != ''",
        "join": "",
    },
    "zipcode": {
        "label": "zip code",
        "key": "LEFT(TRIM(a.zip_code), 5)",
        "where": "a.zip_code IS NOT NULL AND LENGTH(TRIM(a.zip_code)) >= 5",
        "join": "",
    },
    "loose_colocator": {
        "label": "loose colocator (0.5°)",
        "key": (
            "CASE WHEN COALESCE(g.latitude, a.latitude) IS NOT NULL "
            "THEN 'LL:' || CAST(ROUND(COALESCE(g.latitude, a.latitude) / 0.5) * 0.5 AS VARCHAR) "
            "|| ':' || CAST(ROUND(COALESCE(g.longitude, a.longitude) / 0.5) * 0.5 AS VARCHAR) "
            "END"
        ),
        "where": "COALESCE(g.latitude, a.latitude) IS NOT NULL",
        "join": "LEFT JOIN Geocoding g ON g.geocoding_id = a.geocoding_id",
    },
}

PAYROLL_MIN_APPROVED = 50_000

PPP_LEFTOVER = {
    "label": "PPP leftover",
    "where": "1=1",
    "having": "leftover > 0",
    "order": "leftover DESC",
    "metric": "leftover",
    "metric_label": "leftover $",
    "heatmap": "leftover",
    "heatmap_label": "PPP leftover $",
    "blurb": (
        "PPP leftover $ (approved minus forgiven) grouped by {slice}. "
        "Paid in Full leftover ~0 drops out. Sort any column."
    ),
}

PPP_CUTS = {
    "ppp_charged_off": {
        "label": "PPP Charged Off",
        "where": (
            "p.loan_status = 'Charged Off' "
            "AND COALESCE(p.current_approval_amount, 0) > 0"
        ),
        "having": "leftover > 0",
        "order": "leftover DESC",
        "metric": "leftover",
        "metric_label": "leftover $",
        "heatmap": "leftover",
        "heatmap_label": "PPP leftover $",
        "blurb": (
            "SBA wrote these off. Ranked leftover (approved minus forgiven) "
            "at the {slice}. Sort any column."
        ),
    },
    "ppp_not_yet_paid": {
        "label": "PPP Not yet paid",
        "where": (
            f"p.loan_status = '{NOT_YET_PAID_STATUS}' "
            "AND COALESCE(p.current_approval_amount, 0) > 0"
        ),
        "having": "leftover > 0",
        "order": "leftover DESC",
        "metric": "leftover",
        "metric_label": "leftover $",
        "heatmap": "leftover",
        "heatmap_label": "PPP leftover $",
        "blurb": (
            f"FOIA {NOT_YET_PAID_LABEL} (Exemption 4). Ranked leftover at the "
            "{slice}. Sort any column."
        ),
    },
    "ppp_forgiven": {
        "label": "PPP Forgiven",
        "where": (
            "COALESCE(p.forgiveness_amount, 0) > 0 "
            f"AND COALESCE(p.current_approval_amount, 0) >= {int(FORGIVEN_MIN_APPROVED)}"
        ),
        "having": "forgiven > 0",
        "order": "pct DESC NULLS LAST, forgiven DESC",
        "metric": "pct",
        "metric_label": "forgiven %",
        "heatmap": "forgiven",
        "heatmap_label": "PPP forgiven $",
        "blurb": (
            "Any status with forgiveness, approved ≥ $150k, ranked by forgiven "
            "÷ approved at the {slice}. Leftover $0 stays in. Sort any column."
        ),
    },
    "ppp_payroll": {
        "label": "PPP Payroll",
        "where": (
            "p.jobs_reported >= 1 "
            f"AND p.current_approval_amount >= {int(PAYROLL_MIN_APPROVED)}"
        ),
        "having": "jobs_n >= 1",
        "order": "per_job DESC NULLS LAST",
        "metric": "per_job",
        "metric_label": "$ / job",
        "heatmap": "per_job",
        "heatmap_label": "PPP $ / job",
        "blurb": (
            "Approved ≥ $50k with at least one reported job, ranked $ per job "
            "at the {slice}. Sort any column."
        ),
    },
    "ppp_zero_jobs": {
        "label": "PPP Zero jobs",
        "where": (
            "COALESCE(p.jobs_reported, 0) = 0 "
            "AND COALESCE(p.current_approval_amount, 0) > 0"
        ),
        "having": "approved > 0",
        "order": "approved DESC",
        "metric": "approved",
        "metric_label": "approved $",
        "heatmap": "approved",
        "heatmap_label": "PPP approved $",
        "blurb": (
            "Approved dollars with jobs_reported 0 or blank, ranked approved $ "
            "at the {slice}. Sort any column."
        ),
    },
}

PPP_DOMAINS = ("ppp",) + tuple(PPP_CUTS)
LEFTOVER_PPP_SUITES = frozenset(
    {
        "ppp_address",
        "ppp_colocator",
        "ppp_zipcode",
        "ppp_loose_colocator",
        "ppp_by_state",
        "ppp_address_by_state",
        "ppp_zipcode_by_state",
        "ppp_loose_colocator_by_state",
    }
)


def want_suite(name: str, tokens: set[str]) -> bool:
    """``ppp`` is leftover only; cut tokens match that cut's eight dirs."""
    if not tokens or "all" in tokens:
        return True
    if name in tokens:
        return True
    folded = name.replace("_", "")
    if folded in {t.replace("_", "") for t in tokens}:
        return True
    if "ppp" in tokens and name in LEFTOVER_PPP_SUITES:
        return True
    if "federal" in tokens and (name == "federal" or name.startswith("federal_")):
        return True
    for cut in PPP_CUTS:
        if cut in tokens and (name == cut or name.startswith(cut + "_")):
            return True
    return False

PAGE_CSS = """
body { font-family: Georgia, serif; margin: 1.5rem auto; max-width: 1100px;
       color: #222; line-height: 1.45; }
nav.breadcrumbs { margin: 0 0 1rem; font-size: 0.88rem; color: #555; line-height: 1.5; }
nav.breadcrumbs a { color: #0b57d0; text-decoration: none; font-weight: 500; }
nav.breadcrumbs a:hover { text-decoration: underline; }
nav.breadcrumbs .bc-sep { margin: 0 0.35rem; color: #9ca3af; }
nav.breadcrumbs .bc-current { color: #111; font-weight: 600; }
.widen-nav {
  margin: 0.5rem 0 1rem; padding: 0.55rem 0.75rem;
  background: #f0f7ff; border: 1px solid #cfe2ff; border-radius: 8px;
  font-size: 0.9rem;
}
.widen-nav .widen-label { color: #174ea6; margin-right: 0.5rem; }
.widen-nav a.widen-link { color: #0b57d0; font-weight: 600; text-decoration: none; }
.widen-nav a.widen-link:hover { text-decoration: underline; }
.widen-nav a.widen-link.missing { opacity: 0.65; font-weight: 500; }
.widen-nav .widen-sep { color: #94a3b8; margin: 0 0.35rem; }
table { width: 100%; border-collapse: collapse; font-size: 0.9rem; }
th, td { border: 1px solid #ddd; padding: 0.4rem 0.5rem; text-align: left;
         vertical-align: top; }
th { background: #f4f4f4; position: sticky; top: 0; }
tr:nth-child(even) { background: #fafafa; }
.meta { color: #666; font-size: 0.9rem; }
.num { text-align: right; font-variant-numeric: tabular-nums; }
.cards { display: flex; flex-wrap: wrap; gap: 0.75rem; margin: 1rem 0; }
.card { border: 1px solid #ddd; border-radius: 8px; padding: 0.6rem 0.85rem;
        min-width: 7.5rem; }
.card strong { display: block; font-size: 1.15rem; }
.card span { font-size: 0.8rem; color: #555; }
.leaflet-map-section h2 { font-size: 1.05rem; margin: 1rem 0 0.35rem; }
.bad-address {
  margin: 0.5rem 0 1rem; padding: 0.55rem 0.75rem;
  background: #fff7ed; border: 1px solid #fdba74; border-radius: 8px;
}
.bad-address h2 { font-size: 1.05rem; margin: 0 0 0.35rem; }
.bad-address ul { margin: 0.25rem 0 0; padding-left: 1.2rem; }
.bad-address a { color: #9a3412; font-weight: 600; text-decoration: none; }
.bad-address a:hover { text-decoration: underline; }
"""


def esc(s) -> str:
    return html.escape("" if s is None else str(s), quote=True)


def stamp() -> str:
    return datetime.now(TZ).strftime("%Y-%m-%d %H:%M %Z")


def ppp_cut(domain: str) -> dict:
    if domain == "ppp":
        return PPP_LEFTOVER
    return PPP_CUTS[domain]


def wrap(
    title: str,
    body: str,
    *,
    extra_foot: str = "",
    breadcrumbs: list | None = None,
) -> str:
    nav = breadcrumbs_html(breadcrumbs or [])
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><title>{esc(title)}</title>
<style>{PAGE_CSS}</style></head><body>
{nav}
{body}
{extra_foot}
</body></html>
"""


def unique_slug(key: str, used: set[str]) -> str:
    s = slugify_address(key)
    if s in used:
        s = (s[:100] + "-" + hashlib.md5(key.encode("utf-8")).hexdigest()[:8])
    used.add(s)
    return s


def connect(db: str) -> duckdb.DuckDBPyConnection:
    return duckdb.connect(db, read_only=True, config={"memory_limit": "8GB", "threads": "2"})


def ppp_cluster_sql(
    slice_by: str, *, state: str | None, limit: int, domain: str = "ppp"
) -> tuple[str, list]:
    sl = SLICES[slice_by]
    cut = ppp_cut(domain)
    state_sql = "AND UPPER(TRIM(a.state)) = ?" if state else ""
    params: list = []
    if state:
        params.append(state.upper())
    params.append(int(limit))
    sql = f"""
    SELECT
        ({sl['key']}) AS cluster_key,
        COUNT(*)::BIGINT AS loan_n,
        SUM(p.current_approval_amount)::DOUBLE AS approved,
        SUM(COALESCE(p.forgiveness_amount, 0))::DOUBLE AS forgiven,
        SUM(p.current_approval_amount - COALESCE(p.forgiveness_amount, 0))::DOUBLE AS leftover,
        SUM(COALESCE(p.forgiveness_amount, 0))
          / NULLIF(SUM(p.current_approval_amount), 0) AS pct,
        SUM(COALESCE(p.jobs_reported, 0))::DOUBLE AS jobs_n,
        SUM(p.current_approval_amount)
          / NULLIF(SUM(COALESCE(p.jobs_reported, 0)), 0) AS per_job,
        COUNT(*) FILTER (WHERE p.loan_status = 'Charged Off')::BIGINT AS charged_n,
        COUNT(*) FILTER (WHERE p.loan_status = '{NOT_YET_PAID_STATUS}')::BIGINT AS open_n,
        COUNT(*) FILTER (WHERE p.loan_status = 'Paid in Full')::BIGINT AS paid_n,
        ANY_VALUE(a.canonical_address) AS sample_address,
        ANY_VALUE(a.state) AS state,
        ANY_VALUE(LEFT(TRIM(a.zip_code), 5)) AS zip_code,
        ANY_VALUE(a.colocator) AS colocator,
        ANY_VALUE(COALESCE(g.latitude, a.latitude)) AS latitude,
        ANY_VALUE(COALESCE(g.longitude, a.longitude)) AS longitude
    FROM ppp_loans p
    JOIN Addresses a
      ON a.owner_id = p.id AND a.address_type = 'ppp_borrower'
    LEFT JOIN Geocoding g ON g.geocoding_id = a.geocoding_id
    WHERE {sl['where']}
      AND ({sl['key']}) IS NOT NULL
      AND ({cut['where']})
      {state_sql}
    GROUP BY 1
    HAVING {cut['having']}
    ORDER BY {cut['order']}
    LIMIT ?
    """
    return sql, params


def federal_cluster_sql(slice_by: str, *, state: str | None, limit: int) -> tuple[str, list]:
    sl = SLICES[slice_by]
    state_sql = "AND UPPER(TRIM(a.state)) = ?" if state else ""
    params: list = []
    if state:
        params.append(state.upper())
    params.append(int(limit))
    sql = f"""
    SELECT
        ({sl['key']}) AS cluster_key,
        COUNT(*)::BIGINT AS uei_n,
        SUM(u.obligation)::DOUBLE AS dollars,
        SUM(u.award_n)::BIGINT AS award_n,
        ANY_VALUE(a.canonical_address) AS sample_address,
        ANY_VALUE(a.state) AS state,
        ANY_VALUE(LEFT(TRIM(a.zip_code), 5)) AS zip_code,
        ANY_VALUE(a.colocator) AS colocator,
        ANY_VALUE(COALESCE(g.latitude, a.latitude)) AS latitude,
        ANY_VALUE(COALESCE(g.longitude, a.longitude)) AS longitude
    FROM usaspending_by_uei u
    JOIN sam_entities s ON s.uei = u.uei
    JOIN Addresses a
      ON a.owner_id = s.id AND a.address_type = 'sam_entity_phy'
    LEFT JOIN Geocoding g ON g.geocoding_id = a.geocoding_id
    WHERE {sl['where']}
      AND ({sl['key']}) IS NOT NULL
      {state_sql}
    GROUP BY 1
    HAVING dollars > 0
    ORDER BY dollars DESC
    LIMIT ?
    """
    return sql, params


def fetch_clusters(con, domain: str, slice_by: str, limit: int, state: str | None = None):
    if domain == "federal":
        sql, params = federal_cluster_sql(slice_by, state=state, limit=limit)
        metric_key = "dollars"
    else:
        sql, params = ppp_cluster_sql(
            slice_by, state=state, limit=limit, domain=domain
        )
        metric_key = ppp_cut(domain)["metric"]
    cur = con.execute(sql, params)
    names = [c[0] for c in cur.description]
    out = []
    for row in cur.fetchall():
        d = dict(zip(names, row))
        d["rank_metric_value"] = float(d.get(metric_key) or 0)
        out.append(d)
    return out


def _in_clause(n: int) -> str:
    return ",".join(["?"] * n)


def fetch_detail_bundle(con, slice_by: str, keys: list) -> dict:
    """One scan per table for the whole suite, not one per cluster."""
    empty = {"loans": [], "sam": [], "types": [], "charities": []}
    out = {str(k): {kk: [] for kk in empty} for k in keys}
    if not keys:
        return out
    sl = SLICES[slice_by]
    key_sql = sl["key"]
    ph = _in_clause(len(keys))

    loan_rows = con.execute(
        f"""
        SELECT ({key_sql}) AS cluster_key,
               p.borrower_name, p.borrower_city, p.borrower_state, p.borrower_zip,
               p.jobs_reported, p.current_approval_amount,
               COALESCE(p.forgiveness_amount, 0),
               p.current_approval_amount - COALESCE(p.forgiveness_amount, 0),
               p.loan_status, p.originating_lender
        FROM ppp_loans p
        JOIN Addresses a ON a.owner_id = p.id AND a.address_type = 'ppp_borrower'
        LEFT JOIN Geocoding g ON g.geocoding_id = a.geocoding_id
        WHERE ({key_sql}) IN ({ph})
        QUALIFY ROW_NUMBER() OVER (
            PARTITION BY ({key_sql})
            ORDER BY (p.current_approval_amount - COALESCE(p.forgiveness_amount, 0)) DESC
        ) <= {int(LOAN_TOP)}
        """,
        list(keys),
    ).fetchall()
    for r in loan_rows:
        if str(r[0]) in out:
            out[str(r[0])]["loans"].append(r[1:])

    try:
        sam_rows = con.execute(
            f"""
            SELECT ({key_sql}) AS cluster_key,
                   COALESCE(s.legal_name, u.recipient_name),
                   s.uei, s.cage,
                   u.obligation, u.award_n
            FROM Addresses a
            JOIN sam_entities s ON s.id = a.owner_id AND a.address_type = 'sam_entity_phy'
            LEFT JOIN usaspending_by_uei u ON u.uei = s.uei
            LEFT JOIN Geocoding g ON g.geocoding_id = a.geocoding_id
            WHERE ({key_sql}) IN ({ph})
            QUALIFY ROW_NUMBER() OVER (
                PARTITION BY ({key_sql})
                ORDER BY COALESCE(u.obligation, 0) DESC
            ) <= 100
            """,
            list(keys),
        ).fetchall()
        for r in sam_rows:
            if str(r[0]) in out:
                out[str(r[0])]["sam"].append(r[1:])
    except Exception:
        pass

    type_rows = con.execute(
        f"""
        SELECT ({key_sql}) AS cluster_key, a.address_type, COUNT(*)::BIGINT
        FROM Addresses a
        LEFT JOIN Geocoding g ON g.geocoding_id = a.geocoding_id
        WHERE ({key_sql}) IN ({ph})
        GROUP BY 1, 2
        ORDER BY 3 DESC
        """,
        list(keys),
    ).fetchall()
    for k, t, n in type_rows:
        if str(k) in out:
            out[str(k)]["types"].append((t, n))

    try:
        ch_rows = con.execute(
            f"""
            SELECT ({key_sql}) AS cluster_key, c.filer_name, c.ein, c.govt_amt
            FROM Addresses a
            JOIN Charities c ON c.charity_id = a.owner_id AND a.address_type = 'charity'
            LEFT JOIN Geocoding g ON g.geocoding_id = a.geocoding_id
            WHERE ({key_sql}) IN ({ph})
            QUALIFY ROW_NUMBER() OVER (
                PARTITION BY ({key_sql})
                ORDER BY COALESCE(c.govt_amt, 0) DESC
            ) <= 25
            """,
            list(keys),
        ).fetchall()
        for r in ch_rows:
            if str(r[0]) in out:
                out[str(r[0])]["charities"].append(r[1:])
    except Exception:
        pass

    return out


def pct_fmt(v) -> str:
    if v is None:
        return "—"
    try:
        return f"{float(v) * 100:.1f}%"
    except (TypeError, ValueError):
        return "—"


def cluster_table_payload(domain: str, clusters: list[dict]) -> dict:
    if domain == "federal":
        rows = []
        for c in clusters:
            label = c.get("sample_address") or c["cluster_key"]
            rows.append(
                {
                    "pin": label,
                    "pin_html": f'<a href="{esc(c["href"])}">{esc(label)}</a>',
                    "state": c.get("state") or "",
                    "uei_n": int(c.get("uei_n") or 0),
                    "dollars": float(c.get("dollars") or 0),
                    "dollars_html": fmt_money(c.get("dollars")),
                    "award_n": int(c.get("award_n") or 0),
                }
            )
        return {
            "rows": rows,
            "columns": [
                {"id": "pin", "header": "Pin"},
                {"id": "state", "header": "St"},
                {"id": "uei_n", "header": "UEIs"},
                {"id": "dollars", "header": "Obligated"},
                {"id": "award_n", "header": "Awards"},
            ],
            "initialSort": [{"id": "dollars", "desc": True}],
            "pageSize": 50,
        }
    cut = ppp_cut(domain)
    rows = []
    for c in clusters:
        label = c.get("sample_address") or c["cluster_key"]
        rows.append(
            {
                "pin": label,
                "pin_html": f'<a href="{esc(c["href"])}">{esc(label)}</a>',
                "state": c.get("state") or "",
                "loan_n": int(c.get("loan_n") or 0),
                "leftover": float(c.get("leftover") or 0),
                "leftover_html": fmt_money(c.get("leftover")),
                "forgiven": float(c.get("forgiven") or 0),
                "forgiven_html": fmt_money(c.get("forgiven")),
                "approved": float(c.get("approved") or 0),
                "approved_html": fmt_money(c.get("approved")),
                "pct": float(c.get("pct") or 0),
                "pct_html": pct_fmt(c.get("pct")),
                "jobs_n": int(c.get("jobs_n") or 0),
                "per_job": float(c.get("per_job") or 0),
                "per_job_html": fmt_money(c.get("per_job")),
                "charged_n": int(c.get("charged_n") or 0),
                "open_n": int(c.get("open_n") or 0),
                "paid_n": int(c.get("paid_n") or 0),
            }
        )
    return {
        "rows": rows,
        "columns": [
            {"id": "pin", "header": "Pin"},
            {"id": "state", "header": "St"},
            {"id": "loan_n", "header": "Loans"},
            {"id": "leftover", "header": "Leftover"},
            {"id": "forgiven", "header": "Forgiven"},
            {"id": "approved", "header": "Approved"},
            {"id": "pct", "header": "% forgiven"},
            {"id": "jobs_n", "header": "Jobs"},
            {"id": "per_job", "header": "$ / job"},
            {"id": "charged_n", "header": "Charged off"},
            {"id": "open_n", "header": NOT_YET_PAID_LABEL},
            {"id": "paid_n", "header": "Paid in full"},
        ],
        "initialSort": [{"id": cut["metric"], "desc": True}],
        "pageSize": 50,
    }


def table_html(headers, rows, numeric=()) -> str:
    numeric = set(numeric)
    thead = "".join(f"<th>{esc(h)}</th>" for h in headers)
    body = []
    for row in rows:
        tds = []
        for i, cell in enumerate(row):
            cls = ' class="num"' if i in numeric else ""
            tds.append(f"<td{cls}>{cell}</td>")
        body.append("<tr>" + "".join(tds) + "</tr>")
    return f"<table><thead><tr>{thead}</tr></thead><tbody>{''.join(body)}</tbody></table>"


def ts_mount(root_id: str, fallback: str) -> str:
    return (
        f'<div id="{esc(root_id)}"></div>\n'
        f"<noscript>\n{fallback}\n</noscript>\n"
    )


def detail_tables_payload(
    bundle: dict, type_links: dict[str, str] | None = None
) -> list[dict]:
    tables: list[dict] = []
    types = bundle.get("types") or []
    links = type_links or {}
    if types:
        type_rows = []
        for t, n in types:
            name = t or ""
            href = links.get(t) or links.get(name)
            row = {"type": name, "rows": int(n or 0)}
            if href:
                row["type_html"] = f'<a href="{esc(href)}">{esc(name)}</a>'
            type_rows.append(row)
        tables.append(
            {
                "rootId": "ts-types",
                "pageSize": 25,
                "initialSort": [{"id": "rows", "desc": True}],
                "columns": [
                    {"id": "type", "header": "Type"},
                    {"id": "rows", "header": "Rows"},
                ],
                "rows": type_rows,
            }
        )
    loans = bundle.get("loans") or []
    if loans:
        rows = []
        for r in loans:
            jobs = r[4]
            rows.append(
                {
                    "borrower": r[0] or "",
                    "city": r[1] or "",
                    "state": r[2] or "",
                    "zip": r[3] or "",
                    "jobs_n": 0 if jobs is None else int(jobs),
                    "approved": float(r[5] or 0),
                    "approved_html": fmt_money(r[5]),
                    "forgiven": float(r[6] or 0),
                    "forgiven_html": fmt_money(r[6]),
                    "leftover": float(r[7] or 0),
                    "leftover_html": fmt_money(r[7]),
                    "status": status_label(r[8]),
                    "lender": r[9] or "",
                }
            )
        tables.append(
            {
                "rootId": "ts-loans",
                "pageSize": 50,
                "initialSort": [{"id": "leftover", "desc": True}],
                "columns": [
                    {"id": "borrower", "header": "Borrower"},
                    {"id": "city", "header": "City"},
                    {"id": "state", "header": "St"},
                    {"id": "zip", "header": "ZIP"},
                    {"id": "jobs_n", "header": "Jobs"},
                    {"id": "approved", "header": "Approved"},
                    {"id": "forgiven", "header": "Forgiven"},
                    {"id": "leftover", "header": "Leftover"},
                    {"id": "status", "header": "Status"},
                    {"id": "lender", "header": "Lender"},
                ],
                "rows": rows,
            }
        )
    sam = bundle.get("sam") or []
    if sam:
        rows = []
        for r in sam:
            rows.append(
                {
                    "name": r[0] or "",
                    "uei": r[1] or "",
                    "cage": r[2] or "",
                    "dollars": float(r[3] or 0),
                    "dollars_html": fmt_money(r[3]),
                    "award_n": 0 if r[4] is None else int(r[4]),
                }
            )
        tables.append(
            {
                "rootId": "ts-sam",
                "pageSize": 50,
                "initialSort": [{"id": "dollars", "desc": True}],
                "columns": [
                    {"id": "name", "header": "Name"},
                    {"id": "uei", "header": "UEI"},
                    {"id": "cage", "header": "CAGE"},
                    {"id": "dollars", "header": "Obligated"},
                    {"id": "award_n", "header": "Awards"},
                ],
                "rows": rows,
            }
        )
    charities = bundle.get("charities") or []
    if charities:
        rows = []
        for r in charities:
            rows.append(
                {
                    "name": r[0] or "",
                    "ein": r[1] or "",
                    "govt": float(r[2] or 0),
                    "govt_html": fmt_money(r[2]),
                }
            )
        tables.append(
            {
                "rootId": "ts-charities",
                "pageSize": 25,
                "initialSort": [{"id": "govt", "desc": True}],
                "columns": [
                    {"id": "name", "header": "Name"},
                    {"id": "ein", "header": "EIN"},
                    {"id": "govt", "header": "Govt $"},
                ],
                "rows": rows,
            }
        )
    return tables


def state_table_payload(domain: str, stats: list[dict], value_key: str,
                        label: str) -> dict:
    rows = []
    for s in stats:
        st = s["state"]
        href = f"states/{st}/index.html" if s.get("show_n") else ""
        metric = s.get(value_key)
        row = {
            "state": st,
            "state_html": (
                f'<a href="{esc(href)}">{esc(st)}</a>' if href else esc(st)
            ),
            "metric": float(metric or 0),
            "metric_html": fmt_money(metric),
            "clusters": int(s.get("pass_clusters") or 0),
            "show_n": int(s.get("show_n") or 0),
            "open": "Open →" if href else "",
            "open_html": (
                f'<a href="{esc(href)}">Open →</a>' if href else "—"
            ),
        }
        if domain == "federal":
            pop = s.get("pop")
            ev = s.get("electoral_votes")
            row["dollars_per_pop"] = float(s.get("dollars_per_pop") or 0)
            row["dollars_per_pop_html"] = fmt_money(s.get("dollars_per_pop"))
            row["dollars_per_ev"] = float(s.get("dollars_per_ev") or 0)
            row["dollars_per_ev_html"] = fmt_money(s.get("dollars_per_ev"))
            row["pop"] = int(pop) if pop else 0
            row["pop_html"] = f"{int(pop):,}" if pop else "—"
            row["ev"] = int(ev) if ev else 0
            row["ev_html"] = f"{int(ev):,}" if ev else "—"
        rows.append(row)
    if domain == "federal":
        columns = [
            {"id": "state", "header": "State"},
            {"id": "metric", "header": label},
            {"id": "dollars_per_pop", "header": "$ / pop"},
            {"id": "dollars_per_ev", "header": "$ / EV"},
            {"id": "pop", "header": "Pop"},
            {"id": "ev", "header": "EV"},
            {"id": "clusters", "header": "Clusters"},
            {"id": "show_n", "header": "Show"},
            {"id": "open", "header": ""},
        ]
    else:
        columns = [
            {"id": "state", "header": "State"},
            {"id": "metric", "header": label},
            {"id": "clusters", "header": "Clusters"},
            {"id": "show_n", "header": "Show"},
            {"id": "open", "header": ""},
        ]
    return ts_payload(rows, columns, "metric", page_size=60)


def cluster_index_page(
    domain: str,
    slice_by: str,
    clusters: list[dict],
    out: Path,
    *,
    depth: int = 1,
    heading_prefix: str = "",
    state: str | None = None,
) -> None:
    used: set[str] = set()
    for c in clusters:
        c["slug"] = unique_slug(str(c["cluster_key"]), used)
        c["href"] = f"{c['slug']}.html"
        c["detail_file"] = c["href"]
        c["maps"] = google_maps_url(c.get("sample_address") or str(c["cluster_key"]))
        if domain == "federal":
            metric = c.get("dollars")
            c["rank_metric_fmt"] = fmt_money(metric)
        else:
            cut = ppp_cut(domain)
            metric = c.get(cut["metric"])
            if cut["metric"] == "pct":
                c["rank_metric_fmt"] = pct_fmt(metric)
            else:
                c["rank_metric_fmt"] = fmt_money(metric)

    pts = clusters_to_map_points(clusters, slice_by=slice_by)
    map_html = leaflet_embed_html(
        pts,
        heading="Physical pins",
        caption=MAP_CAPTION,
    ) if pts else ""

    if domain != "federal":
        cut = ppp_cut(domain)
        slice_label = SLICES[slice_by]["label"]
        headers = [
            "Pin", "St", "Loans", "Leftover", "Forgiven", "Approved",
            "% forgiven", "Jobs", "$ / job",
            "Charged off", "Not yet paid", "Paid in full",
        ]
        rows = []
        for c in clusters:
            label = esc(c.get("sample_address") or c["cluster_key"])
            rows.append([
                f'<a href="{esc(c["href"])}">{label}</a>',
                esc(c.get("state")),
                f"{int(c['loan_n']):,}",
                fmt_money(c.get("leftover")),
                fmt_money(c.get("forgiven")),
                fmt_money(c.get("approved")),
                pct_fmt(c.get("pct")),
                f"{int(c.get('jobs_n') or 0):,}",
                fmt_money(c.get("per_job")),
                f"{int(c['charged_n']):,}",
                f"{int(c['open_n']):,}",
                f"{int(c['paid_n']):,}",
            ])
        numeric = {2, 3, 4, 5, 6, 7, 8, 9, 10, 11}
        blurb = cut["blurb"].format(slice=slice_label)
        title = f"{cut['label']} · {heading_prefix}{slice_label}"
    else:
        headers = ["Pin", "St", "UEIs", "Obligated", "Awards"]
        rows = []
        for c in clusters:
            label = esc(c.get("sample_address") or c["cluster_key"])
            rows.append([
                f'<a href="{esc(c["href"])}">{label}</a>',
                esc(c.get("state")),
                f"{int(c['uei_n']):,}",
                fmt_money(c.get("dollars")),
                f"{int(c.get('award_n') or 0):,}",
            ])
        numeric = {2, 3, 4}
        blurb = (
            f"FY2025 USASpending obligations on SAM physical pins, grouped by "
            f"{SLICES[slice_by]['label']}. Recipient HQ."
        )
        title = f"Federal $ · {heading_prefix}{SLICES[slice_by]['label']}"

    payload = cluster_table_payload(domain, clusters)
    extra_foot = (
        f"<script>window.__CLUSTER_TABLE__ = {dumps_table_json(payload)};</script>\n"
        + tanstack_assets_html()
    )
    body = f"""
<h1>{esc(title)}</h1>
<p>{blurb}</p>
<p class="meta">Generated {esc(stamp())}. Top {len(clusters):,} clusters.
Click a column header to sort. Search filters the list.</p>
{map_html}
<div id="ts-table-root"></div>
<noscript>
{table_html(headers, rows, numeric)}
</noscript>
"""
    if depth >= 3 and state:
        crumbs = crumbs_by_state_state(focus=domain, slice_by=slice_by, state=state)
    else:
        crumbs = crumbs_national_index(focus=domain, slice_by=slice_by)
    out.mkdir(parents=True, exist_ok=True)
    (out / "index.html").write_text(
        wrap(title, body, extra_foot=extra_foot, breadcrumbs=crumbs),
        encoding="utf-8",
    )


def detail_page(
    domain: str,
    slice_by: str,
    cluster: dict,
    out: Path,
    bundle: dict,
    *,
    depth: int = 1,
    reports_dir: Path | None = None,
    context: str = "national",
    state: str | None = None,
    search_roots: list[Path] | None = None,
) -> None:
    key = str(cluster["cluster_key"])
    loans = bundle.get("loans") or []
    sam = bundle.get("sam") or []
    types = bundle.get("types") or []
    charities = bundle.get("charities") or []
    pts = clusters_to_map_points([cluster], slice_by=slice_by)
    map_html = leaflet_embed_html(
        pts, heading="Physical pin", caption="Double-click opens Google Maps."
    ) if pts else ""

    cards = []
    if domain != "federal":
        cards = [
            (fmt_money(cluster.get("leftover")), "leftover"),
            (fmt_money(cluster.get("forgiven")), "forgiven"),
            (fmt_money(cluster.get("approved")), "approved"),
            (pct_fmt(cluster.get("pct")), "% forgiven"),
            (f"{int(cluster.get('jobs_n') or 0):,}", "jobs"),
            (fmt_money(cluster.get("per_job")), "$ / job"),
            (f"{int(cluster.get('loan_n') or 0):,}", "PPP loans"),
            (f"{int(cluster.get('charged_n') or 0):,}", "charged off"),
            (f"{int(cluster.get('open_n') or 0):,}", NOT_YET_PAID_LABEL.lower()),
        ]
    else:
        cards = [
            (fmt_money(cluster.get("dollars")), "obligated"),
            (f"{int(cluster.get('uei_n') or 0):,}", "UEIs"),
            (f"{int(cluster.get('award_n') or 0):,}", "awards"),
        ]
    card_html = '<div class="cards">' + "".join(
        f'<div class="card"><strong>{esc(v)}</strong><span>{esc(k)}</span></div>'
        for v, k in cards
    ) + "</div>"

    loan_rows = [
        [
            esc(r[0]), esc(r[1]), esc(r[2]), esc(r[3]),
            f"{r[4]:,}" if r[4] is not None else "—",
            fmt_money(r[5]), fmt_money(r[6]), fmt_money(r[7]),
            esc(status_label(r[8])), esc(r[9]),
        ]
        for r in loans
    ]
    sam_rows = [
        [
            esc(r[0]), esc(r[1]), esc(r[2]),
            fmt_money(r[3]), f"{int(r[4]):,}" if r[4] is not None else "—",
        ]
        for r in sam
    ]
    ch_rows = [
        [esc(r[0]), esc(r[1]), fmt_money(r[2])]
        for r in charities
    ]
    title = cluster.get("sample_address") or key
    maps = google_maps_url(str(title))
    loans_block = (
        ts_mount(
            "ts-loans",
            table_html(
                ['Borrower', 'City', 'St', 'ZIP', 'Jobs', 'Approved',
                 'Forgiven', 'Leftover', 'Status', 'Lender'],
                loan_rows, {4, 5, 6, 7},
            ),
        )
        if loan_rows else '<p class="meta">No PPP loans on this key.</p>'
    )
    sam_block = (
        ts_mount(
            "ts-sam",
            table_html(['Name', 'UEI', 'CAGE', 'Obligated', 'Awards'], sam_rows, {3, 4}),
        )
        if sam_rows else '<p class="meta">No SAM entities on this key.</p>'
    )
    ch_block = (
        ts_mount("ts-charities", table_html(['Name', 'EIN', 'Govt $'], ch_rows, {2}))
        if ch_rows else '<p class="meta">No charity rows on this key.</p>'
    )
    st = state or (str(cluster.get("state") or "").strip() or None)
    if depth >= 3 and st:
        crumbs = crumbs_by_state_detail(
            focus=domain, slice_by=slice_by, state=st, detail_label=str(title),
        )
    else:
        crumbs = crumbs_national_detail(
            focus=domain, slice_by=slice_by, detail_label=str(title),
        )
    geo_root = reports_dir
    if geo_root is None:
        geo_root = out.parent if depth < 3 else out.parent.parent.parent
    widen = widen_html(
        domain=domain,
        slice_by=slice_by,
        cluster=cluster,
        reports_dir=geo_root,
        output_dir=out,
        context=context,
    )
    roots = list(search_roots) if search_roots is not None else [geo_root]
    hits = build_overlap_hits(
        domain=domain,
        slice_by=slice_by,
        cluster=cluster,
        types=types,
        reports_dir=geo_root,
        output_dir=out,
        search_roots=roots,
    )
    bad = bad_address_html(hits)
    links = type_hrefs(hits)
    type_rows = []
    for t, n in types:
        href = links.get(t)
        label = f'<a href="{esc(href)}">{esc(t)}</a>' if href else esc(t)
        type_rows.append([label, f"{int(n):,}"])
    types_block = (
        ts_mount("ts-types", table_html(['Type', 'Rows'], type_rows, {1}))
        if type_rows else '<p class="meta">None.</p>'
    )
    tables = detail_tables_payload(bundle, links)
    extra_foot = ""
    if tables:
        extra_foot = (
            f"<script>window.__TS_TABLES__ = {dumps_table_json(tables)};</script>\n"
            + tanstack_assets_html()
        )
    body = f"""
{widen}
{bad}
<h1><a href="{esc(maps)}">{esc(title)}</a></h1>
<p class="meta">{esc(slice_by)} · {esc(key)} · {esc(cluster.get('state'))} · generated {esc(stamp())}.
Click a column header to sort.</p>
{card_html}
{map_html}
<h2>Address types here</h2>
{types_block}
<h2>PPP loans</h2>
{loans_block}
<h2>SAM / FY2025 $</h2>
{sam_block}
<h2>Charities</h2>
{ch_block}
"""
    out.mkdir(parents=True, exist_ok=True)
    (out / "data").mkdir(exist_ok=True)
    (out / "data" / f"{cluster['slug']}.json").write_text(
        json.dumps(
            {
                "cluster_key": key,
                "sample_address": cluster.get("sample_address"),
                "state": cluster.get("state") or st,
                "zip_code": cluster.get("zip_code"),
                "colocator": cluster.get("colocator"),
                "latitude": cluster.get("latitude"),
                "longitude": cluster.get("longitude"),
                "slice_by": slice_by,
                "domain": domain,
                "types": [[t, int(n)] for t, n in types],
            },
            default=str,
        ),
        encoding="utf-8",
    )
    (out / f"{cluster['slug']}.html").write_text(
        wrap(str(title)[:80], body, extra_foot=extra_foot, breadcrumbs=crumbs),
        encoding="utf-8",
    )


def write_suite(
    con,
    domain: str,
    slice_by: str,
    out_root: Path,
    limit: int,
    *,
    skip_details: bool = False,
) -> int:
    clusters = fetch_clusters(con, domain, slice_by, limit)
    dest = out_root / f"{domain}_{slice_by}"
    cluster_index_page(domain, slice_by, clusters, dest, depth=1)
    if skip_details:
        print(f"  {domain} {slice_by}: {len(clusters)} clusters (index only) → {dest}", flush=True)
        return len(clusters)
    keys = [c["cluster_key"] for c in clusters]
    bundle = fetch_detail_bundle(con, slice_by, keys)
    roots = [out_root, *extra_overlap_roots()]
    for c in clusters:
        detail_page(
            domain, slice_by, c, dest, bundle.get(str(c["cluster_key"]), {}),
            depth=1, reports_dir=out_root, context="national",
            search_roots=roots,
        )
    keep = {"index.html"} | {f"{c['slug']}.html" for c in clusters}
    for p in dest.glob("*.html"):
        if p.name not in keep:
            p.unlink()
    print(f"  {domain} {slice_by}: {len(clusters)} clusters → {dest}", flush=True)
    return len(clusters)


def by_state_dirname(domain: str, slice_by: str) -> str:
    """Colocator by-state keeps the original {domain}_by_state dirname."""
    if slice_by == "colocator":
        return f"{domain}_by_state"
    return f"{domain}_{slice_by}_by_state"


def state_stats(con, domain: str, slice_by: str) -> list[dict]:
    sl = SLICES[slice_by]
    if domain != "federal":
        cut = ppp_cut(domain)
        heat = cut["heatmap"]
        rows = con.execute(
            f"""
            SELECT UPPER(TRIM(a.state)) AS state,
                   SUM(p.current_approval_amount - COALESCE(p.forgiveness_amount, 0))::DOUBLE AS leftover,
                   SUM(COALESCE(p.forgiveness_amount, 0))::DOUBLE AS forgiven,
                   SUM(p.current_approval_amount)::DOUBLE AS approved,
                   SUM(COALESCE(p.jobs_reported, 0))::DOUBLE AS jobs_n,
                   SUM(p.current_approval_amount)
                     / NULLIF(SUM(COALESCE(p.jobs_reported, 0)), 0) AS per_job,
                   SUM(COALESCE(p.forgiveness_amount, 0))
                     / NULLIF(SUM(p.current_approval_amount), 0) AS pct,
                   COUNT(*)::BIGINT AS loans,
                   COUNT(DISTINCT ({sl['key']}))::BIGINT AS clusters
            FROM ppp_loans p
            JOIN Addresses a ON a.owner_id = p.id AND a.address_type = 'ppp_borrower'
            LEFT JOIN Geocoding g ON g.geocoding_id = a.geocoding_id
            WHERE a.state IS NOT NULL AND TRIM(a.state) != ''
              AND {sl['where']}
              AND ({sl['key']}) IS NOT NULL
              AND ({cut['where']})
            GROUP BY 1
            HAVING {cut['having']}
            """
        ).fetchall()
        stats = []
        for row in rows:
            (
                st, leftover, forgiven, approved, jobs_n, per_job, pct,
                loans, clusters,
            ) = row
            if not st or len(st) > 2:
                continue
            rec = {
                "state": st,
                "leftover": float(leftover or 0),
                "forgiven": float(forgiven or 0),
                "approved": float(approved or 0),
                "jobs_n": float(jobs_n or 0),
                "per_job": float(per_job or 0),
                "pct": float(pct or 0),
                "loans": int(loans or 0),
                "pass_clusters": int(clusters or 0),
            }
            heat_v = float(rec.get(heat) or 0)
            rec["show_n"] = min(SHOW_CAP, max(1, int(clusters) // 10)) if heat_v else 0
            rec["focus_amount"] = heat_v
            stats.append(rec)
        stats.sort(key=lambda s: s["focus_amount"], reverse=True)
        return stats
    rows = con.execute(
        f"""
        SELECT UPPER(TRIM(a.state)) AS state,
               SUM(u.obligation)::DOUBLE AS dollars,
               COUNT(*)::BIGINT AS ueis,
               COUNT(DISTINCT ({sl['key']}))::BIGINT AS clusters
        FROM usaspending_by_uei u
        JOIN sam_entities s ON s.uei = u.uei
        JOIN Addresses a ON a.owner_id = s.id AND a.address_type = 'sam_entity_phy'
        LEFT JOIN Geocoding g ON g.geocoding_id = a.geocoding_id
        WHERE a.state IS NOT NULL AND TRIM(a.state) != ''
          AND {sl['where']}
          AND ({sl['key']}) IS NOT NULL
        GROUP BY 1
        HAVING dollars > 0
        """
    ).fetchall()
    stats = []
    for st, dollars, ueis, clusters in rows:
        if not st or len(st) > 2:
            continue
        show = min(SHOW_CAP, max(1, int(clusters) // 10)) if dollars else 0
        stats.append({
            "state": st,
            "dollars": float(dollars or 0),
            "ueis": int(ueis or 0),
            "pass_clusters": int(clusters or 0),
            "show_n": show,
            "focus_amount": float(dollars or 0),
        })
    stats.sort(key=lambda s: s["dollars"], reverse=True)
    attach_federal_weights(stats)
    return stats


def federal_extra_heatmaps(stats: list[dict], slice_label: str) -> str:
    """$/pop and $/electoral-vote maps. Recipient HQ, not place of performance."""
    heat_pop = render_heatmap_svg(
        stats,
        value_key="dollars_per_pop",
        href_template="states/{state}/index.html",
        title=f"FY2025 federal $ / pop by state · {slice_label}",
        value_label="$ / resident",
        money=True,
    )
    heat_ev = render_heatmap_svg(
        stats,
        value_key="dollars_per_ev",
        href_template="states/{state}/index.html",
        title=f"FY2025 federal $ / electoral vote by state · {slice_label}",
        value_label="$ / electoral vote",
        money=True,
    )
    blurb = f"""
<div class="meta" style="max-width:52rem;line-height:1.5">
<p>All three maps use FY2025 USASpending obligations rolled to the
<strong>SAM physical HQ state</strong>, not place of performance.
{esc(CENSUS_POP_SOURCE)}. {esc(EV_SOURCE)}</p>
<p><strong>Top map (total $):</strong> California and Texas run hot because
they host more SAM headquarters.</p>
<p><strong>Middle map ($ / pop):</strong> same dollars divided by Census
Vintage 2024 resident population. DC runs hot: agencies and contractors
file HQ in the District.</p>
<p><strong>Bottom map ($ / electoral vote):</strong> same dollars divided by
the 2024 College (538). A small state can outrank a large one.</p>
</div>
"""
    return f"<section>{heat_pop}</section>\n<section>{heat_ev}</section>\n{blurb}"


def write_by_state_index(
    dest: Path,
    domain: str,
    slice_by: str,
    stats: list[dict],
) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    if domain == "federal":
        value_key = "dollars"
        label = "FY2025 federal $"
    else:
        cut = ppp_cut(domain)
        value_key = cut["heatmap"]
        label = cut["heatmap_label"]
    slice_label = SLICES[slice_by]["label"]
    heatmap = render_heatmap_svg(
        stats,
        value_key=value_key,
        href_template="states/{state}/index.html",
        title=f"{label} by state · {slice_label}",
        value_label=label,
        money=True,
    )
    extra_maps = (
        federal_extra_heatmaps(stats, slice_label) if domain == "federal" else ""
    )
    rows = []
    headers = ["State", label, "Clusters", "Show", ""]
    numeric = {1, 2, 3}
    if domain == "federal":
        headers = [
            "State", label, "$ / pop", "$ / EV", "Pop", "EV", "Clusters", "Show", "",
        ]
        numeric = {1, 2, 3, 4, 5, 6, 7}
    for s in stats:
        link = (
            f'<a href="states/{esc(s["state"])}/index.html">Open →</a>'
            if s["show_n"] else "—"
        )
        metric = fmt_money(s.get(value_key))
        if domain == "federal":
            pop = s.get("pop")
            ev = s.get("electoral_votes")
            rows.append([
                esc(s["state"]),
                metric,
                fmt_money(s.get("dollars_per_pop")),
                fmt_money(s.get("dollars_per_ev")),
                f"{int(pop):,}" if pop else "—",
                f"{int(ev):,}" if ev else "—",
                f"{s['pass_clusters']:,}",
                f"{s['show_n']}",
                link,
            ])
        else:
            rows.append([
                esc(s["state"]), metric, f"{s['pass_clusters']:,}", f"{s['show_n']}", link,
            ])
    payload = state_table_payload(domain, stats, value_key, label)
    extra_foot = (
        f"<script>window.__CLUSTER_TABLE__ = {dumps_table_json(payload)};</script>\n"
        + tanstack_assets_html()
    )
    body = f"""
<h1>{esc(label)} by state · {esc(slice_label)}</h1>
<p>Heat is {esc(label)}. Click a state for its top {esc(slice_label)} clusters
(cap min({SHOW_CAP}, 10% of clusters)). Click a column header to sort.</p>
<p class="meta">Generated {esc(stamp())}.</p>
<section>{heatmap}</section>
{extra_maps}
<div id="ts-table-root"></div>
<noscript>
{table_html(headers, rows, numeric)}
</noscript>
"""
    (dest / "index.html").write_text(
        wrap(
            f"{label} by state · {slice_label}",
            body,
            extra_foot=extra_foot,
            breadcrumbs=crumbs_by_state_us(focus=domain, slice_by=slice_by),
        ),
        encoding="utf-8",
    )
    (dest / "data").mkdir(exist_ok=True)
    (dest / "data" / "state_stats.json").write_text(
        json.dumps(
            {
                "domain": domain,
                "slice_by": slice_by,
                "states": stats,
                "generated_at": stamp(),
            },
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )


def write_by_state(
    con,
    domain: str,
    slice_by: str,
    out_root: Path,
    *,
    heatmaps_only: bool = False,
    skip_details: bool = False,
) -> None:
    dest = out_root / by_state_dirname(domain, slice_by)
    stats = state_stats(con, domain, slice_by)
    write_by_state_index(dest, domain, slice_by, stats)
    if heatmaps_only:
        print(f"  {domain} {slice_by} heatmaps → {dest}", flush=True)
        return
    for s in stats:
        if s["show_n"] <= 0:
            continue
        st = s["state"]
        clusters = fetch_clusters(con, domain, slice_by, s["show_n"], state=st)
        st_dir = dest / "states" / st
        cluster_index_page(
            domain, slice_by, clusters, st_dir,
            depth=3, heading_prefix=f"{st} · ", state=st,
        )
        if skip_details:
            print(f"  {domain} {slice_by} state {st}: {len(clusters)} (index only)", flush=True)
            continue
        keys = [c["cluster_key"] for c in clusters]
        bundle = fetch_detail_bundle(con, slice_by, keys)
        roots = [out_root, *extra_overlap_roots()]
        for c in clusters:
            detail_page(
                domain, slice_by, c, st_dir,
                bundle.get(str(c["cluster_key"]), {}),
                depth=3, reports_dir=out_root, context="by_state", state=st,
                search_roots=roots,
            )
        print(f"  {domain} {slice_by} state {st}: {len(clusters)}", flush=True)


def write_hub(out: Path, counts: dict) -> None:
    """Local listing of geo suites. Public lanes live on Fun with Geography."""
    names = []
    if out.is_dir():
        for child in sorted(out.iterdir()):
            if child.is_dir() and (child / "index.html").is_file():
                names.append(child.name)
    lis = "".join(
        f'<li><a href="{esc(n)}/index.html">{esc(n)}</a></li>' for n in names
    )
    bits = " · ".join(
        f"{esc(k)} {v}" if not isinstance(v, int) else f"{esc(k)} {v:,}"
        for k, v in counts.items()
    )
    (out / "index.html").write_text(
        f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<title>PPP / federal geography</title>
<style>{PAGE_CSS}</style></head><body>
<h1>PPP / federal geography</h1>
<p>These suites wrap onto Fun with Geography as PPP Charged Off / Not yet
paid / Forgiven / Payroll / Zero jobs, plus Federal spending
(Address / Colocator / Zipcode / Loose Colocator).</p>
<ul>{lis}</ul>
<p class="meta">{esc(bits)}<br>Generated {esc(stamp())}</p>
</body></html>
""",
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db-path", default=DEFAULT_DB)
    parser.add_argument("--output-dir", default=str(DEFAULT_OUT))
    parser.add_argument("--max-clusters", type=int, default=MAX_N)
    parser.add_argument(
        "--only",
        default="all",
        help="all, leftover ppp, federal, a cut (ppp_charged_off, …), or "
        "comma-separated suite names (ppp_address, ppp_charged_off_colocator, "
        "federal_zipcode_by_state, ppp_by_state, …). Token ppp is leftover "
        "only and does not match the five cut domains.",
    )
    parser.add_argument(
        "--heatmaps-only",
        action="store_true",
        help="Rewrite by-state index heatmaps and state_stats.json; skip "
        "national suites and per-state cluster pages.",
    )
    parser.add_argument(
        "--national-only",
        action="store_true",
        help="Write national cluster suites; skip by-state dirs.",
    )
    parser.add_argument(
        "--skip-details",
        action="store_true",
        help="Rewrite cluster and by-state indexes; leave existing detail HTML.",
    )
    args = parser.parse_args()
    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    con = connect(args.db_path)
    only = args.only.strip().lower()
    tokens = {x.strip() for x in only.split(",") if x.strip()}
    counts = {}

    def want(name: str) -> bool:
        return want_suite(name, tokens)

    for domain in (*PPP_DOMAINS, "federal"):
        for sl in SLICES:
            name = f"{domain}_{sl}"
            if want(name) and not args.heatmaps_only:
                counts[name] = write_suite(
                    con, domain, sl, out, args.max_clusters,
                    skip_details=args.skip_details,
                )
            bs = by_state_dirname(domain, sl)
            if (
                not args.national_only
                and (want(bs) or want(f"{domain}_{sl}_by_state"))
            ):
                write_by_state(
                    con, domain, sl, out,
                    heatmaps_only=args.heatmaps_only,
                    skip_details=args.skip_details,
                )
                counts[bs] = "ok"

    write_hub(out, counts)
    if not args.heatmaps_only:
        w = refresh_geo_widen(out)
        print(
            f"  widen refresh pages={w['pages']} updated={w['updated']} "
            f"skipped={w['skipped']}",
            flush=True,
        )
        o = refresh_geo_overlap(out)
        print(
            f"  overlap refresh pages={o['pages']} updated={o['updated']} "
            f"skipped={o['skipped']}",
            flush=True,
        )
    print(f"wrote {out} {counts}", flush=True)


if __name__ == "__main__":
    main()
