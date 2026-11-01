#!/usr/bin/env python3
"""Bad-address cross-links from PPP/federal geo pins onto other Fun lanes.

A pin that also has NPPES / contractor / grant / charity / DOT / FEC rows
gets a combined panel. Live hrefs only when that lane already has a detail
page (national top-N often does not). Fun-relative paths match wrap layout.
"""

from __future__ import annotations

import html
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent
DOT = ROOT / "dot_reporting"
if str(DOT) not in sys.path:
    sys.path.insert(0, str(DOT))
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from breadcrumbs import focus_label  # noqa: E402
from generate_address_reports import slugify_address  # noqa: E402
from geo_zoom import (  # noqa: E402
    DOMAINS as GEO_DOMAINS,
    geo_detail_filename,
    geo_suite_dirname,
    parse_suite_dir,
)
from widen_links import detail_filename  # noqa: E402

DEFAULT_FUN = Path.home() / "Development" / "grumpytechbro.com" / "fun"

# Address type → Fun lanes. officer / bmf have no suite.
TYPE_LANES: dict[str, tuple[str, ...]] = {
    "nppes_practice": ("medicare",),
    "nppes_mailing": ("medicare",),
    "contractor": ("contractor",),
    "grant": ("grants",),
    "charity": ("grants_out", "usg"),
    "dot_carrier_phy": ("dot",),
    "dot_carrier_mail": ("dot",),
    "fec_committee": ("fec_committee",),
    "sam_entity_phy": ("federal",),
}

_BAD_SECTION_RE = re.compile(
    r'<section class="bad-address"[^>]*>.*?</section>',
    re.DOTALL | re.IGNORECASE,
)


def extra_overlap_roots() -> list[Path]:
    roots: list[Path] = []
    if DEFAULT_FUN.is_dir():
        roots.append(DEFAULT_FUN)
    dot_reports = DOT / "reports"
    if dot_reports.is_dir():
        roots.append(dot_reports)
    return roots


def fun_suite_dirname(focus: str, slice_by: str, *, by_state: bool = False) -> str:
    if focus in GEO_DOMAINS:
        return geo_suite_dirname(focus, slice_by, by_state=by_state)
    if focus == "dot":
        if by_state:
            return f"dot_{slice_by}_by_state"
        return f"{slice_by}_clusters"
    if focus == "fec_committee":
        return "fec_committee_colocator_clusters"
    if by_state:
        return f"{focus}_{slice_by}_by_state"
    return f"{focus}_{slice_by}_clusters"


def fun_detail_filename(
    focus: str,
    slice_by: str,
    cluster_key: str,
    *,
    by_state: bool = False,
    state: str | None = None,
) -> str:
    if focus in GEO_DOMAINS:
        return geo_detail_filename(cluster_key)
    if by_state and state:
        return detail_filename(slice_by, cluster_key, state=state)
    if focus in ("", "dot", None):
        return detail_filename(slice_by, cluster_key)
    return f"{slugify_address(f'{focus}-{slice_by}-{cluster_key}')}.html"


def unsafe_filename(colocator_key: str) -> str:
    return f"unsafe-{slugify_address(colocator_key)}.html"


def _esc(s: str) -> str:
    return html.escape(s, quote=True)


def _rel_from_page(reports_dir: Path, output_dir: Path, target_rel: str) -> str:
    try:
        from_rel = output_dir.resolve().relative_to(reports_dir.resolve())
    except ValueError:
        from_rel = Path(output_dir.name)
    return os.path.relpath(target_rel, str(from_rel))


def _exists(roots: list[Path], rel: str) -> bool:
    for root in roots:
        if (root / rel).is_file():
            return True
    return False


def _candidate_rels(
    *,
    focus: str,
    slice_by: str,
    cluster_key: str,
    state: str | None,
    colocator_key: str | None,
    slug: str | None = None,
) -> list[tuple[str, str]]:
    """(relpath under Fun, scope label) in preference order."""
    out: list[tuple[str, str]] = []
    use_slice = "colocator" if focus == "fec_committee" else slice_by
    use_key = cluster_key
    if focus == "fec_committee" and slice_by != "colocator":
        if not colocator_key:
            return out
        use_key = colocator_key

    suite = fun_suite_dirname(focus, use_slice, by_state=False)
    if focus in GEO_DOMAINS and slug:
        out.append((f"{suite}/{slug}.html", "national"))
    fn = fun_detail_filename(focus, use_slice, use_key)
    out.append((f"{suite}/{fn}", "national"))
    if state:
        st_suite = fun_suite_dirname(focus, use_slice, by_state=True)
        if focus in GEO_DOMAINS and slug:
            out.append((f"{st_suite}/states/{state}/{slug}.html", "by_state"))
        st_fn = fun_detail_filename(
            focus, use_slice, use_key, by_state=True, state=state,
        )
        out.append((f"{st_suite}/states/{state}/{st_fn}", "by_state"))
    return out


def _first_hit(
    roots: list[Path], rels: list[tuple[str, str]]
) -> tuple[str, str] | None:
    for rel, scope in rels:
        if _exists(roots, rel):
            return rel, scope
    return None


def build_overlap_hits(
    *,
    domain: str,
    slice_by: str,
    cluster: dict,
    types: list[tuple[str, int]],
    reports_dir: Path,
    output_dir: Path,
    search_roots: list[Path] | None = None,
) -> list[dict[str, Any]]:
    roots = list(search_roots or [reports_dir])
    key = str(cluster.get("cluster_key") or "").strip()
    if not key:
        return []
    st = (str(cluster.get("state") or "").strip().upper() or None)
    colocator_key = str(cluster.get("colocator") or "").strip() or None
    if not colocator_key:
        lat, lon = cluster.get("latitude"), cluster.get("longitude")
        if lat is not None and lon is not None:
            try:
                colocator_key = f"LL:{float(lat)}:{float(lon)}"
            except (TypeError, ValueError):
                colocator_key = None

    type_counts = {str(t): int(n) for t, n in types if t}
    lanes: dict[str, list[str]] = {}
    for t, n in type_counts.items():
        for focus in TYPE_LANES.get(t, ()):
            if focus == domain:
                continue
            lanes.setdefault(focus, []).append(t)

    # Same pin in other PPP cuts / leftover / federal, even without a type row
    # (a charged-off pin can also rank on forgiven / payroll).
    for focus in GEO_DOMAINS:
        if focus == domain or focus == "ppp":
            continue
        lanes.setdefault(focus, lanes.get(focus, []))

    hits: list[dict[str, Any]] = []
    seen_focus: set[str] = set()

    def add_hit(focus: str, *, kinds: list[str], extra_label: str | None = None) -> None:
        if focus in seen_focus:
            return
        rels = _candidate_rels(
            focus=focus,
            slice_by=slice_by,
            cluster_key=key,
            state=st,
            colocator_key=colocator_key,
            slug=str(cluster.get("slug") or "") or None,
        )
        found = _first_hit(roots, rels)
        rows = sum(type_counts.get(t, 0) for t in kinds)
        if found is None and not kinds and focus in GEO_DOMAINS:
            return
        if found is None and not kinds:
            return
        href = None
        scope = None
        if found:
            rel, scope = found
            href = _rel_from_page(reports_dir, output_dir, rel)
            seen_focus.add(focus)
        elif not kinds:
            return
        else:
            seen_focus.add(focus)
        hits.append(
            {
                "focus": focus,
                "label": extra_label or focus_label(focus),
                "href": href,
                "scope": scope,
                "types": kinds,
                "rows": rows,
            }
        )

    preferred = (
        "dot",
        "medicare",
        "contractor",
        "grants",
        "grants_out",
        "usg",
        "fec_committee",
        "federal",
        "ppp_charged_off",
        "ppp_not_yet_paid",
        "ppp_forgiven",
        "ppp_payroll",
        "ppp_zero_jobs",
    )
    for focus in preferred:
        if focus not in lanes:
            continue
        add_hit(focus, kinds=lanes.get(focus) or [])

    if colocator_key and any(
        t in type_counts for t in ("dot_carrier_phy", "dot_carrier_mail")
    ):
        rel = f"unsafe_colocators/{unsafe_filename(colocator_key)}"
        if _exists(roots, rel):
            hits.append(
                {
                    "focus": "unsafe_colocators",
                    "label": "DOT SMS Rogues",
                    "href": _rel_from_page(reports_dir, output_dir, rel),
                    "scope": "national",
                    "types": [
                        t
                        for t in ("dot_carrier_phy", "dot_carrier_mail")
                        if t in type_counts
                    ],
                    "rows": sum(
                        type_counts.get(t, 0)
                        for t in ("dot_carrier_phy", "dot_carrier_mail")
                    ),
                }
            )
    return hits


def type_hrefs(hits: list[dict[str, Any]]) -> dict[str, str]:
    out: dict[str, str] = {}
    for hit in hits:
        href = hit.get("href")
        if not href:
            continue
        for t in hit.get("types") or []:
            out.setdefault(t, href)
    return out


def bad_address_html(hits: list[dict[str, Any]]) -> str:
    if not hits:
        return ""
    live = [h for h in hits if h.get("href")]
    if not live and not any(h.get("types") for h in hits):
        return ""
    parts = [
        '<section class="bad-address" aria-label="Bad address">',
        "  <h2>Bad address</h2>",
        '  <p class="meta">This pin also shows up in other Fun lanes. '
        "Open a lane for that roster, or stay here for PPP / SAM / charities.</p>",
        "  <ul>",
    ]
    for h in hits:
        kinds = ", ".join(h.get("types") or [])
        rows = h.get("rows") or 0
        extra = []
        if kinds:
            extra.append(kinds)
        if rows:
            extra.append(f"{rows:,} rows")
        if h.get("scope") == "by_state":
            extra.append("state pack")
        suffix = f" — {'; '.join(extra)}" if extra else ""
        if h.get("href"):
            parts.append(
                f'    <li><a href="{_esc(h["href"])}">{_esc(h["label"])}</a>'
                f"{_esc(suffix)}</li>"
            )
        else:
            parts.append(
                f'    <li><span>{_esc(h["label"])}</span>{_esc(suffix)}'
                " (no detail page in this suite)</li>"
            )
    parts.append("  </ul>")
    parts.append("</section>")
    return "\n".join(parts)


def types_from_sidecar(cluster: dict) -> list[tuple[str, int]]:
    raw = cluster.get("types") or []
    out: list[tuple[str, int]] = []
    for item in raw:
        if isinstance(item, (list, tuple)) and len(item) >= 2:
            try:
                out.append((str(item[0]), int(item[1])))
            except (TypeError, ValueError):
                continue
        elif isinstance(item, dict) and "type" in item:
            try:
                out.append((str(item["type"]), int(item.get("rows") or 0)))
            except (TypeError, ValueError):
                continue
    return out


def types_from_html(text: str) -> list[tuple[str, int]]:
    m = re.search(
        r"window\.__TS_TABLES__\s*=\s*(\[.*?\]);\s*</script>",
        text,
        re.DOTALL,
    )
    if not m:
        return []
    try:
        tables = json.loads(m.group(1))
    except json.JSONDecodeError:
        return []
    for table in tables:
        if not isinstance(table, dict):
            continue
        if table.get("rootId") != "ts-types":
            continue
        out: list[tuple[str, int]] = []
        for row in table.get("rows") or []:
            if not isinstance(row, dict):
                continue
            t = row.get("type")
            if not t:
                continue
            try:
                out.append((str(t), int(row.get("rows") or 0)))
            except (TypeError, ValueError):
                out.append((str(t), 0))
        return out
    return []


def refresh_geo_overlap(
    reports_dir: Path,
    *,
    search_roots: list[Path] | None = None,
) -> dict[str, int]:
    """Rebuild Bad address on existing geo details once sibling Fun pages exist."""
    stats = {"pages": 0, "updated": 0, "skipped": 0}
    if not reports_dir.is_dir():
        return stats
    roots = list(search_roots or ([reports_dir] + extra_overlap_roots()))
    for suite in sorted(p for p in reports_dir.iterdir() if p.is_dir()):
        parsed = parse_suite_dir(suite.name)
        if not parsed:
            continue
        domain, slice_by, level = parsed
        htmls = (
            list((suite / "states").rglob("*.html"))
            if level == "by_state"
            else list(suite.glob("*.html"))
        )
        for html_path in htmls:
            if html_path.name == "index.html":
                continue
            sidecar = html_path.parent / "data" / f"{html_path.stem}.json"
            cluster: dict = {}
            if sidecar.is_file():
                try:
                    cluster = json.loads(sidecar.read_text(encoding="utf-8"))
                except Exception:
                    cluster = {}
            text = html_path.read_text(encoding="utf-8", errors="replace")
            types = types_from_sidecar(cluster) or types_from_html(text)
            if not cluster.get("cluster_key"):
                stats["skipped"] += 1
                continue
            stats["pages"] += 1
            cluster.setdefault("slice_by", slice_by)
            if level == "by_state" and html_path.parent.name != "data":
                parent = html_path.parent.name
                if len(parent) == 2:
                    cluster["state"] = parent
            hits = build_overlap_hits(
                domain=domain,
                slice_by=slice_by,
                cluster=cluster,
                types=types,
                reports_dir=reports_dir,
                output_dir=html_path.parent,
                search_roots=roots,
            )
            nav = bad_address_html(hits)
            if not nav:
                stats["skipped"] += 1
                continue
            if _BAD_SECTION_RE.search(text):
                new_text = _BAD_SECTION_RE.sub(nav, text, count=1)
            else:
                new_text = re.sub(
                    r'(<section class="widen-nav"[^>]*>.*?</section>)',
                    r"\1\n" + nav,
                    text,
                    count=1,
                    flags=re.DOTALL | re.IGNORECASE,
                )
                if new_text == text:
                    new_text = re.sub(
                        r'(<nav class="breadcrumbs"[^>]*>.*?</nav>)',
                        r"\1\n" + nav,
                        text,
                        count=1,
                        flags=re.DOTALL | re.IGNORECASE,
                    )
            if new_text == text:
                stats["skipped"] += 1
                continue
            html_path.write_text(new_text, encoding="utf-8")
            stats["updated"] += 1
    return stats
