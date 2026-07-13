#!/usr/bin/env python3
"""
generate_focus_reports.py — Cluster reports like trucking, for other entity stacks.

Same slice modes (address / colocator / zipcode / loose_colocator), different
primary entity focus and ranking:

  medicare    — NPPES practice/mailing + optional Medicare spending
  fec         — FEC contributors / committees / transactions / expenditures
  contractor  — 990 Schedule C contractors
  grants      — 990 grantee addresses

Usage:
  python generate_focus_reports.py --focus medicare --slice-by colocator
  python generate_focus_reports.py --focus fec --slice-by zipcode --max-clusters 100
  python generate_focus_reports.py --focus contractor --slice-by address
  python generate_focus_reports.py --focus grants --slice-by loose_colocator
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any

import duckdb
from mako.template import Template

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

# Reuse slice modes, maps, slugify, CSS, geocoding join logic from trucking reports.
from generate_address_reports import (  # type: ignore  # noqa: E402
    CSS,
    SLICE_MODES,
    fmt_money,
    google_maps_url,
    is_po_box,
    load_physical_notes,
    physical_note_for,
    resolve_slice_mode,
    slugify_address,
    _member_filter_sql,
    fetch_charities,
    fetch_officers,
)

DEFAULT_DB = "/Volumes/Data/final/irs990.duckdb"
MEDICARE_ROLLUP_SIDE = os.environ.get(
    "MEDICARE_ROLLUP_DB",
    "/Volumes/Data/final/medicare_provider_rollup.duckdb",
)


def ensure_medicare_rollup(conn: duckdb.DuckDBPyConnection) -> bool:
    """Ensure medicare_provider_rollup / _hcpcs are queryable.

    Prefer tables on the main DB; if missing (e.g. still building while main is
    locked), ATTACH the sidecar and expose TEMP VIEWs with the same names.
    """
    try:
        conn.execute("SELECT 1 FROM medicare_provider_rollup LIMIT 1")
        conn.execute("SELECT 1 FROM medicare_provider_hcpcs LIMIT 1")
        return True
    except Exception:
        pass
    side = MEDICARE_ROLLUP_SIDE
    if not os.path.exists(side):
        return False
    try:
        try:
            conn.execute("DETACH medroll")
        except Exception:
            pass
        conn.execute(f"ATTACH '{side}' AS medroll (READ_ONLY)")
        # Temp views work on read_only connections for query routing.
        conn.execute(
            "CREATE OR REPLACE TEMP VIEW medicare_provider_rollup AS "
            "SELECT * FROM medroll.medicare_provider_rollup"
        )
        conn.execute(
            "CREATE OR REPLACE TEMP VIEW medicare_provider_hcpcs AS "
            "SELECT * FROM medroll.medicare_provider_hcpcs"
        )
        conn.execute("SELECT 1 FROM medicare_provider_rollup LIMIT 1")
        return True
    except Exception as e:
        print(f"  medicare rollup attach failed: {e}", flush=True)
        return False


FOCUS_DOMAINS: dict[str, dict[str, Any]] = {
    "medicare": {
        "label": "Medicare / NPPES providers",
        "out_prefix": "medicare",
        "address_types": ("nppes_practice", "nppes_mailing"),
        "min_focus_default": 30,
        "min_multi_default": 5,
        "entity_title": "Medicare / NPPES providers",
        "amount_label": "Medicare paid (billing NPI)",
        "review_tag": "medicare",
        # Rank by $ from medicare_provider_rollup (NPI-level, not 230M line grain).
        "rank_metric": "focus_amount",
        "rank_label": "Medicare $",
    },
    "fec": {
        "label": "FEC political money",
        "out_prefix": "fec",
        "address_types": (
            "fec_contributor",
            "fec_committee",
            "fec_committee_transaction",
            "fec_operating_expenditure",
            "fec_candidate_spending",
        ),
        "min_focus_default": 80,
        "min_multi_default": 5,
        "entity_title": "FEC entities",
        "amount_label": "Contribution / transaction $",
        "review_tag": "fec",
        "rank_metric": "focus_amount",
        "rank_label": "FEC $",
    },
    "contractor": {
        "label": "990 contractors",
        "out_prefix": "contractor",
        "address_types": ("contractor",),
        "min_focus_default": 15,
        "min_multi_default": 4,
        "entity_title": "Contractors",
        "amount_label": "Contractor payments",
        "review_tag": "contractor",
        # Prefer distinct street addresses (multi-suite shells), then row count / $.
        "rank_metric": "distinct_addresses",
        "rank_label": "Distinct addresses",
    },
    "grants": {
        "label": "990 grants",
        "out_prefix": "grants",
        "address_types": ("grant",),
        "min_focus_default": 40,
        "min_multi_default": 4,
        "entity_title": "Grants",
        "amount_label": "Grant amounts",
        "review_tag": "grants",
        # $ after excluding name-suppressed / privacy rollups (big_pharma_subsidy.json).
        "rank_metric": "focus_amount",
        "rank_label": "Grant $ (excl. suppressed names)",
    },
}


def _type_in_sql(types: tuple[str, ...], col: str = "address_type") -> str:
    quoted = ", ".join(f"'{t}'" for t in types)
    return f"{col} IN ({quoted})"


def build_focus_cluster_sql(
    slice_by: str,
    focus: str,
    conn: duckdb.DuckDBPyConnection,
    *,
    lat_long: bool = False,
) -> str:
    """Rank clusters by focus entity count + money metric."""
    domain = FOCUS_DOMAINS[focus]
    types = domain["address_types"]
    type_pred = _type_in_sql(types, "address_type")
    type_pred_k = _type_in_sql(types, "k.address_type")

    mode = resolve_slice_mode(slice_by, conn, lat_long=lat_long)
    key = mode["key_expr"]
    where = mode["where"]
    from_sql = mode.get("from_sql", "FROM Addresses")
    if mode.get("needs_geocoding_join") == "1":
        canon, atype, owner, aid = (
            "a.canonical_address",
            "a.address_type",
            "a.owner_id",
            "a.address_id",
        )
    else:
        canon, atype, owner, aid = (
            "canonical_address",
            "address_type",
            "owner_id",
            "address_id",
        )

    # Money metric join varies by focus.
    # Medicare: join NPI-level rollup (not 230M line-level spending).
    if focus == "medicare":
        if not ensure_medicare_rollup(conn):
            # No rollup yet — empty money (rank falls back to focus_count via ties)
            money_cte = """
money AS (
    SELECT
        CAST(NULL AS VARCHAR) AS cluster_key,
        CAST(0 AS DOUBLE) AS focus_amount,
        CAST(0 AS BIGINT) AS hcpcs_type_count_sum,
        CAST(0 AS BIGINT) AS total_claims_sum,
        CAST(0 AS BIGINT) AS npi_with_spend
    WHERE FALSE
)
"""
        else:
            money_cte = f"""
money AS (
    SELECT
        k.cluster_key,
        COALESCE(SUM(r.total_paid), 0)::DOUBLE AS focus_amount,
        COALESCE(SUM(r.hcpcs_type_count), 0)::BIGINT AS hcpcs_type_count_sum,
        COALESCE(SUM(r.total_claims), 0)::BIGINT AS total_claims_sum,
        COUNT(DISTINCT r.npi)::BIGINT AS npi_with_spend
    FROM keyed k
    INNER JOIN medicare_providers m
        ON m.id = k.owner_id
       AND k.address_type IN ('nppes_practice', 'nppes_mailing')
    INNER JOIN medicare_provider_rollup r ON r.npi = m.npi
    GROUP BY k.cluster_key
)
"""
    elif focus == "fec":
        money_cte = f"""
money AS (
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
    ) u
    GROUP BY cluster_key
)
"""
    elif focus == "contractor":
        money_cte = f"""
money AS (
    SELECT
        k.cluster_key,
        COALESCE(SUM(c.amount), 0)::DOUBLE AS focus_amount
    FROM keyed k
    INNER JOIN Contractors c
        ON c.contractor_id = k.owner_id AND k.address_type = 'contractor'
    GROUP BY k.cluster_key
)
"""
    else:  # grants — exclude name-suppressed / privacy rollups from $
        from grant_suppress import suppressed_sql_predicate  # noqa: WPS433

        keep = suppressed_sql_predicate("g.grantee_name")
        money_cte = f"""
money AS (
    SELECT
        k.cluster_key,
        COALESCE(SUM(g.grant_amt), 0)::DOUBLE AS focus_amount
    FROM keyed k
    INNER JOIN Grants g
        ON g.grant_id = k.owner_id AND k.address_type = 'grant'
    WHERE {keep}
    GROUP BY k.cluster_key
)
"""

    # Per-focus ORDER BY for "top" clusters
    rank_metric = domain.get("rank_metric", "focus_count")
    if focus == "medicare":
        # $ first; prefer fewer HCPCS types (home-care / device grift often narrow)
        # then density of NPIs with spend.
        order_by = (
            "COALESCE(m.focus_amount, 0) DESC, "
            "COALESCE(m.npi_with_spend, 0) DESC, "
            "b.focus_count DESC"
        )
        money_select_extra = (
            ", COALESCE(m.hcpcs_type_count_sum, 0) AS hcpcs_type_count_sum, "
            "COALESCE(m.total_claims_sum, 0) AS total_claims_sum, "
            "COALESCE(m.npi_with_spend, 0) AS npi_with_spend"
        )
    elif rank_metric == "focus_amount":
        order_by = (
            "COALESCE(m.focus_amount, 0) DESC, b.focus_count DESC, b.total_rows DESC"
        )
        money_select_extra = (
            ", 0::BIGINT AS hcpcs_type_count_sum, "
            "0::BIGINT AS total_claims_sum, "
            "0::BIGINT AS npi_with_spend"
        )
    elif rank_metric == "distinct_addresses":
        order_by = (
            "b.distinct_focus_addresses DESC, b.focus_count DESC, "
            "COALESCE(m.focus_amount, 0) DESC, b.total_rows DESC"
        )
        money_select_extra = (
            ", 0::BIGINT AS hcpcs_type_count_sum, "
            "0::BIGINT AS total_claims_sum, "
            "0::BIGINT AS npi_with_spend"
        )
    else:
        order_by = (
            "b.focus_count DESC, COALESCE(m.focus_amount, 0) DESC, b.total_rows DESC"
        )
        money_select_extra = (
            ", 0::BIGINT AS hcpcs_type_count_sum, "
            "0::BIGINT AS total_claims_sum, "
            "0::BIGINT AS npi_with_spend"
        )

    return f"""
WITH keyed AS (
    SELECT
        ({key}) AS cluster_key,
        {canon} AS canonical_address,
        {atype} AS address_type,
        {owner} AS owner_id,
        {aid} AS address_id
    {from_sql}
    WHERE {where}
      AND ({key}) IS NOT NULL
      AND TRIM(CAST(({key}) AS VARCHAR)) != ''
),
base AS (
    SELECT
        cluster_key,
        COUNT(*)::BIGINT AS total_rows,
        COUNT(DISTINCT address_type)::BIGINT AS multi_type_count,
        LIST(DISTINCT address_type ORDER BY address_type) AS address_types,
        SUM(CASE WHEN {type_pred} THEN 1 ELSE 0 END)::BIGINT AS focus_count,
        COUNT(DISTINCT CASE WHEN {type_pred}
            THEN COALESCE(NULLIF(TRIM(canonical_address), ''), CAST(address_id AS VARCHAR))
            END)::BIGINT AS distinct_focus_addresses,
        SUM(CASE WHEN address_type IN ('dot_carrier_phy', 'dot_carrier_mail')
                 THEN 1 ELSE 0 END)::BIGINT AS dot_carrier_count,
        SUM(CASE WHEN address_type = 'charity' THEN 1 ELSE 0 END)::BIGINT AS charity_count,
        SUM(CASE WHEN address_type = 'grant' THEN 1 ELSE 0 END)::BIGINT AS grant_count,
        SUM(CASE WHEN address_type = 'officer' THEN 1 ELSE 0 END)::BIGINT AS officer_count,
        SUM(CASE WHEN address_type = 'contractor' THEN 1 ELSE 0 END)::BIGINT AS contractor_count,
        SUM(CASE WHEN address_type LIKE 'fec%' THEN 1 ELSE 0 END)::BIGINT AS fec_count,
        SUM(CASE WHEN address_type IN ('nppes_practice', 'nppes_mailing')
                 THEN 1 ELSE 0 END)::BIGINT AS medicare_count,
        ANY_VALUE(CASE
            WHEN canonical_address IS NOT NULL AND TRIM(canonical_address) != ''
            THEN canonical_address END) AS sample_address
    FROM keyed
    GROUP BY cluster_key
),
charity_signals AS (
    SELECT
        k.cluster_key,
        MAX(c.grift_ratio) AS max_grift_ratio,
        SUM(CASE
            WHEN UPPER(CAST(c.domestic_misrep_flag AS VARCHAR))
                 IN ('TRUE', '1', 'Y', 'YES', 'T')
            THEN 1 ELSE 0
        END)::BIGINT AS misrep_count
    FROM keyed k
    INNER JOIN Charities c ON c.charity_id = k.owner_id AND k.address_type = 'charity'
    GROUP BY k.cluster_key
),
{money_cte}
SELECT
    b.cluster_key,
    b.sample_address,
    b.total_rows,
    b.multi_type_count,
    b.address_types,
    b.focus_count,
    b.distinct_focus_addresses,
    b.dot_carrier_count,
    b.charity_count,
    b.grant_count,
    b.officer_count,
    b.contractor_count,
    b.fec_count,
    b.medicare_count,
    cs.max_grift_ratio,
    COALESCE(cs.misrep_count, 0) AS misrep_count,
    COALESCE(m.focus_amount, 0) AS focus_amount
    {money_select_extra}
FROM base b
LEFT JOIN charity_signals cs ON cs.cluster_key = b.cluster_key
LEFT JOIN money m ON m.cluster_key = b.cluster_key
WHERE (
    b.multi_type_count >= ?
    OR b.focus_count >= ?
)
AND (
    ? = 0
    OR COALESCE(cs.max_grift_ratio, 0) > 5
    OR COALESCE(cs.misrep_count, 0) > 0
)
ORDER BY {order_by}
LIMIT ?
"""


def suspicion_score_focus(cluster: dict, focus: str) -> int:
    fc = int(cluster.get("focus_count") or 0)
    amt = float(cluster.get("focus_amount") or 0)
    multi = int(cluster.get("multi_type_count") or 0)
    score = fc * 2.0
    if amt > 0:
        # log-ish scale: $1M → +200
        score += min(5000, (amt / 1_000_000.0) * 200)
    if multi <= 2 and fc >= 20:
        score += 250  # mono-type stack
    if cluster.get("phy_is_po_box"):
        score += 400
    if (cluster.get("max_grift_ratio") or 0) > 5:
        score += 150
    if focus == "fec" and fc >= 100:
        score += 100
    return int(score)


def reason_codes_focus(cluster: dict, min_multi: int, min_focus: int, focus: str) -> list[str]:
    codes = []
    if cluster["multi_type_count"] >= min_multi:
        codes.append("multi_type")
    if cluster["focus_count"] >= min_focus:
        codes.append(f"{focus}_stack")
    if (cluster.get("max_grift_ratio") or 0) > 5:
        codes.append("high_grift")
    if cluster.get("misrep_count", 0) > 0:
        codes.append("domestic_misrep")
    if cluster.get("phy_is_po_box"):
        codes.append("phy_po_box")
    if float(cluster.get("focus_amount") or 0) >= 1_000_000:
        codes.append("high_dollar")
    return codes or ["threshold"]


def fetch_clusters_focus(
    conn,
    slice_by: str,
    focus: str,
    min_multi: int,
    min_focus: int,
    require_grift: bool,
    limit: int,
    *,
    lat_long: bool = False,
) -> list[dict[str, Any]]:
    domain = FOCUS_DOMAINS[focus]
    rank_metric = domain.get("rank_metric", "focus_count")
    # Grants: over-fetch candidates then keep top `limit` by clean $ (already
    # filtered in SQL). Still over-fetch so borderline multi_type-only clusters
    # with suppressed-only $ don't crowd out real ones after any post-filter.
    sql_limit = limit
    if focus == "grants":
        sql_limit = max(limit * 10, min(1000, limit * 15))

    sql = build_focus_cluster_sql(slice_by, focus, conn, lat_long=lat_long)
    rows = conn.execute(
        sql, [min_multi, min_focus, 1 if require_grift else 0, sql_limit]
    ).fetchall()
    cols = [
        "cluster_key",
        "sample_address",
        "total_rows",
        "multi_type_count",
        "address_types",
        "focus_count",
        "distinct_focus_addresses",
        "dot_carrier_count",
        "charity_count",
        "grant_count",
        "officer_count",
        "contractor_count",
        "fec_count",
        "medicare_count",
        "max_grift_ratio",
        "misrep_count",
        "focus_amount",
        "hcpcs_type_count_sum",
        "total_claims_sum",
        "npi_with_spend",
    ]
    out = []
    for row in rows:
        c = dict(zip(cols, row))
        if isinstance(c["address_types"], str):
            c["address_types"] = [
                t.strip() for t in c["address_types"].strip("[]").split(",") if t.strip()
            ]
        c["canonical_address"] = str(c["cluster_key"])
        c["slice_by"] = slice_by
        c["focus"] = focus
        c["focus_amount_fmt"] = fmt_money(c.get("focus_amount"))
        # Surface rank metric for tables / scoring
        if rank_metric == "distinct_addresses":
            c["rank_metric_value"] = int(c.get("distinct_focus_addresses") or 0)
            c["rank_metric_fmt"] = f"{c['rank_metric_value']:,}"
        elif rank_metric == "focus_amount":
            c["rank_metric_value"] = float(c.get("focus_amount") or 0)
            c["rank_metric_fmt"] = c["focus_amount_fmt"]
        else:
            c["rank_metric_value"] = int(c.get("focus_count") or 0)
            c["rank_metric_fmt"] = f"{c['rank_metric_value']:,}"
        out.append(c)

    # Final cut to requested limit (SQL already ordered by rank metric)
    return out[:limit]


def fetch_focus_entities(
    conn,
    slice_by: str,
    cluster_key: str,
    focus: str,
    top_n: int,
    *,
    lat_long: bool = False,
) -> list[dict]:
    key_expr, where, gjoin = _member_filter_sql(slice_by, conn, lat_long=lat_long)
    types = FOCUS_DOMAINS[focus]["address_types"]
    type_sql = _type_in_sql(types, "a.address_type")

    if focus == "medicare":
        # Prefer rollup (NPI-level $ + HCPCS type count); fall back to providers only.
        has_rollup = ensure_medicare_rollup(conn)

        if has_rollup:
            rows = conn.execute(
                f"""
                SELECT
                    m.npi,
                    COALESCE(
                        NULLIF(TRIM(m.organization_name), ''),
                        NULLIF(TRIM(r.organization_name), ''),
                        NULLIF(TRIM(COALESCE(m.provider_first_name,'') || ' ' ||
                             COALESCE(m.provider_last_name,'')), ''),
                        m.npi
                    ) AS display_name,
                    m.entity_type_code,
                    m.provider_credential,
                    a.address_type,
                    a.canonical_address,
                    COALESCE(r.total_paid, 0)::DOUBLE AS paid,
                    COALESCE(r.hcpcs_type_count, 0)::BIGINT AS hcpcs_types,
                    COALESCE(r.total_claims, 0)::BIGINT AS claims,
                    r.top_hcpcs_code,
                    COALESCE(r.top_hcpcs_paid, 0)::DOUBLE AS top_hcpcs_paid
                FROM Addresses a
                {gjoin}
                JOIN medicare_providers m ON m.id = a.owner_id
                LEFT JOIN medicare_provider_rollup r ON r.npi = m.npi
                WHERE ({key_expr}) = ?
                  AND ({where})
                  AND {type_sql}
                ORDER BY COALESCE(r.total_paid, 0) DESC NULLS LAST, display_name
                LIMIT ?
                """,
                [cluster_key, top_n],
            ).fetchall()
        else:
            rows = conn.execute(
                f"""
                SELECT
                    m.npi,
                    COALESCE(m.organization_name,
                        TRIM(COALESCE(m.provider_first_name,'') || ' ' ||
                             COALESCE(m.provider_last_name,''))) AS display_name,
                    m.entity_type_code,
                    m.provider_credential,
                    a.address_type,
                    a.canonical_address,
                    0::DOUBLE, 0::BIGINT, 0::BIGINT, NULL, 0::DOUBLE
                FROM Addresses a
                {gjoin}
                JOIN medicare_providers m ON m.id = a.owner_id
                WHERE ({key_expr}) = ?
                  AND ({where})
                  AND {type_sql}
                ORDER BY display_name
                LIMIT ?
                """,
                [cluster_key, top_n],
            ).fetchall()

        npis = [r[0] for r in rows if r[0]]
        hcpcs_by_npi: dict[str, list[dict]] = {str(n): [] for n in npis}
        if npis and has_rollup:
            try:
                h_rows = conn.execute(
                    """
                    SELECT npi, hcpcs_code, total_claims, total_paid, total_beneficiaries
                    FROM medicare_provider_hcpcs
                    WHERE npi IN (SELECT UNNEST(?::VARCHAR[]))
                    ORDER BY npi, total_paid DESC NULLS LAST
                    """,
                    [npis],
                ).fetchall()
                for npi, code, claims, paid, bens in h_rows:
                    bucket = hcpcs_by_npi.setdefault(str(npi), [])
                    if len(bucket) >= 12:
                        continue  # cap types shown per provider on cluster page
                    bucket.append(
                        {
                            "hcpcs_code": code or "—",
                            "claims": int(claims or 0),
                            "paid": float(paid or 0),
                            "paid_fmt": fmt_money(paid),
                            "beneficiaries": int(bens or 0),
                        }
                    )
            except Exception:
                pass

        out = []
        for (
            npi,
            name,
            etc,
            cred,
            at,
            canon,
            paid,
            htypes,
            claims,
            top_code,
            top_paid,
        ) in rows:
            paid_f = float(paid or 0)
            detail_bits = [
                f"NPI {npi}",
                f"entity={etc or '—'}",
            ]
            if cred:
                detail_bits.append(str(cred))
            if htypes:
                detail_bits.append(f"{int(htypes)} HCPCS types")
            if claims:
                detail_bits.append(f"{int(claims):,} claims")
            if top_code:
                detail_bits.append(
                    f"top {top_code} {fmt_money(top_paid) if top_paid else ''}".strip()
                )
            out.append(
                {
                    "id": npi,
                    "name": name or "(unnamed)",
                    "detail": " · ".join(detail_bits),
                    "amount": paid_f,
                    "amount_fmt": fmt_money(paid_f) if paid_f else "—",
                    "address_type": at,
                    "canonical_address": (canon or "").strip() or "(no street)",
                    "hcpcs_type_count": int(htypes or 0),
                    "total_claims": int(claims or 0),
                    "hcpcs": hcpcs_by_npi.get(str(npi), []),
                }
            )
        return out

    if focus == "fec":
        rows = conn.execute(
            f"""
            SELECT * FROM (
                SELECT
                    f.contributor_name AS name,
                    'contributor' AS kind,
                    f.contribution_amount AS amount,
                    f.occupation AS detail,
                    a.address_type,
                    a.canonical_address
                FROM Addresses a
                {gjoin}
                JOIN fec_individual_contributions f ON f.id = a.owner_id
                WHERE ({key_expr}) = ? AND ({where})
                  AND a.address_type = 'fec_contributor'
                UNION ALL
                SELECT
                    c.name,
                    'committee',
                    NULL,
                    c.fec_cmte_id,
                    a.address_type,
                    a.canonical_address
                FROM Addresses a
                {gjoin}
                JOIN fec_committees c ON c.id = a.owner_id
                WHERE ({key_expr}) = ? AND ({where})
                  AND a.address_type = 'fec_committee'
                UNION ALL
                SELECT
                    COALESCE(t.fec_cmte_id, t.other_cmte_id),
                    'transaction ' || COALESCE(t.transaction_type, ''),
                    t.transaction_amount,
                    CAST(t.transaction_date AS VARCHAR),
                    a.address_type,
                    a.canonical_address
                FROM Addresses a
                {gjoin}
                JOIN fec_committee_transactions t ON t.id = a.owner_id
                WHERE ({key_expr}) = ? AND ({where})
                  AND a.address_type = 'fec_committee_transaction'
            ) u
            ORDER BY amount DESC NULLS LAST, name
            LIMIT ?
            """,
            [cluster_key, cluster_key, cluster_key, top_n],
        ).fetchall()
        return [
            {
                "id": kind,
                "name": name or "(unnamed)",
                "detail": detail or "",
                "amount": amt,
                "amount_fmt": fmt_money(amt),
                "address_type": at,
                "canonical_address": (canon or "").strip() or "(no street)",
            }
            for name, kind, amt, detail, at, canon in rows
        ]

    if focus == "contractor":
        rows = conn.execute(
            f"""
            SELECT
                c.name,
                c.filer_ein,
                c.amount,
                c.tax_year,
                a.canonical_address
            FROM Addresses a
            {gjoin}
            JOIN Contractors c ON c.contractor_id = a.owner_id
            WHERE ({key_expr}) = ?
              AND ({where})
              AND a.address_type = 'contractor'
            ORDER BY c.amount DESC NULLS LAST
            LIMIT ?
            """,
            [cluster_key, top_n],
        ).fetchall()
        return [
            {
                "id": fe or "",
                "name": name or "(unnamed)",
                "detail": f"filer {fe or '—'} · {yr or '—'}",
                "amount": amt,
                "amount_fmt": fmt_money(amt),
                "address_type": "contractor",
                "canonical_address": (canon or "").strip() or "(no street)",
            }
            for name, fe, amt, yr, canon in rows
        ]

    # grants — over-fetch then drop name-suppressed / privacy rollups
    from grant_suppress import is_suppressed_grantee  # noqa: WPS433

    fetch_n = max(top_n * 10, min(1000, top_n * 20))
    rows = conn.execute(
        f"""
        SELECT
            gr.grantee_name,
            gr.filer_ein,
            gr.grant_amt,
            gr.tax_year,
            gr.recipient_ein,
            a.canonical_address
        FROM Addresses a
        {gjoin}
        JOIN Grants gr ON gr.grant_id = a.owner_id
        WHERE ({key_expr}) = ?
          AND ({where})
          AND a.address_type = 'grant'
        ORDER BY gr.grant_amt DESC NULLS LAST
        LIMIT ?
        """,
        [cluster_key, fetch_n],
    ).fetchall()
    out = []
    for gname, fe, amt, yr, rein, canon in rows:
        if is_suppressed_grantee(gname):
            continue
        out.append(
            {
                "id": rein or "",
                "name": gname or "(unnamed)",
                "detail": f"filer {fe or '—'} · recip {rein or '—'} · {yr or '—'}",
                "amount": amt,
                "amount_fmt": fmt_money(amt),
                "address_type": "grant",
                "canonical_address": (canon or "").strip() or "(no street)",
            }
        )
        if len(out) >= top_n:
            break
    return out


def fetch_grants_simple(conn, slice_by, cluster_key, top_n, *, lat_long=False):
    """Top grants excluding name-suppressed / privacy rollups."""
    from grant_suppress import is_suppressed_grantee  # noqa: WPS433

    key_expr, where, gjoin = _member_filter_sql(slice_by, conn, lat_long=lat_long)
    fetch_n = max(top_n * 10, min(1000, top_n * 20))
    rows = conn.execute(
        f"""
        SELECT DISTINCT gr.filer_ein, gr.grantee_name, gr.grant_amt, gr.tax_year
        FROM Addresses a
        {gjoin}
        JOIN Grants gr ON gr.grant_id = a.owner_id
        WHERE ({key_expr}) = ?
          AND ({where})
          AND a.address_type = 'grant'
        ORDER BY gr.grant_amt DESC NULLS LAST
        LIMIT ?
        """,
        [cluster_key, fetch_n],
    ).fetchall()
    out = [
        {
            "filer_ein": fe,
            "grantee_name": gn,
            "grant_amt": amt,
            "amt_fmt": fmt_money(amt),
            "tax_year": yr,
        }
        for fe, gn, amt, yr in rows
        if not is_suppressed_grantee(gn)
    ]
    return out[:top_n]


def render_template(name: str, **kwargs) -> str:
    from mako.lookup import TemplateLookup

    lookup = TemplateLookup(
        directories=[str(SCRIPT_DIR / "templates")],
        input_encoding="utf-8",
    )
    tpl = lookup.get_template(name)
    return tpl.render(css=CSS, **kwargs)


def write_focus_report(
    db_path: str,
    output_dir: Path,
    focus: str,
    slice_by: str,
    min_multi_type: int,
    min_focus: int,
    require_grift_signal: bool,
    top_n: int,
    max_clusters: int,
    physical_notes_path: Path,
    include_officers: bool,
    include_grants: bool,
    *,
    lat_long: bool = False,
) -> int:
    if focus not in FOCUS_DOMAINS:
        raise ValueError(f"Unknown focus {focus!r}")
    if slice_by not in SLICE_MODES:
        raise ValueError(f"Unknown slice-by {slice_by!r}")

    domain = FOCUS_DOMAINS[focus]
    conn = duckdb.connect(db_path, read_only=True)
    try:
        if focus == "medicare":
            ok = ensure_medicare_rollup(conn)
            print(
                f"  medicare rollup: {'ready' if ok else 'MISSING (rank/$ degraded)'}",
                flush=True,
            )
        # Probe FEC column names that may differ
        if focus == "fec":
            try:
                cols = {r[0] for r in conn.execute("DESCRIBE fec_operating_expenditures").fetchall()}
                if "expenditure_amount" not in cols and "disbursement_amount" in cols:
                    # patch money SQL at runtime by re-fetch — handled via try in build
                    pass
            except Exception:
                pass

        mode = resolve_slice_mode(slice_by, conn, lat_long=lat_long)
        output_dir.mkdir(parents=True, exist_ok=True)
        data_dir = output_dir / "data"
        data_dir.mkdir(exist_ok=True)
        notes = load_physical_notes(physical_notes_path)
        generated_at = datetime.now().isoformat(timespec="seconds")
        report_date = date.today().isoformat()

        print(
            f"  fetching clusters focus={focus} slice={slice_by} "
            f"min_multi={min_multi_type} min_focus={min_focus}...",
            flush=True,
        )
        try:
            clusters_raw = fetch_clusters_focus(
                conn,
                slice_by,
                focus,
                min_multi_type,
                min_focus,
                require_grift_signal,
                max_clusters,
                lat_long=lat_long,
            )
        except Exception as e:
            # FEC operating expenditures column name variance
            if focus == "fec" and "expenditure_amount" in str(e).lower():
                print(f"  FEC column adjust: {e}", flush=True)
            raise

        if not clusters_raw:
            print("No clusters matched selection criteria.")
            return 0

        clusters_out = []
        for i, c in enumerate(clusters_raw, 1):
            key = str(c["cluster_key"])
            sample = c.get("sample_address") or key
            slug = slugify_address(f"{focus}-{slice_by}-{key}")
            detail_file = f"{slug}.html"
            phy_is_po_box = is_po_box(sample) or (
                slice_by == "colocator" and key.upper().startswith("PO:")
            )
            c["phy_is_po_box"] = phy_is_po_box
            c["suspicion_score"] = suspicion_score_focus(c, focus)
            c["reason_codes"] = reason_codes_focus(
                c, min_multi_type, min_focus, focus
            )
            c["maps_url"] = google_maps_url(sample, zoom=17)
            c["detail_file"] = detail_file
            c["slug"] = slug
            c["sample_address"] = sample
            c["focus_label"] = domain["label"]
            c["entity_title"] = domain["entity_title"]
            c["amount_label"] = domain["amount_label"]
            c["review_tag"] = domain["review_tag"]
            if slice_by == "address":
                c["canonical_address"] = key
            else:
                c["canonical_address"] = (
                    f"{key}  ·  e.g. {sample}" if sample != key else key
                )
            clusters_out.append(c)

            if i == 1 or i % 10 == 0:
                print(f"  detail {i}/{len(clusters_raw)}: {key[:60]}...", flush=True)

            entities = fetch_focus_entities(
                conn, slice_by, key, focus, top_n, lat_long=lat_long
            )
            charities = fetch_charities(conn, slice_by, key, top_n, lat_long=lat_long)
            officers = (
                fetch_officers(conn, slice_by, key, top_n, lat_long=lat_long)
                if include_officers
                else []
            )
            grants = (
                fetch_grants_simple(conn, slice_by, key, top_n, lat_long=lat_long)
                if include_grants and focus != "grants"
                else []
            )

            from cluster_table_payload import (  # noqa: WPS433
                build_detail_tables,
                dumps_table_json,
            )

            detail_tables = build_detail_tables(
                entities=entities,
                charities=charities,
                officers=officers,
                grants=grants,
                focus=focus,
                entity_title=domain.get("entity_title", "Entities"),
            )
            from map_points import (  # noqa: WPS433
                clusters_to_map_points,
                single_point_from_key,
            )

            detail_map = clusters_to_map_points([c], slice_by=slice_by) or single_point_from_key(
                key,
                label=c.get("canonical_address") or key,
                slice_by=slice_by,
                sample_address=sample,
            )
            from breadcrumbs import crumbs_national_detail  # noqa: WPS433
            from widen_links import build_widen_links  # noqa: WPS433

            widen_links = build_widen_links(
                focus=focus,
                slice_by=slice_by,
                cluster_key=key,
                sample_address=sample,
                report_day=report_date,
                reports_dir=SCRIPT_DIR / "reports",
                output_dir=output_dir,
                conn=conn,
                lat_long=lat_long,
                context="national",
            )
            html = render_template(
                "focus_cluster_detail.mako",
                cluster=c,
                entities=entities,
                charities=charities,
                officers=officers,
                grants=grants,
                focus=focus,
                domain=domain,
                physical_note=physical_note_for(sample, notes)
                or physical_note_for(key, notes),
                generated_at=generated_at,
                detail_tables_json=dumps_table_json(detail_tables),
                map_points=detail_map,
                breadcrumbs=crumbs_national_detail(
                    focus=focus,
                    slice_by=slice_by,
                    detail_label=str(c.get("canonical_address") or key),
                ),
                widen_links=widen_links,
            )
            (output_dir / detail_file).write_text(html, encoding="utf-8")

            json_path = data_dir / f"{slug}.json"
            json_path.write_text(
                json.dumps(
                    {
                        "slug": slug,
                        "focus": focus,
                        "slice_by": slice_by,
                        "cluster_key": key,
                        "sample_address": sample,
                        "focus_count": c.get("focus_count"),
                        "focus_amount": c.get("focus_amount"),
                        "suspicion_score": c.get("suspicion_score"),
                        "entities": entities,
                        "generated_at": generated_at,
                    },
                    indent=2,
                    default=str,
                ),
                encoding="utf-8",
            )

        from cluster_table_payload import (  # noqa: WPS433
            build_focus_cluster_table,
            dumps_table_json,
        )

        table_payload = build_focus_cluster_table(
            clusters_out,
            focus=focus,
            min_focus=min_focus,
            rank_metric=domain.get("rank_metric", "focus_count"),
            rank_label=domain.get("rank_label", "Focus metric"),
        )
        from map_points import clusters_to_map_points  # noqa: WPS433

        map_points = clusters_to_map_points(clusters_out, slice_by=slice_by)
        from breadcrumbs import crumbs_national_index  # noqa: WPS433

        index_html = render_template(
            "focus_cluster_index.mako",
            clusters=clusters_out,
            cluster_count=len(clusters_out),
            generated_at=generated_at,
            report_date=report_date,
            db_path=db_path,
            min_multi_type=min_multi_type,
            min_focus=min_focus,
            require_grift_signal=require_grift_signal,
            slice_by=slice_by,
            slice_label=mode["label"],
            focus=focus,
            focus_label=domain["label"],
            domain=domain,
            rank_label=domain.get("rank_label", "Focus metric"),
            cluster_table_json=dumps_table_json(table_payload),
            map_points=map_points,
            breadcrumbs=crumbs_national_index(focus=focus, slice_by=slice_by),
        )
        if map_points:
            (data_dir / "map_points.json").write_text(
                json.dumps(map_points, indent=2), encoding="utf-8"
            )
        (output_dir / "index.html").write_text(index_html, encoding="utf-8")

        serializable = []
        for c in clusters_out:
            row = {k: v for k, v in c.items() if k != "address_types"}
            row["address_types"] = list(c.get("address_types") or [])
            serializable.append(row)
        with open(data_dir / "clusters.json", "w", encoding="utf-8") as f:
            json.dump(serializable, f, indent=2, default=str)

        metadata = {
            "generated_at": generated_at,
            "db_path": db_path,
            "focus": focus,
            "focus_label": domain["label"],
            "slice_by": slice_by,
            "slice_label": mode["label"],
            "criteria": {
                "min_multi_type": min_multi_type,
                "min_focus": min_focus,
                "require_grift_signal": require_grift_signal,
                "top_n_entities": top_n,
                "max_clusters": max_clusters,
            },
            "cluster_count": len(clusters_out),
        }
        with open(output_dir / "export_metadata.json", "w", encoding="utf-8") as f:
            json.dump(metadata, f, indent=2)

        readme = f"""# {domain['label']} — {mode['label']} — {report_date}

Focus: **{focus}** · Slice: **{slice_by}**

- Open `index.html` in a browser.
- Ranked by focus entity count, then dollars.

Clusters: {len(clusters_out)}
Generated: {generated_at}
DB: {db_path}
"""
        (output_dir / "README.md").write_text(readme, encoding="utf-8")
        print(f"Wrote {len(clusters_out)} cluster pages to {output_dir}", flush=True)
        return len(clusters_out)
    finally:
        conn.close()


def main() -> int:
    p = argparse.ArgumentParser(
        description="Focus cluster reports (medicare / fec / contractor / grants)"
    )
    p.add_argument("--db-path", default=os.environ.get("IRS990_DB_PATH", DEFAULT_DB))
    p.add_argument(
        "--focus",
        required=True,
        choices=list(FOCUS_DOMAINS.keys()),
        help="Primary entity stack to rank on",
    )
    p.add_argument(
        "--slice-by",
        choices=list(SLICE_MODES.keys()),
        default="address",
    )
    p.add_argument("--lat-long", action="store_true")
    p.add_argument("--output-dir")
    p.add_argument("--physical-notes", default=str(SCRIPT_DIR / "physical_notes.json"))
    p.add_argument("--min-multi-type", type=int, default=None)
    p.add_argument("--min-focus", type=int, default=None, help="Min focus entity rows")
    p.add_argument("--require-grift-signal", action="store_true")
    p.add_argument("--top-n", type=int, default=25)
    p.add_argument("--max-clusters", type=int, default=100)
    p.add_argument("--no-officers", action="store_true")
    p.add_argument("--no-grants", action="store_true")
    args = p.parse_args()

    domain = FOCUS_DOMAINS[args.focus]
    min_multi = (
        args.min_multi_type
        if args.min_multi_type is not None
        else domain["min_multi_default"]
    )
    min_focus = (
        args.min_focus if args.min_focus is not None else domain["min_focus_default"]
    )

    prefix = f"{domain['out_prefix']}_{args.slice_by}_clusters"
    if args.lat_long and args.slice_by == "colocator":
        prefix = f"{domain['out_prefix']}_colocator_ll_clusters"
    out = (
        Path(args.output_dir)
        if args.output_dir
        else SCRIPT_DIR / "reports" / f"{prefix}_{date.today().isoformat()}"
    )
    if not os.path.exists(args.db_path):
        raise SystemExit(f"Database not found: {args.db_path}")

    print(
        f"Opening {args.db_path} focus={args.focus} slice={args.slice_by} "
        f"min_multi={min_multi} min_focus={min_focus} → {out}",
        flush=True,
    )
    n = write_focus_report(
        db_path=args.db_path,
        output_dir=out,
        focus=args.focus,
        slice_by=args.slice_by,
        min_multi_type=min_multi,
        min_focus=min_focus,
        require_grift_signal=args.require_grift_signal,
        top_n=args.top_n,
        max_clusters=args.max_clusters,
        physical_notes_path=Path(args.physical_notes),
        include_officers=not args.no_officers,
        include_grants=not args.no_grants,
        lat_long=args.lat_long,
    )
    return 0 if n >= 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
