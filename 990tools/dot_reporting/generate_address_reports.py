#!/usr/bin/env python3
"""
generate_address_reports.py — Static HTML address cluster reports for fraud precursor review.

Spec: address_cluster_report.md

Usage:
  python generate_address_reports.py
  python generate_address_reports.py --db-path /Volumes/Data/final/irs990.duckdb --min-dot-carriers 50 --max-clusters 25
"""

from __future__ import annotations

import argparse
import json
import os
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus

import duckdb
from mako.template import Template

DEFAULT_DB = "/Volumes/Data/final/irs990.duckdb"
DEFAULT_FINAL_DIR = "/Volumes/Data/final"
SCRIPT_DIR = Path(__file__).resolve().parent

CLUSTER_SQL = """
WITH base AS (
    SELECT
        canonical_address,
        COUNT(*) AS total_rows,
        COUNT(DISTINCT address_type) AS multi_type_count,
        LIST(DISTINCT address_type ORDER BY address_type) AS address_types,
        SUM(CASE WHEN address_type IN ('dot_carrier_phy', 'dot_carrier_mail') THEN 1 ELSE 0 END)::BIGINT AS dot_carrier_count,
        SUM(CASE WHEN address_type = 'charity' THEN 1 ELSE 0 END)::BIGINT AS charity_count,
        SUM(CASE WHEN address_type = 'grant' THEN 1 ELSE 0 END)::BIGINT AS grant_count,
        SUM(CASE WHEN address_type = 'officer' THEN 1 ELSE 0 END)::BIGINT AS officer_count
    FROM Addresses
    WHERE canonical_address IS NOT NULL AND TRIM(canonical_address) != ''
    GROUP BY canonical_address
),
charity_signals AS (
    SELECT
        a.canonical_address,
        MAX(c.grift_ratio) AS max_grift_ratio,
        SUM(CASE
            WHEN UPPER(CAST(c.domestic_misrep_flag AS VARCHAR)) IN ('TRUE', '1', 'Y', 'YES', 'T')
            THEN 1 ELSE 0
        END)::BIGINT AS misrep_count
    FROM Addresses a
    INNER JOIN Charities c ON c.charity_id = a.owner_id AND a.address_type = 'charity'
    GROUP BY a.canonical_address
),
dot_active AS (
    SELECT
        a.canonical_address,
        SUM(CASE WHEN d.status_code = 'A' THEN COALESCE(d.power_units, 0) ELSE 0 END)::BIGINT AS active_power_units
    FROM Addresses a
    INNER JOIN dot_carriers d ON d.id = a.owner_id
        AND a.address_type IN ('dot_carrier_phy', 'dot_carrier_mail')
    GROUP BY a.canonical_address
)
SELECT
    b.canonical_address,
    b.total_rows,
    b.multi_type_count,
    b.address_types,
    b.dot_carrier_count,
    b.charity_count,
    b.grant_count,
    b.officer_count,
    cs.max_grift_ratio,
    COALESCE(cs.misrep_count, 0) AS misrep_count,
    COALESCE(da.active_power_units, 0) AS active_power_units
FROM base b
LEFT JOIN charity_signals cs ON cs.canonical_address = b.canonical_address
LEFT JOIN dot_active da ON da.canonical_address = b.canonical_address
WHERE (
    b.multi_type_count >= ?
    OR b.dot_carrier_count >= ?
)
AND (
    ? = 0
    OR COALESCE(cs.max_grift_ratio, 0) > 5
    OR COALESCE(cs.misrep_count, 0) > 0
)
ORDER BY COALESCE(da.active_power_units, 0) DESC, b.dot_carrier_count DESC
LIMIT ?
"""

CSS = """
* { box-sizing: border-box; }
body { font-family: system-ui, -apple-system, sans-serif; margin: 1.5rem; color: #1a1a1a; line-height: 1.45; max-width: 1200px; }
header h1 { margin-bottom: 0.25rem; }
.meta { color: #555; font-size: 0.9rem; }
nav { margin-bottom: 1rem; }
table { width: 100%; border-collapse: collapse; margin: 1rem 0; font-size: 0.88rem; }
th, td { border: 1px solid #ddd; padding: 0.45rem 0.55rem; text-align: left; vertical-align: top; }
th { background: #f4f4f4; position: sticky; top: 0; }
tr:nth-child(even) { background: #fafafa; }
a { color: #0b57d0; }
.dot-heavy { background: #fff4e5 !important; }
.flag-misrep { background: #fde8e8; }
.flag-grift { background: #fff0d6; }
.chips { margin: 0.75rem 0; }
.chip { display: inline-block; background: #e8f0fe; color: #174ea6; padding: 0.2rem 0.55rem; border-radius: 999px; font-size: 0.8rem; margin-right: 0.35rem; }
.physical { background: #f0f7ff; border-left: 4px solid #0b57d0; padding: 0.75rem 1rem; margin: 1rem 0; }
.cards { display: flex; flex-wrap: wrap; gap: 0.75rem; margin: 1rem 0; }
.card { background: #f8f9fa; border: 1px solid #e0e0e0; border-radius: 8px; padding: 0.75rem 1rem; min-width: 110px; }
.card strong { display: block; font-size: 1.35rem; }
.card span { font-size: 0.8rem; color: #666; }
.types { font-size: 0.9rem; }
footer { margin-top: 2rem; color: #666; font-size: 0.85rem; }
@media (max-width: 700px) { body { margin: 0.75rem; } table { font-size: 0.8rem; } }
"""


def is_po_box(address: str) -> bool:
    """Detect if an address is a PO Box (for physical address red flagging)."""
    if not address:
        return False
    a = address.upper()
    return bool(re.search(r'\bP\.?\s*O\.?\s*BOX\b|\bPO\s*BOX\b|\bP\.?O\.?B\.?\b', a))


def slugify_address(addr: str) -> str:
    s = addr.lower().strip()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return (s.strip("-") or "cluster")[:120]


def google_maps_url(address: str, zoom: int = 17) -> str:
    return f"https://www.google.com/maps/search/?api=1&query={quote_plus(address)}&zoom={zoom}"


def street_view_url(address: str, heading: int) -> str:
    """Generate Google Street View link with specific heading.
    0 = North, 90 = East, 180 = South, 270 = West.
    """
    return (
        "https://www.google.com/maps/@?api=1&map_action=pano"
        f"&viewpoint={quote_plus(address)}&heading={heading}&pitch=0"
    )


def fmt_money(value: float | None) -> str:
    if value is None:
        return "—"
    if abs(value) >= 1_000_000:
        return f"${value / 1_000_000:,.2f}M"
    if abs(value) >= 1_000:
        return f"${value:,.0f}"
    return f"${value:,.2f}"


def load_physical_notes(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return {k.strip(): v for k, v in data.items()}


def physical_note_for(canonical: str, notes: dict[str, str]) -> str | None:
    if canonical in notes:
        return notes[canonical]
    key_lower = canonical.lower()
    for k, v in notes.items():
        if k.lower() == key_lower:
            return v
    return None


def reason_codes(cluster: dict[str, Any], min_multi: int, min_dot: int) -> list[str]:
    codes = []
    if cluster["multi_type_count"] >= min_multi:
        codes.append("multi_type")
    if cluster["dot_carrier_count"] >= min_dot:
        codes.append("dot_stack")
    if (cluster.get("max_grift_ratio") or 0) > 5:
        codes.append("high_grift")
    if cluster.get("misrep_count", 0) > 0:
        codes.append("domestic_misrep")
    if cluster.get("phy_is_po_box"):
        codes.append("phy_po_box")
    return codes or ["threshold"]


def suspicion_score(cluster: dict) -> int:
    dot_count = cluster.get('dot_carrier_count', 0)
    active_count = cluster.get('dot_active_count', 0)
    inactive_count = cluster.get('dot_inactive_count', 0)
    active_power = cluster.get('dot_active_power_units', 0)
    inactive_power = cluster.get('dot_inactive_power_units', 0)
    multi_type = cluster.get('multi_type_count', 0)
    phy_is_po_box = cluster.get('phy_is_po_box', False)

    if dot_count == 0:
        return 0

    ia_ratio = inactive_count / dot_count if dot_count > 0 else 0

    score = 0

    # Base score from total carriers
    score += dot_count * 1.2

    # Strong penalty for high inactive ratio
    score += ia_ratio * 1400

    # Extra weight on inactive power units (very suspicious)
    score += inactive_power * 1.6

    # Still give some weight to active power units, but less when IA ratio is high
    if ia_ratio > 0.5:
        score += active_power * 0.5
    else:
        score += active_power * 0.9

    # Penalty for low address diversity
    if multi_type <= 2:
        score += 300

    # Physical address red flags
    if phy_is_po_box:
        score += 400

    return int(score)


def fetch_clusters(
    conn: duckdb.DuckDBPyConnection,
    min_multi: int,
    min_dot: int,
    require_grift: bool,
    limit: int,
) -> list[dict[str, Any]]:
    rows = conn.execute(
        CLUSTER_SQL,
        [min_multi, min_dot, 1 if require_grift else 0, limit],
    ).fetchall()
    cols = [
        "canonical_address", "total_rows", "multi_type_count", "address_types",
        "dot_carrier_count", "charity_count", "grant_count", "officer_count",
        "max_grift_ratio", "misrep_count", "active_power_units",
    ]
    clusters = []
    for row in rows:
        c = dict(zip(cols, row))
        if isinstance(c["address_types"], str):
            c["address_types"] = [t.strip() for t in c["address_types"].strip("[]").split(",") if t.strip()]
        clusters.append(c)
    return clusters


def fetch_charities(conn, canonical: str, top_n: int) -> list[dict]:
    rows = conn.execute(
        """
        SELECT DISTINCT c.ein, c.filer_name, c.tax_year, c.receipt_amt, c.grift_ratio, c.domestic_misrep_flag
        FROM Addresses a
        JOIN Charities c ON c.charity_id = a.owner_id
        WHERE a.canonical_address = ? AND a.address_type = 'charity'
        ORDER BY c.grift_ratio DESC NULLS LAST, c.receipt_amt DESC NULLS LAST
        LIMIT ?
        """,
        [canonical, top_n],
    ).fetchall()
    out = []
    for ein, name, year, receipt, grift, misrep in rows:
        misrep_flag = str(misrep or "").upper() in ("TRUE", "1", "Y", "YES", "T")
        out.append({
            "ein": ein, "filer_name": name, "tax_year": year,
            "receipt_amt": receipt, "receipt_fmt": fmt_money(receipt),
            "grift_ratio": grift, "misrep": misrep_flag,
        })
    return out


def fetch_officers(conn, canonical: str, top_n: int) -> list[dict]:
    rows = conn.execute(
        """
        SELECT DISTINCT o.full_name, o.first_name, o.last_name, o.compensation, o.tax_year
        FROM Addresses a
        JOIN Officers o ON o.officer_id = a.owner_id
        WHERE a.canonical_address = ? AND a.address_type = 'officer'
        ORDER BY o.compensation DESC NULLS LAST
        LIMIT ?
        """,
        [canonical, top_n],
    ).fetchall()
    out = []
    for full, first, last, comp, year in rows:
        display = full or f"{first or ''} {last or ''}".strip()
        out.append({
            "display_name": display, "tax_year": year,
            "compensation": comp, "comp_fmt": fmt_money(comp),
        })
    return out


def fetch_grants(conn, canonical: str, top_n: int) -> list[dict]:
    rows = conn.execute(
        """
        SELECT DISTINCT g.filer_ein, g.grantee_name, g.grant_amt, g.tax_year
        FROM Addresses a
        JOIN Grants g ON g.grant_id = a.owner_id
        WHERE a.canonical_address = ? AND a.address_type = 'grant'
        ORDER BY g.grant_amt DESC NULLS LAST
        LIMIT ?
        """,
        [canonical, top_n],
    ).fetchall()
    return [
        {
            "filer_ein": fe, "grantee_name": gn, "grant_amt": amt,
            "amt_fmt": fmt_money(amt), "tax_year": yr,
        }
        for fe, gn, amt, yr in rows
    ]


def fetch_dot_carriers(conn, canonical: str, top_n: int) -> list[dict]:
    rows = conn.execute(
        """
        SELECT DISTINCT d.dot_number, d.legal_name, d.dba_name, d.status_code, d.power_units, d.phone
        FROM Addresses a
        JOIN dot_carriers d ON d.id = a.owner_id
        WHERE a.canonical_address = ?
          AND a.address_type IN ('dot_carrier_phy', 'dot_carrier_mail')
        ORDER BY d.power_units DESC NULLS LAST, d.legal_name
        LIMIT ?
        """,
        [canonical, top_n],
    ).fetchall()
    return [
        {
            "dot_number": dot, "legal_name": legal, "dba_name": dba,
            "status_code": status, "power_units": pu, "phone": phone,
        }
        for dot, legal, dba, status, pu, phone in rows
    ]


def render_template(name: str, **kwargs) -> str:
    path = SCRIPT_DIR / "templates" / name
    tpl = Template(filename=str(path))
    return tpl.render(css=CSS, **kwargs)


def write_report(
    db_path: str,
    output_dir: Path,
    min_multi_type: int,
    min_dot_carriers: int,
    require_grift_signal: bool,
    top_n: int,
    max_clusters: int,
    physical_notes_path: Path,
    include_officers: bool,
    include_grants: bool,
) -> int:
    output_dir.mkdir(parents=True, exist_ok=True)
    data_dir = output_dir / "data"
    data_dir.mkdir(exist_ok=True)

    notes = load_physical_notes(physical_notes_path)
    generated_at = datetime.now().isoformat(timespec="seconds")
    report_date = date.today().isoformat()

    conn = duckdb.connect(db_path, read_only=True)
    try:
        clusters_raw = fetch_clusters(
            conn, min_multi_type, min_dot_carriers, require_grift_signal, max_clusters
        )
        if not clusters_raw:
            print("No clusters matched selection criteria.")
            return 0

        clusters_out = []
        for c in clusters_raw:
            canon = c["canonical_address"]
            slug = slugify_address(canon)
            detail_file = f"{slug}.html"

            # Fetch DOT carriers to compute active/inactive stats
            dot_carriers_full = fetch_dot_carriers(conn, canon, 9999)  # full list for stats + phone grouping

            active_count = sum(1 for d in dot_carriers_full if d.get("status_code") == "A")
            inactive_count = sum(1 for d in dot_carriers_full if d.get("status_code") == "I")
            active_power = sum(d.get("power_units") or 0 for d in dot_carriers_full if d.get("status_code") == "A")
            inactive_power = sum(d.get("power_units") or 0 for d in dot_carriers_full if d.get("status_code") == "I")

            phy_is_po_box = False
            if is_po_box(canon):
                phy_is_po_box = True

            c["dot_active_count"] = active_count
            c["dot_inactive_count"] = inactive_count
            c["dot_active_power_units"] = active_power
            c["dot_inactive_power_units"] = inactive_power
            c["ia_ratio"] = round(inactive_count / (active_count + inactive_count), 3) if (active_count + inactive_count) > 0 else 0
            c["inactive_pct"] = round(c["ia_ratio"] * 100, 1)
            c["phy_is_po_box"] = phy_is_po_box

            c["suspicion_score"] = suspicion_score(c)
            c["reason_codes"] = reason_codes(c, min_multi_type, min_dot_carriers)
            c["maps_url"] = google_maps_url(canon, zoom=17)
            c["detail_file"] = detail_file
            c["slug"] = slug
            clusters_out.append(c)

            charities = fetch_charities(conn, canon, top_n)
            officers = fetch_officers(conn, canon, top_n) if include_officers else []
            grants = fetch_grants(conn, canon, top_n) if include_grants else []
            dot_carriers_display = fetch_dot_carriers(conn, canon, top_n)  # limited for display tables if needed

            # Group by phone using the FULL list for accurate Active/Inactive power units
            from collections import defaultdict
            phone_groups = defaultdict(list)
            for d in dot_carriers_full:
                phone = d.get('phone') or 'No Phone'
                phone_groups[phone].append(d)

            phone_groups_sorted = sorted(
                phone_groups.items(),
                key=lambda item: sum(x.get('power_units') or 0 for x in item[1]),
                reverse=True
            )

            html = render_template(
                "address_cluster_detail.mako",
                cluster=c,
                charities=charities,
                officers=officers,
                grants=grants,
                dot_carriers=dot_carriers_display,
                phone_groups=phone_groups_sorted,
                physical_note=physical_note_for(canon, notes),
                generated_at=generated_at,
            )
            (output_dir / detail_file).write_text(html, encoding="utf-8")

            # Also write a clean JSON version for later consolidation (DOT lookup file, etc.)
            import json
            json_data = {
                "slug": slug,
                "canonical_address": c.get("canonical_address"),
                "suspicion_score": c.get("suspicion_score"),
                "dot_carrier_count": c.get("dot_carrier_count"),
                "active_power_units": c.get("dot_active_power_units"),
                "inactive_power_units": c.get("dot_inactive_power_units"),
                "ia_ratio": c.get("ia_ratio"),
                "physical_note": physical_note_for(canon, notes),
                "phone_groups": [],
                "generated_at": generated_at,
            }

            for phone, carriers in phone_groups_sorted:
                active = [d for d in carriers if d.get("status_code") == "A"]
                inactive = [d for d in carriers if d.get("status_code") != "A"]

                json_data["phone_groups"].append({
                    "phone": phone,
                    "total_power_units": sum((d.get("power_units") or 0) for d in carriers),
                    "active_count": len(active),
                    "inactive_count": len(inactive),
                    "active": active,
                    "inactive": inactive,
                })

            json_path = output_dir / "data" / f"{slug}.json"
            json_path.parent.mkdir(parents=True, exist_ok=True)
            json_path.write_text(json.dumps(json_data, indent=2, default=str), encoding="utf-8")

        index_html = render_template(
            "address_cluster_index.mako",
            clusters=clusters_out,
            cluster_count=len(clusters_out),
            generated_at=generated_at,
            report_date=report_date,
            db_path=db_path,
            min_multi_type=min_multi_type,
            min_dot_carriers=min_dot_carriers,
            require_grift_signal=require_grift_signal,
        )
        (output_dir / "index.html").write_text(index_html, encoding="utf-8")

        serializable = []
        for c in clusters_out:
            row = {k: v for k, v in c.items() if k != "address_types"}
            row["address_types"] = list(c["address_types"])
            serializable.append(row)
        with open(data_dir / "clusters.json", "w", encoding="utf-8") as f:
            json.dump(serializable, f, indent=2)

        metadata = {
            "generated_at": generated_at,
            "db_path": db_path,
            "criteria": {
                "min_multi_type": min_multi_type,
                "min_dot_carriers": min_dot_carriers,
                "require_grift_signal": require_grift_signal,
                "top_n_entities": top_n,
                "max_clusters": max_clusters,
            },
            "cluster_count": len(clusters_out),
        }
        with open(output_dir / "export_metadata.json", "w", encoding="utf-8") as f:
            json.dump(metadata, f, indent=2)

        readme = f"""# Address Cluster Report — {report_date}

Static HTML drill-down for high-signal canonical addresses.

- Open `index.html` in a browser (no server required).
- Addresses link to Google Maps search.
- Criteria in `export_metadata.json`.

Generated: {generated_at}
Clusters: {len(clusters_out)}
"""
        (output_dir / "README.md").write_text(readme, encoding="utf-8")

        print(f"Wrote {len(clusters_out)} cluster pages to {output_dir}")
        print(f"  index: {output_dir / 'index.html'}")
        return len(clusters_out)
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(description="Generate static address cluster HTML reports")
    parser.add_argument("--db-path", default=os.environ.get("IRS990_DB_PATH", DEFAULT_DB))
    parser.add_argument("--output-dir", help="Output folder (default: reports/address_clusters_YYYY-MM-DD)")
    parser.add_argument("--physical-notes", default=str(SCRIPT_DIR / "physical_notes.json"))
    parser.add_argument("--min-multi-type", type=int, default=7)
    parser.add_argument("--min-dot-carriers", type=int, default=50)
    parser.add_argument("--require-grift-signal", action="store_true")
    parser.add_argument("--top-n", type=int, default=20)
    parser.add_argument("--max-clusters", type=int, default=100)
    parser.add_argument("--no-officers", action="store_true")
    parser.add_argument("--no-grants", action="store_true")
    args = parser.parse_args()

    out = Path(args.output_dir) if args.output_dir else SCRIPT_DIR / "reports" / f"address_clusters_{date.today().isoformat()}"
    if not os.path.exists(args.db_path):
        raise SystemExit(f"Database not found: {args.db_path}")

    write_report(
        db_path=args.db_path,
        output_dir=out,
        min_multi_type=args.min_multi_type,
        min_dot_carriers=args.min_dot_carriers,
        require_grift_signal=args.require_grift_signal,
        top_n=args.top_n,
        max_clusters=args.max_clusters,
        physical_notes_path=Path(args.physical_notes),
        include_officers=not args.no_officers,
        include_grants=not args.no_grants,
    )


if __name__ == "__main__":
    main()
