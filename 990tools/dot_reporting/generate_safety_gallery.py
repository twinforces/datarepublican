#!/usr/bin/env python3
"""Rogues gallery: FMCSA SMS harm stacked at a physical DOT colocator.

Join `dot_carrier_phy` Addresses.colocator (LL: only) to SMS crash / inspection /
BASIC rows by USDOT number.

Volume rank: fatalities, then crashes, then OOS, then BASIC-alert carriers.
Rate rank: one rotten apple stains the yard, on an expected-fatality scale.
Weights are universe-wide sum(fatalities)/sum(metric) (~0.030 per crash,
~0.0069 per OOS). Each BASIC category has its own weight
(sum(fatalities)/count of that flag) — unsafe driving is rare and heavy,
driver fitness is common and lighter. Lit flags add. Then max(fatalities,
sum of flag weights, weighted crashes, weighted OOS) / power units —
outside the max (yard totals) and inside (worst USDOT / its own trucks).
Stack rank: paperwork mill × bad apple — Apple/PU (worst USDOT harm) times
physical USDOT records at the yard. A mill is legal; a mill that did not vet
a killer or BASIC-alert driver ranks first. Admission is still a shared-phone
farm (paper extras on a small-harm mill phone, or ≥2 small fatality DOTs).

  python3 -u dot_reporting/generate_safety_gallery.py \
      --db-path /Volumes/Data/final/irs990.duckdb \
      --max-clusters 100
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import quote_plus
from zoneinfo import ZoneInfo

import duckdb

SCRIPT_DIR = Path(__file__).resolve().parent
_ROOT = SCRIPT_DIR.parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from cluster_table_payload import _dot_link_html
from cluster_table_payload import basic_flags_html
from cluster_table_payload import build_detail_tables as shared_detail_tables
from cluster_table_payload import rank_dot_html
from html_format import esc, linkify_zip_codes
from percentiles import MetricScale
from fmcsa_safety_processor import (
    BASIC_CATEGORIES,
    TERMS,
    census_phone_norm_sql,
    default_flag_weights,
    normalize_dot_sql,
    set_flag_weights,
    sms_per_dot_sql,
)

DEFAULT_DB = "/Volumes/Data/final/irs990.duckdb"
DEFAULT_OUT = SCRIPT_DIR / "reports" / "unsafe_colocators"
# Parking-lot vinyl: extras are paper (≤5 PU). Count them only when the
# harm DOTs on that phone are small too (≤50 PU) — a mill of 1-truck
# LLCs, not agent shells around United Van Lines.
PAPER_PU = 5
SMALL_HARM_PU = 50
# Universe-wide sum(fatalities)/sum(metric) in the current SMS window
# (2026-10-02: 7,675 deaths, 255,582 crash records, 1,109,047 OOS
# inspections, 2,324 lit BASIC flags). Same numerator for every metric.
SMS_CRASH_RECORDS = 255582
SMS_CRASH_FATALITIES = 7675
SMS_OOS_INSPECTIONS = 1109047
SMS_BASIC_FLAGS = 2324
SMS_BASIC_DOTS = 1678
FAT_PER_CRASH = SMS_CRASH_FATALITIES / SMS_CRASH_RECORDS  # ~0.0300
FAT_PER_OOS = SMS_CRASH_FATALITIES / SMS_OOS_INSPECTIONS  # ~0.0069
FAT_PER_FLAG = SMS_CRASH_FATALITIES / SMS_BASIC_FLAGS  # pooled; scoring uses per-flag
FAT_PER_FLAG_CAT = default_flag_weights()
FAT_PER_BASIC = FAT_PER_FLAG
FAT_PER_MAINT = FAT_PER_FLAG_CAT["maint"]
FAT_PER_UNSAFE = FAT_PER_FLAG_CAT["unsafe"]

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


def _th(label: str, key: str | None = None, *, cls: str = "") -> str:
    attrs = ""
    if cls:
        attrs += f' class="{esc(cls)}"'
    if key:
        attrs += f' title="{esc(TERMS[key])}"'
    return f"<th{attrs}>{esc(label)}</th>"

CLUSTER_SQL = """
WITH phy AS (
    SELECT
        a.colocator AS cluster_key,
        a.canonical_address,
        a.zip_code,
        a.latitude,
        a.longitude,
        {dot_norm} AS dot_norm,
        d.dot_number,
        d.legal_name,
        d.dba_name,
        d.power_units,
        d.status_code,
        d.phone
    FROM Addresses a
    JOIN dot_carriers d ON d.id = a.owner_id
    WHERE a.address_type = 'dot_carrier_phy'
      AND a.colocator LIKE 'LL:%'
      AND TRIM(a.colocator) != ''
),
sms AS (
    SELECT * FROM ({sms_per_dot}) sms_inner
),
scored AS (
    SELECT
        p.cluster_key,
        p.dot_norm,
        ANY_VALUE(p.canonical_address) AS canonical_address,
        ANY_VALUE(p.zip_code) AS zip_code,
        ANY_VALUE(p.latitude) AS latitude,
        ANY_VALUE(p.longitude) AS longitude,
        ANY_VALUE(p.legal_name) AS legal_name,
        ANY_VALUE(p.dot_number) AS dot_number,
        ANY_VALUE(p.power_units) AS power_units,
        ANY_VALUE(p.status_code) AS status_code,
        ANY_VALUE(p.phone) AS phone,
        COALESCE(ANY_VALUE(s.crashes), 0) AS crashes,
        COALESCE(ANY_VALUE(s.fatalities), 0) AS fatalities,
        COALESCE(ANY_VALUE(s.injuries), 0) AS injuries,
        COALESCE(ANY_VALUE(s.oos_inspections), 0) AS oos_inspections,
        COALESCE(ANY_VALUE(s.basic_alert), FALSE) AS basic_alert,
        COALESCE(ANY_VALUE(s.basic_flags), 0) AS basic_flags,
        COALESCE(ANY_VALUE(s.basic_unsafe), FALSE) AS basic_unsafe,
        COALESCE(ANY_VALUE(s.basic_hos), FALSE) AS basic_hos,
        COALESCE(ANY_VALUE(s.basic_fit), FALSE) AS basic_fit,
        COALESCE(ANY_VALUE(s.basic_drugs), FALSE) AS basic_drugs,
        COALESCE(ANY_VALUE(s.basic_maint), FALSE) AS basic_maint,
        COALESCE(ANY_VALUE(s.basic_cargo), FALSE) AS basic_cargo,
        COALESCE(ANY_VALUE(s.basic_crash), FALSE) AS basic_crash
    FROM phy p
    LEFT JOIN sms s ON s.dot_norm = p.dot_norm
    GROUP BY p.cluster_key, p.dot_norm
),
clustered AS (
SELECT
    cluster_key,
    ANY_VALUE(canonical_address) AS canonical_address,
    ANY_VALUE(zip_code) AS zip_code,
    ANY_VALUE(latitude) AS latitude,
    ANY_VALUE(longitude) AS longitude,
    COUNT(*)::BIGINT AS phy_carriers,
    SUM(COALESCE(power_units, 0))::BIGINT AS power_units,
    SUM(CASE WHEN status_code = 'A' THEN COALESCE(power_units, 0) ELSE 0 END)
        ::BIGINT AS active_power_units,
    SUM(CASE WHEN fatalities > 0 OR crashes > 0 OR oos_inspections > 0 OR basic_alert
             THEN 1 ELSE 0 END)::BIGINT AS hot_carriers_n,
    (COUNT(*) - SUM(CASE WHEN fatalities > 0 OR crashes > 0 OR oos_inspections > 0 OR basic_alert
                         THEN 1 ELSE 0 END))::BIGINT AS extra_identities,
    MAX(fatalities)::BIGINT AS worst_fatalities,
    MAX(oos_inspections)::BIGINT AS worst_oos,
    SUM(CASE WHEN fatalities > 0 THEN 1 ELSE 0 END)::BIGINT AS fatality_carriers,
    SUM(crashes)::BIGINT AS crashes,
    SUM(fatalities)::BIGINT AS fatalities,
    SUM(injuries)::BIGINT AS injuries,
    SUM(oos_inspections)::BIGINT AS oos_inspections,
    SUM(CASE WHEN basic_alert THEN 1 ELSE 0 END)::BIGINT AS basic_alert_carriers,
    SUM(basic_flags)::BIGINT AS basic_flags,
    {flag_count_sums},
    MAX(
        GREATEST(
            fatalities,
            ({flag_harm_dot}),
            {w_crash} * crashes,
            {w_oos} * oos_inspections
        )::DOUBLE / GREATEST(COALESCE(power_units, 0), 1)
    ) AS spoil_in,
    LIST({{
        'dot_number': dot_number,
        'legal_name': legal_name,
        'status_code': status_code,
        'power_units': power_units,
        'phone': phone,
        'fatalities': fatalities,
        'crashes': crashes,
        'oos_inspections': oos_inspections,
        'basic_alert': basic_alert,
        'basic_flags': basic_flags,
        'basic_unsafe': basic_unsafe,
        'basic_hos': basic_hos,
        'basic_fit': basic_fit,
        'basic_drugs': basic_drugs,
        'basic_maint': basic_maint,
        'basic_cargo': basic_cargo,
        'basic_crash': basic_crash
    }} ORDER BY fatalities DESC, crashes DESC, oos_inspections DESC)
        FILTER (WHERE fatalities > 0 OR crashes > 0 OR oos_inspections > 0 OR basic_alert)
        AS hot_carriers,
    LIST({{
        'dot_number': dot_number,
        'legal_name': legal_name,
        'status_code': status_code,
        'power_units': power_units,
        'phone': phone,
        'fatalities': fatalities,
        'crashes': crashes,
        'oos_inspections': oos_inspections,
        'basic_alert': basic_alert,
        'basic_flags': basic_flags,
        'basic_unsafe': basic_unsafe,
        'basic_hos': basic_hos,
        'basic_fit': basic_fit,
        'basic_drugs': basic_drugs,
        'basic_maint': basic_maint,
        'basic_cargo': basic_cargo,
        'basic_crash': basic_crash
    }} ORDER BY fatalities DESC, crashes DESC, oos_inspections DESC) AS phy_roster
FROM scored
GROUP BY cluster_key
)
"""

VOLUME_TAIL = """
SELECT
    *,
    extra_identities * worst_fatalities AS stack_score,
    {spoil_rate} AS spoil_rate,
    spoil_in * GREATEST(phy_carriers, 1)::DOUBLE AS mill_score
FROM clustered
WHERE fatalities > 0 OR crashes > 0 OR oos_inspections > 0 OR basic_alert_carriers > 0
ORDER BY fatalities DESC, crashes DESC, oos_inspections DESC,
         basic_alert_carriers DESC, phy_carriers DESC
LIMIT ?
"""

RATE_TAIL = """
SELECT
    *,
    extra_identities * worst_fatalities AS stack_score,
    {spoil_rate} AS spoil_rate,
    spoil_in * GREATEST(phy_carriers, 1)::DOUBLE AS mill_score
FROM clustered
WHERE fatalities > 0 OR crashes > 0 OR basic_alert_carriers > 0
      OR oos_inspections > 0
ORDER BY spoil_rate DESC, fatalities DESC, crashes DESC,
         basic_alert_carriers DESC, phy_carriers DESC
LIMIT ?
"""

STACK_TAIL = """
, phoned AS (
    SELECT
        scored.*,
        {phone_norm} AS phone_norm,
        (fatalities > 0 OR crashes > 0 OR oos_inspections > 0
         OR COALESCE(basic_alert, FALSE)) AS is_harm,
        (COALESCE(power_units, 0) <= {paper_pu}) AS is_paper
    FROM scored
),
phone_g AS (
    SELECT
        cluster_key,
        phone_norm,
        COUNT(*)::BIGINT AS dots,
        MAX(COALESCE(power_units, 0))::BIGINT AS max_pu,
        SUM(CASE WHEN is_harm THEN 1 ELSE 0 END)::BIGINT AS harm_dots,
        SUM(CASE WHEN NOT is_harm AND is_paper THEN 1 ELSE 0 END)::BIGINT AS extra_on_phone,
        SUM(CASE WHEN fatalities > 0
                  AND COALESCE(power_units, 0) <= {small_harm_pu}
                 THEN 1 ELSE 0 END)::BIGINT AS fat_dots,
        SUM(CASE WHEN crashes > 0
                  AND COALESCE(power_units, 0) <= {small_harm_pu}
                 THEN 1 ELSE 0 END)::BIGINT AS crash_dots,
        SUM(CASE WHEN COALESCE(power_units, 0) <= {small_harm_pu}
                 THEN fatalities ELSE 0 END)::BIGINT AS phone_fatalities,
        SUM(CASE WHEN COALESCE(power_units, 0) <= {small_harm_pu}
                 THEN crashes ELSE 0 END)::BIGINT AS phone_crashes,
        MAX(fatalities)::BIGINT AS worst_fat
    FROM phoned
    WHERE phone_norm IS NOT NULL
    GROUP BY cluster_key, phone_norm
    HAVING COUNT(*) >= 2
       AND SUM(CASE WHEN is_harm THEN 1 ELSE 0 END) >= 1
       AND (
            (MAX(CASE WHEN is_harm THEN COALESCE(power_units, 0) ELSE 0 END)
                <= {small_harm_pu}
             AND SUM(CASE WHEN NOT is_harm AND is_paper THEN 1 ELSE 0 END) >= 2)
            OR SUM(CASE WHEN fatalities > 0
                         AND COALESCE(power_units, 0) <= {small_harm_pu}
                        THEN 1 ELSE 0 END) >= 2
       )
),
farm AS (
    SELECT
        cluster_key,
        COUNT(*)::BIGINT AS harm_phones,
        SUM(dots)::BIGINT AS dots_on_harm_phones,
        SUM(extra_on_phone)::BIGINT AS extra_identities,
        SUM(fat_dots)::BIGINT AS fat_dots,
        SUM(crash_dots)::BIGINT AS crash_dots,
        SUM(phone_fatalities)::BIGINT AS phone_fatalities,
        SUM(phone_crashes)::BIGINT AS phone_crashes,
        MAX(worst_fat)::BIGINT AS worst_fatalities,
        MAX(dots)::BIGINT AS biggest_phone,
        MAX(max_pu)::BIGINT AS max_pu
    FROM phone_g
    GROUP BY cluster_key
)
SELECT
    c.* REPLACE (
        f.extra_identities AS extra_identities,
        f.worst_fatalities AS worst_fatalities
    ),
    f.harm_phones,
    f.fat_dots,
    f.crash_dots,
    f.phone_fatalities,
    f.phone_crashes,
    f.biggest_phone,
    f.dots_on_harm_phones,
    f.max_pu,
    c.spoil_in * GREATEST(c.phy_carriers, 1)::DOUBLE AS mill_score,
    c.spoil_in * GREATEST(c.phy_carriers, 1)::DOUBLE AS stack_score,
    {spoil_rate} AS spoil_rate
FROM clustered c
INNER JOIN farm f USING (cluster_key)
ORDER BY mill_score DESC, f.fat_dots DESC, c.crashes DESC,
         f.extra_identities DESC, f.biggest_phone DESC
LIMIT ?
"""


def _flag_harm_sql(w_flags: dict[str, float], *, yard: bool) -> str:
    parts = []
    for key, *_rest in BASIC_CATEGORIES:
        w = float(w_flags.get(key) or 0.0)
        if yard:
            parts.append(f"{w} * COALESCE(basic_{key}_n, 0)")
        else:
            parts.append(f"{w} * CASE WHEN basic_{key} THEN 1 ELSE 0 END")
    return " + ".join(parts) if parts else "0"


def _sql_kwargs(
    w_crash: float = FAT_PER_CRASH,
    w_flag: float = FAT_PER_FLAG,
    w_oos: float = FAT_PER_OOS,
    w_flags: dict[str, float] | None = None,
) -> dict:
    w_flags = w_flags or FAT_PER_FLAG_CAT
    return dict(
        dot_norm=normalize_dot_sql("d.dot_number"),
        sms_per_dot=sms_per_dot_sql(),
        phone_norm=census_phone_norm_sql("phone"),
        paper_pu=PAPER_PU,
        small_harm_pu=SMALL_HARM_PU,
        w_crash=w_crash,
        w_flag=w_flag,
        w_oos=w_oos,
        flag_count_sums=",\n    ".join(
            f"SUM(CASE WHEN basic_{key} THEN 1 ELSE 0 END)::BIGINT AS basic_{key}_n"
            for key, *_rest in BASIC_CATEGORIES
        ),
        flag_harm_dot=_flag_harm_sql(w_flags, yard=False),
        spoil_rate=(
            "GREATEST(fatalities, "
            f"({_flag_harm_sql(w_flags, yard=True)}), "
            f"{w_crash} * crashes, "
            f"{w_oos} * oos_inspections)::DOUBLE "
            "/ GREATEST(COALESCE(power_units, 0), 1)"
        ),
    )


def cluster_sql(
    w_crash: float = FAT_PER_CRASH,
    w_flag: float = FAT_PER_FLAG,
    w_oos: float = FAT_PER_OOS,
    w_flags: dict[str, float] | None = None,
) -> str:
    kw = _sql_kwargs(w_crash=w_crash, w_flag=w_flag, w_oos=w_oos, w_flags=w_flags)
    return CLUSTER_SQL.format(**kw) + VOLUME_TAIL.format(**kw)


def stack_sql(
    w_crash: float = FAT_PER_CRASH,
    w_flag: float = FAT_PER_FLAG,
    w_oos: float = FAT_PER_OOS,
    w_flags: dict[str, float] | None = None,
) -> str:
    kw = _sql_kwargs(w_crash=w_crash, w_flag=w_flag, w_oos=w_oos, w_flags=w_flags)
    return CLUSTER_SQL.format(**kw) + STACK_TAIL.format(**kw)


def rate_sql(
    w_crash: float = FAT_PER_CRASH,
    w_flag: float = FAT_PER_FLAG,
    w_oos: float = FAT_PER_OOS,
    w_flags: dict[str, float] | None = None,
) -> str:
    kw = _sql_kwargs(w_crash=w_crash, w_flag=w_flag, w_oos=w_oos, w_flags=w_flags)
    return CLUSTER_SQL.format(**kw) + RATE_TAIL.format(**kw)


def measure_fatality_weights(
    con: duckdb.DuckDBPyConnection,
) -> tuple[float, float, float, dict]:
    """Universe-wide sum(fatalities) / sum(metric) for crash, each BASIC flag, OOS."""
    n_crash, n_fat = con.execute(
        """
        SELECT COUNT(*)::BIGINT,
               COALESCE(SUM(TRY_CAST(fatalities AS INTEGER)), 0)::BIGINT
        FROM fmcsa_sms_crashes
        WHERE dot_norm IS NOT NULL
        """
    ).fetchone()
    n_oos = con.execute(
        """
        SELECT COUNT(*)::BIGINT
        FROM fmcsa_sms_inspections
        WHERE dot_norm IS NOT NULL
          AND TRY_CAST(oos_total AS INTEGER) > 0
        """
    ).fetchone()[0]
    basic_sql = sms_per_dot_sql()
    flag_select = ",\n            ".join(
        f"COALESCE(SUM(CASE WHEN basic_{key} THEN 1 ELSE 0 END), 0)::BIGINT"
        for key, *_rest in BASIC_CATEGORIES
    )
    n_flags, n_basic, *flag_counts = con.execute(
        f"""
        SELECT
            COALESCE(SUM(basic_flags), 0)::BIGINT,
            SUM(CASE WHEN basic_alert THEN 1 ELSE 0 END)::BIGINT,
            {flag_select}
        FROM ({basic_sql}) s
        """
    ).fetchone()
    w_crash = (n_fat / n_crash) if n_crash else FAT_PER_CRASH
    w_flag = (n_fat / n_flags) if n_flags else FAT_PER_FLAG
    w_oos = (n_fat / n_oos) if n_oos else FAT_PER_OOS
    w_flags: dict[str, float] = {}
    counts: dict[str, int] = {}
    for (key, *_rest), n in zip(BASIC_CATEGORIES, flag_counts):
        counts[key] = int(n or 0)
        w_flags[key] = (n_fat / n) if n else 0.0
    set_flag_weights(w_flags)
    meta = {
        "crash_records": int(n_crash or 0),
        "crash_fatalities": int(n_fat or 0),
        "oos_inspections": int(n_oos or 0),
        "basic_flags": int(n_flags or 0),
        "basic_dots": int(n_basic or 0),
        "flag_counts": counts,
        "fat_per_flag_cat": w_flags,
        "fat_per_crash": w_crash,
        "fat_per_flag": w_flag,
        "fat_per_oos": w_oos,
    }
    return w_crash, w_flag, w_oos, meta





def safer_url(dot_number: str) -> str:
    d = "".join(ch for ch in str(dot_number or "") if ch.isdigit())
    return (
        "https://safer.fmcsa.dot.gov/query.asp?searchtype=ANY"
        "&query_type=queryCarrierSnapshot&query_param=USDOT"
        f"&query_string={d}"
    )


PAGE_CSS = """
    body { font-family: ui-sans-serif, system-ui, sans-serif; margin: 1.5rem; color: #111; }
    table { border-collapse: collapse; width: 100%; font-size: 0.92rem; }
    th, td { border-bottom: 1px solid #e5e7eb; padding: 0.4rem 0.5rem; text-align: left; }
    th { background: #f3f4f6; }
    td.n, th.n { text-align: right; font-variant-numeric: tabular-nums; }
    .meta { color: #4b5563; font-size: 0.9rem; }
    .rank-dot {
      display: inline-block; width: 0.7rem; height: 0.7rem; border-radius: 999px;
      border: 1px solid rgba(0,0,0,0.12); vertical-align: middle; margin-right: 0.35rem;
    }
    .rank-dot.high { background: #b91c1c; }
    .rank-dot.mid { background: #d97706; }
    .rank-dot.low { background: #2563eb; }
    .rank-dot.top { background: #7f1d1d; box-shadow: 0 0 0 1px #fecaca; }
    .rank-dot.none { background: #e5e7eb; }
    .term {
      border-bottom: 1px dotted currentColor;
      cursor: help;
    }
    th[title] {
      text-decoration: underline dotted;
      text-underline-offset: 0.15em;
    }
    .cards { display: flex; flex-wrap: wrap; gap: 0.75rem; margin: 1rem 0; }
    .card {
      border: 1px solid #e5e7eb; border-radius: 8px; padding: 0.75rem 1rem;
      min-width: 8rem;
    }
    .card strong { display: block; font-size: 1.25rem; }
    .card span { color: #4b5563; font-size: 0.85rem; }
"""


def maps_url(c: dict) -> str:
    lat, lon = c.get("latitude"), c.get("longitude")
    addr = c.get("canonical_address") or c.get("cluster_key") or ""
    if lat is not None and lon is not None:
        try:
            return f"https://www.google.com/maps?q={float(lat)},{float(lon)}"
        except (TypeError, ValueError):
            pass
    return f"https://www.google.com/maps/search/?api=1&query={quote_plus(str(addr))}&zoom=17"


def tanstack_assets_html() -> str:
    from mako.lookup import TemplateLookup

    lookup = TemplateLookup(
        directories=[str(SCRIPT_DIR / "templates")],
        input_encoding="utf-8",
    )
    return lookup.get_template("partials/tanstack_table_assets.mako").render()


def _table_json(obj) -> str:
    return json.dumps(obj, ensure_ascii=True, default=str)


def build_index_table(clusters: list[dict], *, kind: str) -> dict:
    stack_view = kind == "stack"
    rate_view = kind == "rate"
    if rate_view:
        metric_key, label = "spoil_rate", "harm / PU"
        scale_float = True
    elif stack_view:
        metric_key, label = "mill_score", "Mill×harm"
        scale_float = True
    else:
        metric_key, label = "fatalities", "fatalities"
        scale_float = False
    scale = MetricScale.from_values(
        (_f(c, metric_key) if scale_float else _n(c, metric_key)) for c in clusters
    )
    rows = []
    for i, c in enumerate(clusters, 1):
        key = str(c.get("cluster_key") or "")
        addr = c.get("canonical_address") or key
        detail = f"unsafe-{slug(key)}.html"
        maps = maps_url(c)
        metric = _f(c, metric_key) if scale_float else _n(c, metric_key)
        if rate_view or stack_view:
            shown = str(i)
        else:
            shown = f"{int(metric):,}"
        rows.append(
            {
                "slug": slug(key),
                "rank": i,
                "rank_html": rank_dot_html(metric, scale=scale, label=label)
                + esc(shown),
                "address": addr,
                "address_html": (
                    f"{linkify_zip_codes(addr)}"
                    f' · <a href="{esc(maps)}" target="_blank" rel="noopener">map</a>'
                ),
                "colocator": key,
                "fatalities": _n(c, "fatalities"),
                "crashes": _n(c, "crashes"),
                "oos": _n(c, "oos_inspections"),
                "basic": _n(c, "basic_alert_carriers"),
                "phy": _n(c, "phy_carriers"),
                "extra": _n(c, "extra_identities"),
                "worst_fat": _n(c, "worst_fatalities"),
                "score": _n(c, "stack_score"),
                "fat_dots": _n(c, "fat_dots"),
                "crash_dots": _n(c, "crash_dots"),
                "harm_phones": _n(c, "harm_phones"),
                "phone_fatalities": _n(c, "phone_fatalities"),
                "biggest_phone": _n(c, "biggest_phone"),
                "max_pu": _n(c, "max_pu"),
                "power_units": _n(c, "power_units"),
                "active_pu": _n(c, "active_power_units"),
                "spoil_rate": round(_f(c, "spoil_rate"), 6),
                "spoil_in": round(_f(c, "spoil_in"), 6),
                "mill_score": round(_f(c, "mill_score"), 4),
                "detail_html": f'<a href="{esc(detail)}">Detail →</a>',
            }
        )
    if stack_view:
        columns = [
            _col("rank", "● Rank", key="rank_score"),
            _col("mill_score", "Mill×harm", key="mill_score"),
            _col("spoil_in", "Apple/PU", key="spoil_in"),
            _col("phy", "Phy DOT#", key="phy"),
            _col("extra", "New vinyl", key="extra_quiet"),
            _col("crashes", "Crashes", key="crashes"),
            _col("crash_dots", "Crash DOTs", key="crash_dots"),
            _col("fat_dots", "Fat DOTs", key="fat_dots"),
            _col("oos", "OOS insp", key="oos"),
            _col("basic", "BASIC", key="basic"),
            _col("max_pu", "Max PU", key="max_pu"),
            _col("address", "Address"),
            _col("detail_html", "", sortable=False),
        ]
        initial = [
            {"id": "mill_score", "desc": True},
            {"id": "fat_dots", "desc": True},
            {"id": "crashes", "desc": True},
        ]
    elif rate_view:
        columns = [
            _col("rank", "● Rank", key="rank_rate"),
            _col("spoil_rate", "Yard/PU", key="spoil_rate"),
            _col("spoil_in", "Apple/PU", key="spoil_in"),
            _col("fatalities", "Fatalities", key="fatalities"),
            _col("crashes", "Crashes", key="crashes"),
            _col("basic", "BASIC alerts", key="basic"),
            _col("power_units", "Power units", key="power_units"),
            _col("address", "Address"),
            _col("phy", "Phy DOT#", key="phy"),
            _col("detail_html", "", sortable=False),
        ]
        initial = [
            {"id": "spoil_rate", "desc": True},
            {"id": "spoil_in", "desc": True},
        ]
    else:
        columns = [
            _col("rank", "● Fatalities", key="rank_fat"),
            _col("address", "Address"),
            _col("fatalities", "Fatalities", key="fatalities"),
            _col("crashes", "Crashes", key="crashes"),
            _col("oos", "OOS insp", key="oos"),
            _col("basic", "BASIC alerts", key="basic"),
            _col("spoil_rate", "Yard/PU", key="spoil_rate"),
            _col("spoil_in", "Apple/PU", key="spoil_in"),
            _col("mill_score", "Mill×harm", key="mill_score"),
            _col("phy", "Phy DOT#", key="phy"),
            _col("active_pu", "Active PU", key="power_units"),
            _col("detail_html", "", sortable=False),
        ]
        initial = [{"id": "fatalities", "desc": True}]
    return {
        "rows": rows,
        "columns": columns,
        "initialSort": initial,
        "pageSize": 50,
        "pageSizeOptions": [10, 25, 50, 100, 200],
    }


def _roster_as_carriers(c: dict) -> list[dict]:
    addr = c.get("canonical_address") or ""
    out = []
    for h in _carrier_list(c.get("phy_roster")):
        if not isinstance(h, dict):
            continue
        rec = {
            "dot_number": h.get("dot_number"),
            "legal_name": h.get("legal_name"),
            "dba_name": h.get("dba_name"),
            "status_code": h.get("status_code"),
            "power_units": h.get("power_units"),
            "phone": h.get("phone") or "No Phone",
            "canonical_address": addr,
            "fatalities": int(h.get("fatalities") or 0),
            "crashes": int(h.get("crashes") or 0),
            "oos_inspections": int(h.get("oos_inspections") or 0),
            "basic_alert": bool(h.get("basic_alert")),
            "basic_flags": int(h.get("basic_flags") or 0),
        }
        for key, _, _, _, _ in BASIC_CATEGORIES:
            rec[f"basic_{key}"] = bool(h.get(f"basic_{key}"))
        out.append(rec)
    return out


def build_detail_tables(c: dict) -> list[dict]:
    from collections import defaultdict

    carriers = _roster_as_carriers(c)
    phone_groups: dict[str, list] = defaultdict(list)
    for d in carriers:
        phone_groups[d.get("phone") or "No Phone"].append(d)
    phone_groups_sorted = sorted(
        phone_groups.items(),
        key=lambda item: sum(int(x.get("power_units") or 0) for x in item[1]),
        reverse=True,
    )
    tables = shared_detail_tables(phone_groups=phone_groups_sorted)
    for t in tables:
        if t.get("rootId") == "ts-carriers":
            t["initialSort"] = [
                {"id": "fatalities", "desc": True},
                {"id": "power_units", "desc": True},
            ]
    return tables


def slug(key: str) -> str:
    return (
        str(key)
        .lower()
        .replace(":", "-")
        .replace(".", "-")
        .replace("/", "-")[:80]
    )


def _n(c: dict, key: str, default: int = 0) -> int:
    v = c.get(key)
    return default if v is None else int(v)


def _f(c: dict, key: str, default: float = 0.0) -> float:
    v = c.get(key)
    if v is None:
        return default
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def render_index(
    clusters: list[dict],
    *,
    generated_at: str,
    db_path: str,
    kind: str = "volume",
    assets: str = "",
    w_crash: float = FAT_PER_CRASH,
    w_flag: float = FAT_PER_FLAG,
    w_oos: float = FAT_PER_OOS,
    w_flags: dict[str, float] | None = None,
) -> str:
    stack_view = kind == "stack"
    rate_view = kind == "rate"
    payload = build_index_table(clusters, kind=kind)
    nav = (
        '<p class="meta">'
        '<a href="../master_index.html">All reports</a> · '
        '<a href="index.html">Volume</a> · '
        '<a href="rates.html">Harm / PU</a> · '
        '<a href="stacks.html">Identity farms</a>'
        "</p>"
    )
    if stack_view:
        title = "DOT identity farms / mill × harm"
        blurb = (
            f"{term('Mill×harm', 'mill_score')} = {term('Apple/PU', 'spoil_in')} "
            f"× {term('Phy DOT#', 'phy')}. A paperwork mill is legal; this is the "
            "vet-your-drivers stain — one bad apple times how many names share the yard. "
            "Admission is still a shared-phone farm: "
            f"{term('New vinyl', 'extra_quiet')} (power units ≤ {PAPER_PU} on a phone "
            f"whose harm DOTs are ≤ {SMALL_HARM_PU}) or ≥2 small {term('Fat DOTs', 'fat_dots')}. "
            f"{term('Sort', 'score')} is Mill×harm, then Fat DOTs, then crashes. "
            "Table: sort, search, pagination (TanStack)."
        )
        other = nav
        noscript_heads = (
            f"{_th('Rank', 'rank_score')}{_th('Mill×harm', 'mill_score')}"
            f"{_th('Apple/PU', 'spoil_in')}{_th('Phy DOT#', 'phy')}"
            f"{_th('New vinyl', 'extra_quiet')}{_th('Crashes', 'crashes')}"
            f"{_th('Fat DOTs', 'fat_dots')}{_th('Address')}{_th('')}"
        )
        noscript_rows = []
        for r in payload["rows"]:
            noscript_rows.append(
                "<tr>"
                f"<td class='n'>{int(r['rank']):,}</td>"
                f"<td class='n'>{float(r['mill_score']):.2f}</td>"
                f"<td class='n'>{float(r['spoil_in']):.4f}</td>"
                f"<td class='n'>{int(r['phy']):,}</td>"
                f"<td class='n'>{int(r['extra']):,}</td>"
                f"<td class='n'>{int(r['crashes']):,}</td>"
                f"<td class='n'>{int(r['fat_dots']):,}</td>"
                f"<td>{r['address_html']}</td>"
                f"<td>{r['detail_html']}</td>"
                "</tr>"
            )
    elif rate_view:
        title = "DOT colocator harm per power unit"
        per_crash = 1 / w_crash if w_crash else 0
        per_oos = 1 / w_oos if w_oos else 0
        cats = w_flags or FAT_PER_FLAG_CAT
        flag_bits = []
        for key, short, cat_title, *_rest in BASIC_CATEGORIES:
            ww = float(cats.get(key) or 0.0)
            if ww <= 0:
                continue
            flag_bits.append(f"{ww:.1f} per {short} ({cat_title})")
        flag_blurb = "; ".join(flag_bits) if flag_bits else f"{w_flag:.2f} pooled"
        blurb = (
            f"{term('Yard/PU', 'spoil_rate')} / {term('Apple/PU', 'spoil_in')} "
            "weight crashes, OOS, and each BASIC flag by universe-wide "
            "sum(fatalities)/sum(metric): "
            f"{w_crash:.4f} per crash (~1 death per {per_crash:.0f} crashes), "
            f"{w_oos:.4f} per OOS (~1 death per {per_oos:.0f} OOS). "
            f"Per flag: {flag_blurb}. Lit flags add. "
            "Then divide by power units (outside vs inside the max). "
            "Yard/PU and Apple/PU match on a one-truck yard; they split on mixed "
            "fleets (sort Volume, or click Apple/PU). "
            "Click a header to sort. Table: sort, search, pagination (TanStack)."
        )
        other = nav
        noscript_heads = (
            f"{_th('Yard/PU', 'spoil_rate')}{_th('Apple/PU', 'spoil_in')}"
            f"{_th('Fatalities', 'fatalities')}{_th('Crashes', 'crashes')}"
            f"{_th('BASIC', 'basic')}{_th('Power units', 'power_units')}"
            f"{_th('Address')}{_th('Phy DOT#', 'phy')}{_th('')}"
        )
        noscript_rows = []
        for r in payload["rows"]:
            noscript_rows.append(
                "<tr>"
                f"<td class='n'>{float(r['spoil_rate']):.4f}</td>"
                f"<td class='n'>{float(r['spoil_in']):.4f}</td>"
                f"<td class='n'>{int(r['fatalities']):,}</td>"
                f"<td class='n'>{int(r['crashes']):,}</td>"
                f"<td class='n'>{int(r['basic']):,}</td>"
                f"<td class='n'>{int(r['power_units']):,}</td>"
                f"<td>{r['address_html']}</td>"
                f"<td class='n'>{int(r['phy']):,}</td>"
                f"<td>{r['detail_html']}</td>"
                "</tr>"
            )
    else:
        title = "DOT unsafe physical colocators"
        blurb = (
            "These 100 yards have the most crash deaths in the SMS window "
            f"(then {term('crashes', 'crashes')}, {term('OOS', 'oos')}, "
            f"{term('BASIC', 'basic')}). Default sort is body count — FedEx, UPS, "
            "the big fleets. "
            f"{term('Apple/PU', 'spoil_in')} is the score per truck of the worst "
            "USDOT at the yard (one rotten apple). Click that header to float "
            "1-truck mass-casualty events without leaving this death-first 100. "
            f"{term('Yard/PU', 'spoil_rate')} is the same math on the whole lot. "
            f"{term('Mill×harm', 'mill_score')} is Apple/PU × physical USDOT names. "
            "Harm / PU is a different 100 (BASIC-weighted, includes 0-death yards). "
            "Table: sort, search, pagination (TanStack)."
        )
        other = nav
        noscript_heads = (
            f"{_th('Fatalities', 'fatalities')}{_th('Address')}"
            f"{_th('Crashes', 'crashes')}{_th('OOS', 'oos')}"
            f"{_th('BASIC', 'basic')}{_th('Yard/PU', 'spoil_rate')}"
            f"{_th('Apple/PU', 'spoil_in')}{_th('Mill×harm', 'mill_score')}"
            f"{_th('Phy DOT#', 'phy')}{_th('Active PU', 'power_units')}{_th('')}"
        )
        noscript_rows = []
        for r in payload["rows"]:
            noscript_rows.append(
                "<tr>"
                f"<td class='n'>{int(r['fatalities']):,}</td>"
                f"<td>{r['address_html']}</td>"
                f"<td class='n'>{int(r['crashes']):,}</td>"
                f"<td class='n'>{int(r['oos']):,}</td>"
                f"<td class='n'>{int(r['basic']):,}</td>"
                f"<td class='n'>{float(r['spoil_rate']):.4f}</td>"
                f"<td class='n'>{float(r['spoil_in']):.4f}</td>"
                f"<td class='n'>{float(r['mill_score']):.2f}</td>"
                f"<td class='n'>{int(r['phy']):,}</td>"
                f"<td class='n'>{int(r['active_pu']):,}</td>"
                f"<td>{r['detail_html']}</td>"
                "</tr>"
            )
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>{esc(title)} — {esc(generated_at[:10])}</title>
  <style>
{PAGE_CSS}
  </style>
</head>
<body>
  <h1>{esc(title)}</h1>
  <p class="meta">Generated {esc(generated_at)} · {esc(db_path)} · {len(clusters)} places</p>
  {other}
  <p>{blurb}</p>
  <div id="ts-table-root"></div>
  <noscript>
  <table id="clusters-table">
    <thead><tr>{noscript_heads}</tr></thead>
    <tbody>
      {"".join(noscript_rows)}
    </tbody>
  </table>
  </noscript>
  <script>
    window.__CLUSTER_TABLE__ = {_table_json(payload)};
  </script>
  {assets}
</body>
</html>
"""


def _carrier_list(value) -> list:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return []
    return value if isinstance(value, list) else []


def _carrier_rows(items: list, *, limit: int = 200) -> str:
    rows = []
    for h in items[:limit]:
        if not isinstance(h, dict):
            continue
        pu = h.get("power_units")
        pu_s = f"{int(pu):,}" if pu is not None else "—"
        rows.append(
            "<tr>"
            f"<td>{_dot_link_html(h.get('dot_number'))}</td>"
            f"<td>{esc(h.get('legal_name') or '')}</td>"
            f"<td>{esc(h.get('phone') or '—')}</td>"
            f"<td>{esc(h.get('status_code') or '—')}</td>"
            f"<td class='n'>{pu_s}</td>"
            f"<td class='n'>{int(h.get('fatalities') or 0):,}</td>"
            f"<td class='n'>{int(h.get('crashes') or 0):,}</td>"
            f"<td class='n'>{int(h.get('oos_inspections') or 0):,}</td>"
            f"<td>{basic_flags_html(h) or ''}</td>"
            "</tr>"
        )
    return "".join(rows)


def render_detail(c: dict, *, generated_at: str, assets: str = "") -> str:
    roster_rows = _carrier_rows(_carrier_list(c.get("phy_roster")))
    addr = c.get("canonical_address") or ""
    maps = maps_url(c)
    tables = build_detail_tables(c)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>{esc(c['cluster_key'])} — DOT unsafe colocator</title>
  <style>
{PAGE_CSS}
  </style>
</head>
<body>
  <p><a href="../master_index.html">All reports</a> ·
     <a href="index.html">volume</a> · <a href="rates.html">harm / PU</a> ·
     <a href="stacks.html">identity farms / mill × harm</a></p>
  <h1>{esc(c['cluster_key'])}</h1>
  <p class="meta">{linkify_zip_codes(addr)}
     · <a href="{esc(maps)}" target="_blank" rel="noopener">map</a>
     · ZIP {esc(c.get('zip_code') or '')}</p>
  <section class="cards">
    <div class="card"><strong>{_n(c, 'phy_carriers'):,}</strong><span>{term('Phy DOT#', 'phy')}</span></div>
    <div class="card"><strong>{_n(c, 'active_power_units'):,}</strong><span>{term('Active PU', 'power_units')}</span></div>
    <div class="card"><strong>{_n(c, 'power_units'):,}</strong><span>{term('Power units', 'power_units')}</span></div>
    <div class="card"><strong>{_n(c, 'fatalities'):,}</strong><span>{term('Fatalities', 'fatalities')}</span></div>
    <div class="card"><strong>{_n(c, 'crashes'):,}</strong><span>{term('Crashes', 'crashes')}</span></div>
    <div class="card"><strong>{_n(c, 'oos_inspections'):,}</strong><span>{term('OOS insp', 'oos')}</span></div>
    <div class="card"><strong>{_n(c, 'basic_alert_carriers'):,}</strong><span>{term('BASIC-alert', 'basic')}</span></div>
    <div class="card"><strong>{_n(c, 'crash_dots'):,}</strong><span>{term('Crash DOTs', 'crash_dots')}</span></div>
    <div class="card"><strong>{_n(c, 'fat_dots'):,}</strong><span>{term('Fat DOTs', 'fat_dots')}</span></div>
    <div class="card"><strong>{_n(c, 'extra_identities'):,}</strong><span>{term('New vinyl', 'extra_quiet')}</span></div>
    <div class="card"><strong>{_n(c, 'phone_fatalities'):,}</strong><span>{term('Phone fat', 'phone_fatalities')}</span></div>
    <div class="card"><strong>{_n(c, 'harm_phones'):,}</strong><span>{term('Harm phones', 'harm_phones')}</span></div>
    <div class="card"><strong>{_n(c, 'max_pu'):,}</strong><span>{term('Max PU', 'max_pu')}</span></div>
    <div class="card"><strong>{_f(c, 'spoil_rate'):.4f}</strong><span>{term('Yard/PU', 'spoil_rate')}</span></div>
    <div class="card"><strong>{_f(c, 'spoil_in'):.4f}</strong><span>{term('Apple/PU', 'spoil_in')}</span></div>
    <div class="card"><strong>{_f(c, 'mill_score'):.2f}</strong><span>{term('Mill×harm', 'mill_score')}</span></div>
  </section>
  <p>{term('worst fatalities', 'worst_fat')} {_n(c, 'worst_fatalities'):,} ·
     biggest shared phone {_n(c, 'biggest_phone'):,} USDOTs</p>
  <h2>DOT by shared phone</h2>
  <p class="meta">Click a phone to filter the carrier list. Census phone, status, and power units sit next to SMS harm.</p>
  <div id="ts-phone-groups" class="ts-table-root"></div>
  <h2>All physical USDOT numbers at this colocator</h2>
  <p class="meta">{term('DOT#', 'dot')} links: Search Carriers, map, MOTUS, SAFER.
     Default sort: fatalities, then power units.</p>
  <div id="ts-carriers" class="ts-table-root"></div>
  <noscript>
  <h3>All physical USDOT numbers</h3>
  <table>
    <thead>
      <tr>{_th('DOT#', 'dot')}{_th('Name')}{_th('Phone', 'phone')}{_th('Status', 'status')}
          {_th('Power units', 'power_units', cls='n')}{_th('Fatalities', 'fatalities', cls='n')}
          {_th('Crashes', 'crashes', cls='n')}{_th('OOS insp', 'oos', cls='n')}{_th('BASIC', 'basic_flag')}</tr>
    </thead>
    <tbody>
      {roster_rows or "<tr><td colspan='9'>None listed</td></tr>"}
    </tbody>
  </table>
  </noscript>
  <p class="meta">Generated {esc(generated_at)}</p>
  <script>
    window.__TS_TABLES__ = {_table_json(tables)};
  </script>
  {assets}
</body>
</html>
"""


def fetch_clusters(
    con: duckdb.DuckDBPyConnection, limit: int, *, sql: str | None = None
) -> list[dict]:
    rows = con.execute(sql or cluster_sql(), [limit]).fetchall()
    cols = [d[0] for d in con.description]
    return [dict(zip(cols, row)) for row in rows]


def write_gallery(
    clusters: list[dict],
    out_dir: Path,
    *,
    db_path: str,
    stacks: list[dict] | None = None,
    rates: list[dict] | None = None,
    w_crash: float = FAT_PER_CRASH,
    w_flag: float = FAT_PER_FLAG,
    w_oos: float = FAT_PER_OOS,
    w_flags: dict[str, float] | None = None,
    weight_meta: dict | None = None,
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    generated_at = datetime.now(ZoneInfo("America/Los_Angeles")).strftime(
        "%Y-%m-%d %H:%M %Z"
    )
    stacks = stacks or []
    rates = rates or []
    assets = tanstack_assets_html()
    (out_dir / "index.html").write_text(
        render_index(
            clusters,
            generated_at=generated_at,
            db_path=db_path,
            kind="volume",
            assets=assets,
            w_crash=w_crash,
            w_flag=w_flag,
            w_oos=w_oos,
            w_flags=w_flags,
        ),
        encoding="utf-8",
    )
    (out_dir / "stacks.html").write_text(
        render_index(
            stacks,
            generated_at=generated_at,
            db_path=db_path,
            kind="stack",
            assets=assets,
            w_crash=w_crash,
            w_flag=w_flag,
            w_oos=w_oos,
            w_flags=w_flags,
        ),
        encoding="utf-8",
    )
    (out_dir / "rates.html").write_text(
        render_index(
            rates,
            generated_at=generated_at,
            db_path=db_path,
            kind="rate",
            assets=assets,
            w_crash=w_crash,
            w_flag=w_flag,
            w_oos=w_oos,
            w_flags=w_flags,
        ),
        encoding="utf-8",
    )
    by_key: dict[str, dict] = {}
    for c in list(clusters) + list(stacks) + list(rates):
        by_key[c["cluster_key"]] = c
    for c in by_key.values():
        name = f"unsafe-{slug(c['cluster_key'])}.html"
        (out_dir / name).write_text(
            render_detail(c, generated_at=generated_at, assets=assets),
            encoding="utf-8",
        )
    meta = {
        "generated_at": generated_at,
        "db_path": db_path,
        "volume_count": len(clusters),
        "stack_count": len(stacks),
        "rate_count": len(rates),
        "detail_count": len(by_key),
        "fat_per_crash": w_crash,
        "fat_per_flag": w_flag,
        "fat_per_oos": w_oos,
        "fat_per_flag_cat": w_flags or FAT_PER_FLAG_CAT,
        "weight_probe": weight_meta or {},
    }
    (out_dir / "export_metadata.json").write_text(
        json.dumps(meta, indent=2), encoding="utf-8"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="DOT unsafe colocator gallery")
    parser.add_argument("--db-path", default=DEFAULT_DB)
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT))
    parser.add_argument("--max-clusters", type=int, default=100)
    args = parser.parse_args()
    con = duckdb.connect(args.db_path, read_only=True)
    try:
        w_crash, w_flag, w_oos, weight_meta = measure_fatality_weights(con)
        w_flags = weight_meta.get("fat_per_flag_cat") or FAT_PER_FLAG_CAT
        volume = fetch_clusters(
            con,
            args.max_clusters,
            sql=cluster_sql(w_crash, w_flag, w_oos, w_flags=w_flags),
        )
        stacks = fetch_clusters(
            con,
            args.max_clusters,
            sql=stack_sql(w_crash, w_flag, w_oos, w_flags=w_flags),
        )
        rates = fetch_clusters(
            con,
            args.max_clusters,
            sql=rate_sql(w_crash, w_flag, w_oos, w_flags=w_flags),
        )
    finally:
        con.close()
    write_gallery(
        volume,
        Path(args.out_dir),
        db_path=args.db_path,
        stacks=stacks,
        rates=rates,
        w_crash=w_crash,
        w_flag=w_flag,
        w_oos=w_oos,
        w_flags=w_flags,
        weight_meta=weight_meta,
    )
    flag_s = " ".join(
        f"{k}={float(w_flags.get(k) or 0):.1f}"
        for k, *_rest in BASIC_CATEGORIES
        if float(w_flags.get(k) or 0) > 0
    )
    print(
        f"Wrote {len(volume)} volume + {len(rates)} rate + {len(stacks)} stack "
        f"clusters to {args.out_dir} "
        f"(w_crash={w_crash:.4f} w_oos={w_oos:.4f} {flag_s})"
    )


if __name__ == "__main__":
    main()
