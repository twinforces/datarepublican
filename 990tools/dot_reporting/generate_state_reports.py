#!/usr/bin/env python3
"""
generate_state_reports.py — Per-state cluster reports + national heatmap index.

Uses Addresses.state (column) — not parsed from street text.

Flow:
  1. Count pass clusters per state (same thresholds as national reports).
  2. show_n(state) = min(100, max(1, pass_clusters // 10))  # ≤10%
  3. Emit national heatmap index + states/XX/index.html + detail pages.

Location slices:
  address  — cluster key = canonical_address, partitioned by Addresses.state
  zipcode  — cluster key = 5-digit zip, partitioned by Addresses.state
  colocator / loose_colocator — cluster key as usual, partitioned by Addresses.state
    (rows missing state are dropped from by-state views)

Usage:
  python generate_state_reports.py --focus dot --slice-by address
  python generate_state_reports.py --focus medicare --slice-by zipcode --max-states 10
  python generate_state_reports.py --research-only
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any

import duckdb
from mako.template import Template

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from generate_address_reports import (  # noqa: E402
    CSS,
    SLICE_MODES,
    fmt_money,
    google_maps_url,
    is_po_box,
    load_physical_notes,
    physical_note_for,
    resolve_slice_mode,
    slugify_address,
    reason_codes,
    suspicion_score,
    fetch_charities,
    fetch_officers,
    fetch_grants,
    fetch_dot_carriers,
    fetch_address_subgroups,
    group_dot_by_address,
)
from state_research import FOCUSES, US_STATES, show_cap, open_db  # noqa: E402
from us_state_map import render_heatmap_svg  # noqa: E402
from collections import defaultdict  # noqa: E402

DEFAULT_DB = "/Volumes/Data/final/irs990.duckdb"


def _type_sql_for_focus(focus: str) -> str:
    if focus == "dot":
        return "address_type IN ('dot_carrier_phy', 'dot_carrier_mail')"
    return FOCUSES[focus]["type_sql"] if focus in FOCUSES else "TRUE"


def research_states(
    con: duckdb.DuckDBPyConnection,
    focus: str,
    slice_by: str,
    min_multi: int,
    min_focus: int,
) -> list[dict[str, Any]]:
    """Pass-cluster counts by **Addresses.state** column for this focus + slice."""
    mode = resolve_slice_mode(slice_by, con, lat_long=False)
    key = mode["key_expr"]
    where = mode["where"]
    from_sql = mode.get("from_sql", "FROM Addresses")
    type_sql = _type_sql_for_focus(focus)  # uses bare address_type (set in keyed)

    if mode.get("needs_geocoding_join") == "1":
        # colocator path: alias a / g already in key/where/from
        keyed = f"""
        SELECT
            UPPER(TRIM(a.state)) AS st,
            ({key}) AS cluster_key,
            a.address_type AS address_type
        {from_sql}
        WHERE {where}
          AND a.state IS NOT NULL AND LENGTH(TRIM(a.state)) = 2
          AND ({key}) IS NOT NULL
          AND TRIM(CAST(({key}) AS VARCHAR)) != ''
        """
    else:
        keyed = f"""
        SELECT
            UPPER(TRIM(state)) AS st,
            ({key}) AS cluster_key,
            address_type
        {from_sql}
        WHERE {where}
          AND state IS NOT NULL AND LENGTH(TRIM(state)) = 2
          AND ({key}) IS NOT NULL
          AND TRIM(CAST(({key}) AS VARCHAR)) != ''
        """

    sql = f"""
    WITH keyed AS (
        {keyed}
    ),
    base AS (
        SELECT
            st,
            cluster_key,
            COUNT(*)::BIGINT AS total_rows,
            COUNT(DISTINCT address_type)::BIGINT AS multi,
            SUM(CASE WHEN {type_sql} THEN 1 ELSE 0 END)::BIGINT AS focus_n
        FROM keyed
        GROUP BY st, cluster_key
    )
    SELECT
        st,
        COUNT(*)::BIGINT AS pass_clusters,
        SUM(focus_n)::BIGINT AS focus_rows
    FROM base
    WHERE multi >= ? OR focus_n >= ?
    GROUP BY st
    ORDER BY pass_clusters DESC
    """
    rows = con.execute(sql, [min_multi, min_focus]).fetchall()
    out = []
    have = set()
    for st, pass_c, focus_rows in rows:
        st = (st or "").upper()
        if st not in US_STATES:
            continue
        have.add(st)
        pc = int(pass_c)
        out.append(
            {
                "state": st,
                "pass_clusters": pc,
                "focus_rows": int(focus_rows or 0),
                "show_n": show_cap(pc),
            }
        )
    for st in US_STATES:
        if st not in have:
            out.append({"state": st, "pass_clusters": 0, "focus_rows": 0, "show_n": 0})
    out.sort(key=lambda s: (-s["pass_clusters"], s["state"]))
    return out


def fetch_state_clusters(
    con: duckdb.DuckDBPyConnection,
    focus: str,
    slice_by: str,
    state: str,
    min_multi: int,
    min_focus: int,
    limit: int,
) -> list[dict[str, Any]]:
    """Top `limit` clusters for one state, ranked by focus intensity."""
    mode = resolve_slice_mode(slice_by, con, lat_long=False)
    key = mode["key_expr"]
    where = mode["where"]
    from_sql = mode.get("from_sql", "FROM Addresses")
    type_sql = _type_sql_for_focus(focus)
    st = state.upper()

    if focus == "dot":
        rank_expr = "COALESCE(da.active_power_units, 0) DESC, b.focus_n DESC"
        metric_join = """
        LEFT JOIN (
            SELECT k.cluster_key,
                   SUM(CASE WHEN d.status_code = 'A' THEN COALESCE(d.power_units, 0) ELSE 0 END)::BIGINT
                       AS active_power_units
            FROM keyed k
            INNER JOIN dot_carriers d ON d.id = k.owner_id
                AND k.address_type IN ('dot_carrier_phy', 'dot_carrier_mail')
            GROUP BY k.cluster_key
        ) da ON da.cluster_key = b.cluster_key
        """
        extra_select = (
            "COALESCE(da.active_power_units, 0) AS active_power_units, "
            "0::DOUBLE AS focus_amount, "
            "b.distinct_focus_addresses"
        )
    elif focus == "fec":
        # Rank by FEC $ (sum of contribution/transaction/expenditure amounts)
        rank_expr = "COALESCE(m.focus_amount, 0) DESC, b.focus_n DESC"
        metric_join = f"""
        LEFT JOIN (
            SELECT cluster_key, SUM(amt)::DOUBLE AS focus_amount FROM (
                SELECT k.cluster_key, COALESCE(f.contribution_amount, 0) AS amt
                FROM keyed k
                INNER JOIN fec_individual_contributions f
                    ON f.id = k.owner_id AND k.address_type = 'fec_contributor'
                UNION ALL
                SELECT k.cluster_key, COALESCE(t.transaction_amount, 0)
                FROM keyed k
                INNER JOIN fec_committee_transactions t
                    ON t.id = k.owner_id AND k.address_type = 'fec_committee_transaction'
                UNION ALL
                SELECT k.cluster_key, COALESCE(o.expenditure_amount, 0)
                FROM keyed k
                INNER JOIN fec_operating_expenditures o
                    ON o.id = k.owner_id AND k.address_type = 'fec_operating_expenditure'
                UNION ALL
                SELECT k.cluster_key, COALESCE(c.spending_amount, 0)
                FROM keyed k
                INNER JOIN fec_candidate_spendings c
                    ON c.id = k.owner_id AND k.address_type = 'fec_candidate_spending'
            ) u GROUP BY cluster_key
        ) m ON m.cluster_key = b.cluster_key
        """
        extra_select = (
            "0::BIGINT AS active_power_units, "
            "COALESCE(m.focus_amount, 0) AS focus_amount, "
            "b.distinct_focus_addresses"
        )
    elif focus == "contractor":
        rank_expr = (
            "b.distinct_focus_addresses DESC, b.focus_n DESC, "
            "COALESCE(m.focus_amount, 0) DESC"
        )
        metric_join = """
        LEFT JOIN (
            SELECT k.cluster_key, COALESCE(SUM(c.amount), 0)::DOUBLE AS focus_amount
            FROM keyed k
            INNER JOIN Contractors c
                ON c.contractor_id = k.owner_id AND k.address_type = 'contractor'
            GROUP BY k.cluster_key
        ) m ON m.cluster_key = b.cluster_key
        """
        extra_select = (
            "0::BIGINT AS active_power_units, "
            "COALESCE(m.focus_amount, 0) AS focus_amount, "
            "b.distinct_focus_addresses"
        )
    elif focus == "grants":
        from grant_suppress import suppressed_sql_predicate  # noqa: WPS433

        keep = suppressed_sql_predicate("g.grantee_name")
        rank_expr = "COALESCE(m.focus_amount, 0) DESC, b.focus_n DESC"
        metric_join = f"""
        LEFT JOIN (
            SELECT k.cluster_key, COALESCE(SUM(g.grant_amt), 0)::DOUBLE AS focus_amount
            FROM keyed k
            INNER JOIN Grants g
                ON g.grant_id = k.owner_id AND k.address_type = 'grant'
            WHERE {keep}
            GROUP BY k.cluster_key
        ) m ON m.cluster_key = b.cluster_key
        """
        extra_select = (
            "0::BIGINT AS active_power_units, "
            "COALESCE(m.focus_amount, 0) AS focus_amount, "
            "b.distinct_focus_addresses"
        )
    else:  # medicare — $ via NPI rollup (not 230M line grain)
        from generate_focus_reports import ensure_medicare_rollup  # noqa: WPS433

        has_rollup = ensure_medicare_rollup(con)
        rank_expr = "COALESCE(m.focus_amount, 0) DESC, b.focus_n DESC"
        if has_rollup:
            metric_join = """
            LEFT JOIN (
                SELECT
                    k.cluster_key,
                    COALESCE(SUM(r.total_paid), 0)::DOUBLE AS focus_amount
                FROM keyed k
                INNER JOIN medicare_providers mp
                    ON mp.id = k.owner_id
                   AND k.address_type IN ('nppes_practice', 'nppes_mailing')
                INNER JOIN medicare_provider_rollup r ON r.npi = mp.npi
                GROUP BY k.cluster_key
            ) m ON m.cluster_key = b.cluster_key
            """
            extra_select = (
                "0::BIGINT AS active_power_units, "
                "COALESCE(m.focus_amount, 0) AS focus_amount, "
                "b.distinct_focus_addresses"
            )
        else:
            rank_expr = "b.focus_n DESC, b.total_rows DESC"
            metric_join = ""
            extra_select = (
                "0::BIGINT AS active_power_units, "
                "0::DOUBLE AS focus_amount, "
                "b.distinct_focus_addresses"
            )

    if mode.get("needs_geocoding_join") == "1":
        sql = f"""
        WITH keyed AS (
            SELECT
                ({key}) AS cluster_key,
                a.canonical_address AS canonical_address,
                a.address_type AS address_type,
                a.owner_id AS owner_id
            {from_sql}
            WHERE {where}
              AND UPPER(TRIM(a.state)) = ?
              AND ({key}) IS NOT NULL
              AND TRIM(CAST(({key}) AS VARCHAR)) != ''
        ),
        base AS (
            SELECT
                cluster_key,
                COUNT(*)::BIGINT AS total_rows,
                COUNT(DISTINCT address_type)::BIGINT AS multi_type_count,
                LIST(DISTINCT address_type ORDER BY address_type) AS address_types,
                SUM(CASE WHEN {type_sql} THEN 1 ELSE 0 END)::BIGINT AS focus_n,
                COUNT(DISTINCT CASE WHEN {type_sql}
                    THEN COALESCE(NULLIF(TRIM(canonical_address), ''), CAST(owner_id AS VARCHAR))
                    END)::BIGINT AS distinct_focus_addresses,
                SUM(CASE WHEN address_type IN ('dot_carrier_phy','dot_carrier_mail')
                         THEN 1 ELSE 0 END)::BIGINT AS dot_carrier_count,
                SUM(CASE WHEN address_type = 'charity' THEN 1 ELSE 0 END)::BIGINT AS charity_count,
                SUM(CASE WHEN address_type = 'grant' THEN 1 ELSE 0 END)::BIGINT AS grant_count,
                SUM(CASE WHEN address_type = 'officer' THEN 1 ELSE 0 END)::BIGINT AS officer_count,
                ANY_VALUE(CASE WHEN canonical_address IS NOT NULL AND TRIM(canonical_address) != ''
                          THEN canonical_address END) AS sample_address
            FROM keyed
            GROUP BY cluster_key
        )
        SELECT
            b.cluster_key, b.sample_address, b.total_rows, b.multi_type_count,
            b.address_types, b.focus_n, b.dot_carrier_count, b.charity_count,
            b.grant_count, b.officer_count, {extra_select}
        FROM base b
        {metric_join}
        WHERE b.multi_type_count >= ? OR b.focus_n >= ?
        ORDER BY {rank_expr}
        LIMIT ?
        """
    else:
        sql = f"""
        WITH keyed AS (
            SELECT
                ({key}) AS cluster_key,
                canonical_address,
                address_type,
                owner_id
            {from_sql}
            WHERE {where}
              AND UPPER(TRIM(state)) = ?
              AND ({key}) IS NOT NULL
              AND TRIM(CAST(({key}) AS VARCHAR)) != ''
        ),
        base AS (
            SELECT
                cluster_key,
                COUNT(*)::BIGINT AS total_rows,
                COUNT(DISTINCT address_type)::BIGINT AS multi_type_count,
                LIST(DISTINCT address_type ORDER BY address_type) AS address_types,
                SUM(CASE WHEN {type_sql} THEN 1 ELSE 0 END)::BIGINT AS focus_n,
                COUNT(DISTINCT CASE WHEN {type_sql}
                    THEN COALESCE(NULLIF(TRIM(canonical_address), ''), CAST(owner_id AS VARCHAR))
                    END)::BIGINT AS distinct_focus_addresses,
                SUM(CASE WHEN address_type IN ('dot_carrier_phy','dot_carrier_mail')
                         THEN 1 ELSE 0 END)::BIGINT AS dot_carrier_count,
                SUM(CASE WHEN address_type = 'charity' THEN 1 ELSE 0 END)::BIGINT AS charity_count,
                SUM(CASE WHEN address_type = 'grant' THEN 1 ELSE 0 END)::BIGINT AS grant_count,
                SUM(CASE WHEN address_type = 'officer' THEN 1 ELSE 0 END)::BIGINT AS officer_count,
                ANY_VALUE(CASE WHEN canonical_address IS NOT NULL AND TRIM(canonical_address) != ''
                          THEN canonical_address END) AS sample_address
            FROM keyed
            GROUP BY cluster_key
        )
        SELECT
            b.cluster_key, b.sample_address, b.total_rows, b.multi_type_count,
            b.address_types, b.focus_n, b.dot_carrier_count, b.charity_count,
            b.grant_count, b.officer_count, {extra_select}
        FROM base b
        {metric_join}
        WHERE b.multi_type_count >= ? OR b.focus_n >= ?
        ORDER BY {rank_expr}
        LIMIT ?
        """

    # Grants: over-fetch then take top `limit` (SQL already ranks by clean $)
    sql_limit = limit
    if focus == "grants":
        sql_limit = max(limit * 10, min(1000, limit * 15))

    rows = con.execute(sql, [st, min_multi, min_focus, sql_limit]).fetchall()
    cols = [
        "cluster_key", "sample_address", "total_rows", "multi_type_count",
        "address_types", "focus_n", "dot_carrier_count", "charity_count",
        "grant_count", "officer_count", "active_power_units", "focus_amount",
        "distinct_focus_addresses",
    ]
    out = []
    for row in rows:
        c = dict(zip(cols, row))
        if isinstance(c["address_types"], str):
            c["address_types"] = [
                t.strip() for t in c["address_types"].strip("[]").split(",") if t.strip()
            ]
        # Map focus_n into fields used by trucking scoring/templates
        if focus == "dot":
            c["dot_carrier_count"] = int(c["focus_n"] or c["dot_carrier_count"] or 0)
        c["focus_count"] = int(c["focus_n"] or 0)
        c["max_grift_ratio"] = None
        c["misrep_count"] = 0
        c["state"] = st
        c["slice_by"] = slice_by
        c["canonical_address"] = str(c["cluster_key"])
        c["focus_amount_fmt"] = fmt_money(c.get("focus_amount"))
        if focus == "dot":
            c["rank_metric_value"] = int(c.get("active_power_units") or 0)
            c["rank_metric_fmt"] = f"{c['rank_metric_value']:,}"
        elif focus == "contractor":
            c["rank_metric_value"] = int(c.get("distinct_focus_addresses") or 0)
            c["rank_metric_fmt"] = f"{c['rank_metric_value']:,}"
        elif focus in ("fec", "grants"):
            c["rank_metric_value"] = float(c.get("focus_amount") or 0)
            c["rank_metric_fmt"] = c["focus_amount_fmt"]
        else:
            c["rank_metric_value"] = int(c.get("focus_count") or 0)
            c["rank_metric_fmt"] = f"{c['rank_metric_value']:,}"
        out.append(c)
    return out[:limit]


def render_tpl(name: str, **kwargs) -> str:
    from mako.lookup import TemplateLookup

    lookup = TemplateLookup(
        directories=[str(SCRIPT_DIR / "templates")],
        input_encoding="utf-8",
    )
    return lookup.get_template(name).render(css=CSS, **kwargs)


def write_state_suite(
    db_path: str,
    output_dir: Path,
    focus: str,
    slice_by: str,
    min_multi: int,
    min_focus: int,
    top_n: int,
    physical_notes_path: Path,
    *,
    max_states: int | None = None,
    research_only: bool = False,
) -> dict[str, Any]:
    con = open_db(db_path)
    try:
        print(
            f"Researching states focus={focus} slice={slice_by} "
            f"(using Addresses.state column)...",
            flush=True,
        )
        t0 = time.time()
        state_stats = research_states(con, focus, slice_by, min_multi, min_focus)
        print(f"  research done in {time.time()-t0:.1f}s", flush=True)

        total_pages = sum(s["show_n"] for s in state_stats)
        total_pass = sum(s["pass_clusters"] for s in state_stats)
        print(
            f"  pass_clusters={total_pass:,} detail_pages={total_pages:,} "
            f"(rule min(100, max(1, n//10)))",
            flush=True,
        )
        for s in state_stats[:15]:
            if s["pass_clusters"]:
                print(
                    f"    {s['state']}: pass={s['pass_clusters']:,} → show {s['show_n']}",
                    flush=True,
                )

        output_dir.mkdir(parents=True, exist_ok=True)
        data_dir = output_dir / "data"
        data_dir.mkdir(exist_ok=True)
        (data_dir / "state_stats.json").write_text(
            json.dumps(
                {
                    "focus": focus,
                    "slice_by": slice_by,
                    "state_column": "Addresses.state",
                    "cap_rule": "min(100, max(1, pass_clusters // 10))",
                    "min_multi": min_multi,
                    "min_focus": min_focus,
                    "total_pass_clusters": total_pass,
                    "total_detail_pages": total_pages,
                    "states": state_stats,
                    "generated_at": datetime.now().isoformat(timespec="seconds"),
                },
                indent=2,
            ),
            encoding="utf-8",
        )

        # National heatmap index
        label = FOCUSES.get(focus, {}).get("label", focus)
        heatmap = render_heatmap_svg(
            state_stats,
            value_key="pass_clusters",
            href_template="states/{state}/index.html",
            title=f"{label} · {slice_by} · clusters by Addresses.state",
        )
        from breadcrumbs import crumbs_by_state_us  # noqa: WPS433

        bc = crumbs_by_state_us(focus=focus, slice_by=slice_by)
        bc_html = (
            '<nav class="breadcrumbs" aria-label="Breadcrumb">'
            + ' <span class="bc-sep">›</span> '.join(
                (
                    f'<a href="{c["href"]}">{c["label"]}</a>'
                    if c.get("href")
                    else f'<span class="bc-current">{c["label"]}</span>'
                )
                for c in bc
            )
            + "</nav>"
        )
        index_html = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<title>{label} — US heatmap</title>
<style>{CSS}
.legend {{ margin: 1rem 0; font-size: 0.9rem; color: #444; }}
table.state-table {{ font-size: 0.85rem; }}
</style></head><body>
{bc_html}
<header>
  <h1>{label} by state</h1>
  <p class="meta">Slice: {slice_by} · State column: <code>Addresses.state</code> ·
  Cap: min(100, max(1, pass//10)) · {total_pass:,} pass clusters → {total_pages:,} detail pages
  · Generated {datetime.now().isoformat(timespec="seconds")}</p>
</header>
<section>{heatmap}</section>
<section>
  <h2>All states</h2>
  <table class="state-table">
    <thead><tr><th>State</th><th>Pass clusters</th><th>Show</th><th>Focus rows</th><th></th></tr></thead>
    <tbody>
"""
        for s in state_stats:
            if s["show_n"] > 0:
                link = f'<a href="states/{s["state"]}/index.html">Open →</a>'
            else:
                link = "—"
            index_html += (
                f"<tr><td>{s['state']}</td><td>{s['pass_clusters']:,}</td>"
                f"<td>{s['show_n']}</td><td>{s['focus_rows']:,}</td><td>{link}</td></tr>\n"
            )
        index_html += """</tbody></table></section>
<footer><p>Stats: <code>data/state_stats.json</code></p></footer>
</body></html>"""
        (output_dir / "index.html").write_text(index_html, encoding="utf-8")
        (output_dir / "README.md").write_text(
            f"""# {label} by state ({slice_by})

- State key: **`Addresses.state`** column (not parsed from street text)
- Cap per state: `min(100, max(1, pass_clusters // 10))` (≤10%)
- National heatmap: `index.html`
- State pages: `states/XX/index.html`

Pass clusters: {total_pass:,}
Detail pages: {total_pages:,}
""",
            encoding="utf-8",
        )

        if research_only:
            print(f"Research-only: wrote heatmap + stats to {output_dir}", flush=True)
            return {"total_pages": total_pages, "total_pass": total_pass}

        notes = load_physical_notes(physical_notes_path)
        generated_at = datetime.now().isoformat(timespec="seconds")
        active_states = [s for s in state_stats if s["show_n"] > 0]
        if max_states is not None:
            active_states = active_states[: max_states]

        # Detail generation reuses trucking entity fetchers (national key filter).
        # For state pages we filter by state in SQL when selecting clusters; entity
        # fetchers still filter by cluster_key only (correct — key is unique enough).
        for si, s in enumerate(active_states, 1):
            st = s["state"]
            show_n = s["show_n"]
            print(
                f"  [{si}/{len(active_states)}] {st}: top {show_n} of {s['pass_clusters']:,}...",
                flush=True,
            )
            st_dir = output_dir / "states" / st
            st_dir.mkdir(parents=True, exist_ok=True)
            st_data = st_dir / "data"
            st_data.mkdir(exist_ok=True)

            clusters = fetch_state_clusters(
                con, focus, slice_by, st, min_multi, min_focus, show_n
            )
            clusters_out = []
            for c in clusters:
                key = str(c["cluster_key"])
                sample = c.get("sample_address") or key
                slug = slugify_address(f"{st}-{slice_by}-{key}")
                detail_file = f"{slug}.html"
                phy = is_po_box(sample)
                c["phy_is_po_box"] = phy
                c["dot_active_count"] = 0
                c["dot_inactive_count"] = 0
                c["dot_active_power_units"] = int(c.get("active_power_units") or 0)
                c["dot_inactive_power_units"] = 0
                c["ia_ratio"] = 0
                c["inactive_pct"] = 0
                if focus == "dot":
                    # enrich with full DOT list for scoring
                    dots = fetch_dot_carriers(con, slice_by, key, 9999)
                    act = [d for d in dots if d.get("status_code") == "A"]
                    ina = [d for d in dots if d.get("status_code") == "I"]
                    c["dot_active_count"] = len(act)
                    c["dot_inactive_count"] = len(ina)
                    c["dot_active_power_units"] = sum(d.get("power_units") or 0 for d in act)
                    c["dot_inactive_power_units"] = sum(d.get("power_units") or 0 for d in ina)
                    tot = len(act) + len(ina)
                    c["ia_ratio"] = round(len(ina) / tot, 3) if tot else 0
                    c["inactive_pct"] = round(c["ia_ratio"] * 100, 1)
                    c["suspicion_score"] = suspicion_score(c)
                    c["reason_codes"] = reason_codes(c, min_multi, min_focus)
                else:
                    c["suspicion_score"] = int(c.get("focus_count") or 0) * 2
                    c["reason_codes"] = (
                        ["multi_type"] if c["multi_type_count"] >= min_multi else []
                    ) + (
                        [f"{focus}_stack"]
                        if c.get("focus_count", 0) >= min_focus
                        else []
                    ) or ["threshold"]

                c["maps_url"] = google_maps_url(sample)
                c["detail_file"] = detail_file
                c["slug"] = slug
                if slice_by == "address":
                    c["canonical_address"] = key
                else:
                    c["canonical_address"] = (
                        f"{key}  ·  e.g. {sample}" if sample != key else key
                    )
                clusters_out.append(c)

                charities = fetch_charities(con, slice_by, key, top_n)
                officers = fetch_officers(con, slice_by, key, top_n)
                grants = fetch_grants(con, slice_by, key, top_n)

                # Prefer focus detail template for non-dot; trucking for dot
                if focus == "dot":
                    # Full carrier list (same as national write_report): active by
                    # phone, inactive collapsed; also group by street address.
                    dots_full = fetch_dot_carriers(con, slice_by, key, 9999)
                    dots_disp = dots_full[:top_n]
                    phone_groups: dict[str, list] = defaultdict(list)
                    for d in dots_full:
                        phone_groups[d.get("phone") or "No Phone"].append(d)
                    phone_groups_sorted = sorted(
                        phone_groups.items(),
                        key=lambda item: sum(
                            x.get("power_units") or 0 for x in item[1]
                        ),
                        reverse=True,
                    )
                    address_subgroups: list = []
                    address_groups_sorted: list = []
                    if slice_by != "address":
                        address_subgroups = fetch_address_subgroups(
                            con, slice_by, key
                        )
                        address_groups_sorted = group_dot_by_address(dots_full)
                    c["distinct_address_count"] = (
                        len(address_subgroups)
                        if address_subgroups
                        else (1 if slice_by == "address" else 0)
                    )
                    from cluster_table_payload import (  # noqa: WPS433
                        build_detail_tables,
                        dumps_table_json,
                    )

                    detail_tables = build_detail_tables(
                        charities=charities,
                        officers=officers,
                        grants=grants,
                        address_subgroups=address_subgroups
                        if slice_by != "address"
                        else None,
                        phone_groups=phone_groups_sorted,
                        address_groups=address_groups_sorted,
                    )
                    from map_points import (  # noqa: WPS433
                        clusters_to_map_points,
                        single_point_from_key,
                    )

                    detail_map = clusters_to_map_points(
                        [c], slice_by=slice_by
                    ) or single_point_from_key(
                        key,
                        label=c.get("canonical_address") or key,
                        slice_by=slice_by,
                        sample_address=sample,
                    )
                    from breadcrumbs import crumbs_by_state_detail  # noqa: WPS433
                    from widen_links import build_widen_links  # noqa: WPS433

                    report_day = date.today().isoformat()
                    mday = re.search(r"(\d{4}-\d{2}-\d{2})", str(output_dir))
                    if mday:
                        report_day = mday.group(1)
                    widen_links = build_widen_links(
                        focus=focus,
                        slice_by=slice_by,
                        cluster_key=key,
                        sample_address=sample,
                        state=st,
                        report_day=report_day,
                        reports_dir=SCRIPT_DIR / "reports",
                        output_dir=st_dir,
                        conn=con,
                        context="by_state",
                    )
                    html = render_tpl(
                        "address_cluster_detail.mako",
                        cluster=c,
                        charities=charities,
                        officers=officers,
                        grants=grants,
                        dot_carriers=dots_disp,
                        phone_groups=phone_groups_sorted,
                        address_subgroups=address_subgroups,
                        address_groups=address_groups_sorted,
                        show_address_subgroups=(slice_by != "address"),
                        physical_note=physical_note_for(sample, notes),
                        generated_at=generated_at,
                        detail_tables_json=dumps_table_json(detail_tables),
                        map_points=detail_map,
                        breadcrumbs=crumbs_by_state_detail(
                            focus=focus,
                            slice_by=slice_by,
                            state=st,
                            detail_label=str(c.get("canonical_address") or key),
                        ),
                        widen_links=widen_links,
                    )
                else:
                    domain = FOCUSES.get(focus, {"label": focus, "entity_title": focus,
                                                  "amount_label": "$", "review_tag": focus})
                    # minimal entities: reuse grants/charities as secondary only
                    from cluster_table_payload import (  # noqa: WPS433
                        build_detail_tables,
                        dumps_table_json,
                    )

                    detail_tables = build_detail_tables(
                        charities=charities,
                        officers=officers,
                        grants=grants if focus != "grants" else [],
                        focus=focus,
                    )
                    from breadcrumbs import crumbs_by_state_detail  # noqa: WPS433
                    from widen_links import build_widen_links  # noqa: WPS433

                    report_day = date.today().isoformat()
                    mday = re.search(r"(\d{4}-\d{2}-\d{2})", str(output_dir))
                    if mday:
                        report_day = mday.group(1)
                    widen_links = build_widen_links(
                        focus=focus,
                        slice_by=slice_by,
                        cluster_key=key,
                        sample_address=sample,
                        state=st,
                        report_day=report_day,
                        reports_dir=SCRIPT_DIR / "reports",
                        output_dir=st_dir,
                        conn=con,
                        context="by_state",
                    )
                    html = render_tpl(
                        "focus_cluster_detail.mako",
                        cluster={
                            **c,
                            "focus_count": c.get("focus_count"),
                            "focus_amount_fmt": c.get("focus_amount_fmt")
                            or c.get("rank_metric_fmt")
                            or "—",
                            "focus_label": domain.get("label", focus),
                        },
                        entities=[],
                        charities=charities,
                        officers=officers,
                        grants=grants if focus != "grants" else [],
                        focus=focus,
                        domain=domain,
                        physical_note=physical_note_for(sample, notes),
                        generated_at=generated_at,
                        detail_tables_json=dumps_table_json(detail_tables),
                        breadcrumbs=crumbs_by_state_detail(
                            focus=focus,
                            slice_by=slice_by,
                            state=st,
                            detail_label=str(c.get("canonical_address") or key),
                        ),
                        widen_links=widen_links,
                    )
                (st_dir / detail_file).write_text(html, encoding="utf-8")

            # State index (TanStack table)
            from cluster_table_payload import (  # noqa: WPS433
                build_dot_cluster_table,
                build_focus_cluster_table,
                dumps_table_json,
            )

            from map_points import clusters_to_map_points  # noqa: WPS433

            if focus == "dot":
                for cc in clusters_out:
                    if cc.get("active_power_units") is None:
                        cc["active_power_units"] = cc.get("dot_active_power_units") or 0
                table_payload = build_dot_cluster_table(
                    clusters_out, min_dot_carriers=min_focus
                )
                rank_label = "Active PUs"
            else:
                rank_map = {
                    "medicare": ("focus_count", "Provider rows"),
                    "fec": ("focus_amount", "FEC $"),
                    "contractor": ("distinct_addresses", "Distinct addresses"),
                    "grants": ("focus_amount", "Grant $ (excl. suppressed)"),
                }
                rm, rl = rank_map.get(focus, ("focus_count", "Focus #"))
                table_payload = build_focus_cluster_table(
                    clusters_out,
                    focus=focus,
                    min_focus=min_focus,
                    rank_metric=rm,
                    rank_label=rl,
                )
                rank_label = rl

            map_points = clusters_to_map_points(clusters_out, slice_by=slice_by)
            if map_points:
                (st_data / "map_points.json").write_text(
                    json.dumps(map_points, indent=2), encoding="utf-8"
                )

            from breadcrumbs import crumbs_by_state_state  # noqa: WPS433

            st_index = render_tpl(
                "focus_cluster_index.mako" if focus != "dot" else "address_cluster_index.mako",
                clusters=clusters_out,
                cluster_count=len(clusters_out),
                generated_at=generated_at,
                report_date=date.today().isoformat(),
                db_path=db_path,
                min_multi_type=min_multi,
                min_focus=min_focus,
                min_dot_carriers=min_focus,
                require_grift_signal=False,
                slice_by=slice_by,
                slice_label=f"{slice_by} · state={st}",
                focus=focus,
                focus_label=f"{label} — {st}",
                domain=FOCUSES.get(focus, {"label": focus}),
                rank_label=rank_label,
                cluster_table_json=dumps_table_json(table_payload),
                map_points=map_points,
                breadcrumbs=crumbs_by_state_state(
                    focus=focus, slice_by=slice_by, state=st
                ),
            )
            (st_dir / "index.html").write_text(st_index, encoding="utf-8")
            with open(st_data / "clusters.json", "w", encoding="utf-8") as f:
                json.dump(clusters_out, f, indent=2, default=str)

        print(f"Done → {output_dir / 'index.html'}", flush=True)
        return {"total_pages": total_pages, "total_pass": total_pass, "output": str(output_dir)}
    finally:
        con.close()


def main() -> int:
    p = argparse.ArgumentParser(description="Per-state cluster reports + US heatmap")
    p.add_argument("--db-path", default=os.environ.get("IRS990_DB_PATH", DEFAULT_DB))
    p.add_argument(
        "--focus",
        default="dot",
        choices=["dot", "medicare", "fec", "contractor", "grants"],
    )
    p.add_argument(
        "--slice-by",
        default="address",
        choices=list(SLICE_MODES.keys()),
        help="Location slice; always partitioned by Addresses.state",
    )
    p.add_argument("--output-dir")
    p.add_argument("--physical-notes", default=str(SCRIPT_DIR / "physical_notes.json"))
    p.add_argument("--min-multi-type", type=int, default=None)
    p.add_argument("--min-focus", type=int, default=None)
    p.add_argument("--top-n", type=int, default=20)
    p.add_argument("--max-states", type=int, default=None, help="Limit states (debug)")
    p.add_argument(
        "--research-only",
        action="store_true",
        help="Only write heatmap + state_stats.json (no detail pages)",
    )
    args = p.parse_args()

    if args.focus == "dot":
        min_multi = args.min_multi_type if args.min_multi_type is not None else 7
        min_focus = args.min_focus if args.min_focus is not None else 50
    else:
        cfg = FOCUSES[args.focus]
        min_multi = (
            args.min_multi_type
            if args.min_multi_type is not None
            else cfg["min_multi"]
        )
        min_focus = (
            args.min_focus if args.min_focus is not None else cfg["min_focus"]
        )

    out = (
        Path(args.output_dir)
        if args.output_dir
        else SCRIPT_DIR
        / "reports"
        / f"{args.focus}_{args.slice_by}_by_state_{date.today().isoformat()}"
    )

    write_state_suite(
        db_path=args.db_path,
        output_dir=out,
        focus=args.focus,
        slice_by=args.slice_by,
        min_multi=min_multi,
        min_focus=min_focus,
        top_n=args.top_n,
        physical_notes_path=Path(args.physical_notes),
        max_states=args.max_states,
        research_only=args.research_only,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
