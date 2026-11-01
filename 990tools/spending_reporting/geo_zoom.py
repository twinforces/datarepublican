#!/usr/bin/env python3
"""DOT-style Widen: zoom-out for PPP/federal geo pages.

Address → colocator (all street variants at the pin) → loose 0.5° → ZIP → state.
Suite dirs are ``{domain}_{slice}`` (colocator by-state stays ``{domain}_by_state``).
"""

from __future__ import annotations

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

from generate_address_reports import slugify_address  # noqa: E402
from map_points import parse_ll, parse_zip5, zip_from_text  # noqa: E402
from widen_links import render_widen_nav_html, tight_ll_to_loose_key  # noqa: E402

DOMAINS = (
    "ppp_charged_off",
    "ppp_not_yet_paid",
    "ppp_forgiven",
    "ppp_payroll",
    "ppp_zero_jobs",
    "federal",
    "ppp",
)
SLICES = ("address", "colocator", "zipcode", "loose_colocator")

_WIDEN_SECTION_RE = re.compile(
    r'<section class="widen-nav"[^>]*>.*?</section>',
    re.DOTALL | re.IGNORECASE,
)
_SUITE_RE = re.compile(
    r"^(?P<domain>"
    + "|".join(DOMAINS)
    + r")(?:_(?P<slice>address|zipcode|loose_colocator))?_by_state$"
)
_NAT_RE = re.compile(
    r"^(?P<domain>"
    + "|".join(DOMAINS)
    + r")_(?P<slice>address|colocator|zipcode|loose_colocator)$"
)


def geo_suite_dirname(domain: str, slice_by: str, *, by_state: bool = False) -> str:
    if by_state:
        if slice_by == "colocator":
            return f"{domain}_by_state"
        return f"{domain}_{slice_by}_by_state"
    return f"{domain}_{slice_by}"


def geo_detail_filename(cluster_key: str) -> str:
    return f"{slugify_address(str(cluster_key))}.html"


def _detail_path(
    *,
    reports_dir: Path,
    domain: str,
    target_slice: str,
    target_key: str,
    by_state: bool = False,
    state_code: str | None = None,
) -> Path:
    if by_state and state_code:
        suite = geo_suite_dirname(domain, target_slice, by_state=True)
        if target_key == "__STATE_INDEX__":
            return reports_dir / suite / "states" / state_code / "index.html"
        return reports_dir / suite / "states" / state_code / geo_detail_filename(target_key)
    suite = geo_suite_dirname(domain, target_slice, by_state=False)
    return reports_dir / suite / geo_detail_filename(target_key)


def _find_existing(
    *,
    reports_dir: Path,
    domain: str,
    target_slice: str,
    target_key: str,
    state_code: str | None,
) -> tuple[Path, str] | None:
    candidates: list[tuple[Path, str]] = [
        (
            _detail_path(
                reports_dir=reports_dir,
                domain=domain,
                target_slice=target_slice,
                target_key=target_key,
            ),
            "national",
        )
    ]
    if state_code:
        candidates.append(
            (
                _detail_path(
                    reports_dir=reports_dir,
                    domain=domain,
                    target_slice=target_slice,
                    target_key=target_key,
                    by_state=True,
                    state_code=state_code,
                ),
                "by_state",
            )
        )
    for path, kind in candidates:
        if path.exists():
            return path, kind
    return None


def _ll_from_cluster(cluster: dict) -> str | None:
    lat, lon = cluster.get("latitude"), cluster.get("longitude")
    if lat is None or lon is None:
        return None
    try:
        return f"LL:{float(lat)}:{float(lon)}"
    except (TypeError, ValueError):
        return None


def build_geo_widen_links(
    *,
    domain: str,
    slice_by: str,
    cluster: dict,
    reports_dir: Path,
    output_dir: Path,
    context: str = "national",
) -> list[dict[str, str]]:
    """Return Widen: entries. Same labels as trucking."""
    key = str(cluster.get("cluster_key") or "").strip()
    sample = str(cluster.get("sample_address") or "")
    st = (str(cluster.get("state") or "").strip().upper() or None)
    zip5 = (
        parse_zip5(str(cluster.get("zip_code") or ""))
        or parse_zip5(key)
        or zip_from_text(sample)
        or zip_from_text(key)
    )
    colocator_key: str | None = None
    if slice_by == "address":
        colocator_key = str(cluster.get("colocator") or "").strip() or _ll_from_cluster(cluster)
    elif slice_by == "colocator":
        colocator_key = key
    elif slice_by == "zipcode" and sample:
        colocator_key = str(cluster.get("colocator") or "").strip() or None

    loose_key: str | None = None
    if slice_by == "loose_colocator":
        loose_key = key if parse_ll(key) else None
    elif colocator_key and parse_ll(colocator_key):
        loose_key = tight_ll_to_loose_key(colocator_key)
    elif parse_ll(key):
        loose_key = tight_ll_to_loose_key(key)
    else:
        ll = _ll_from_cluster(cluster)
        if ll:
            loose_key = tight_ll_to_loose_key(ll)

    links: list[dict[str, str]] = []

    def add(
        level: str,
        label: str,
        target_slice: str,
        target_key: str,
        *,
        by_state: bool = False,
        state_code: str | None = None,
        prefer_existing: bool = False,
    ) -> None:
        sc = state_code or st
        target: Path | None = None
        exists = "0"
        resolved_kind = "national" if not by_state else "by_state"
        if prefer_existing or target_key == "__STATE_INDEX__":
            found = _find_existing(
                reports_dir=reports_dir,
                domain=domain,
                target_slice=target_slice,
                target_key=target_key,
                state_code=sc,
            )
            if found:
                target, resolved_kind = found
                exists = "1"
            elif sc and target_key != "__STATE_INDEX__":
                st_idx = (
                    reports_dir
                    / geo_suite_dirname(domain, target_slice, by_state=True)
                    / "states"
                    / sc
                    / "index.html"
                )
                if st_idx.exists():
                    target, resolved_kind, exists = st_idx, "by_state_index", "1"
                    label = f"{label} · {sc} index"
            if target is None and target_key != "__STATE_INDEX__":
                nat_idx = (
                    reports_dir
                    / geo_suite_dirname(domain, target_slice, by_state=False)
                    / "index.html"
                )
                if nat_idx.exists():
                    target, resolved_kind, exists = nat_idx, "national_index", "1"
                    label = f"{label} · suite"
        if target is None:
            target = _detail_path(
                reports_dir=reports_dir,
                domain=domain,
                target_slice=target_slice,
                target_key=target_key,
                by_state=by_state,
                state_code=sc if by_state else state_code,
            )
            exists = "1" if target.exists() else "0"
            if by_state:
                resolved_kind = "by_state"
        if resolved_kind == "by_state" and "state" not in label.lower():
            label = f"{label} · state pack"
        href = os.path.relpath(str(target), str(output_dir))
        links.append(
            {
                "level": level,
                "label": label,
                "href": href,
                "key": target_key if target_key != "__STATE_INDEX__" else sc or "",
                "exists": exists,
                "scope": resolved_kind,
            }
        )

    if slice_by == "address" and colocator_key:
        add(
            "colocator",
            "Colocator (all address variants)",
            "colocator",
            colocator_key,
            prefer_existing=True,
            state_code=st,
        )
    if slice_by in ("address", "colocator") and loose_key:
        add(
            "loose_colocator",
            "Loose colocator (0.5° cell)",
            "loose_colocator",
            loose_key,
            prefer_existing=True,
            state_code=st,
        )
    if slice_by in ("address", "colocator", "loose_colocator") and zip5:
        add(
            "zipcode",
            f"ZIP {zip5}",
            "zipcode",
            zip5,
            prefer_existing=True,
            state_code=st,
        )
    if st and slice_by in SLICES:
        if context == "by_state":
            add(
                "state",
                f"State {st} (all clusters)",
                slice_by,
                "__STATE_INDEX__",
                by_state=True,
                state_code=st,
                prefer_existing=True,
            )
        else:
            if slice_by in ("address", "colocator") and loose_key:
                state_slice = "loose_colocator"
            elif slice_by == "zipcode":
                state_slice = "zipcode"
            elif slice_by == "loose_colocator":
                state_slice = "loose_colocator"
            else:
                state_slice = "colocator"
            add(
                "state",
                f"State {st}",
                state_slice,
                "__STATE_INDEX__",
                by_state=True,
                state_code=st,
                prefer_existing=True,
            )
    return links


def widen_html(
    *,
    domain: str,
    slice_by: str,
    cluster: dict,
    reports_dir: Path,
    output_dir: Path,
    context: str = "national",
) -> str:
    return render_widen_nav_html(
        build_geo_widen_links(
            domain=domain,
            slice_by=slice_by,
            cluster=cluster,
            reports_dir=reports_dir,
            output_dir=output_dir,
            context=context,
        )
    )


def breadcrumbs_html(crumbs: list[dict[str, Any]]) -> str:
    if not crumbs:
        return ""
    parts = ['<nav class="breadcrumbs" aria-label="Breadcrumb">']
    n = len(crumbs)
    for i, crumb in enumerate(crumbs):
        if i:
            parts.append('  <span class="bc-sep">›</span>')
        href = crumb.get("href")
        label = str(crumb.get("label") or "")
        if href and i < n - 1:
            parts.append(f'  <a href="{_esc(href)}">{_esc(label)}</a>')
        else:
            parts.append(
                f'  <span class="bc-current" aria-current="page">{_esc(label)}</span>'
            )
    parts.append("</nav>")
    return "\n".join(parts)


def _esc(s: str) -> str:
    import html

    return html.escape(s, quote=True)


def parse_suite_dir(name: str) -> tuple[str, str, str] | None:
    """Return (domain, slice_by, national|by_state) or None."""
    m = _SUITE_RE.match(name)
    if m:
        return m.group("domain"), m.group("slice") or "colocator", "by_state"
    m = _NAT_RE.match(name)
    if m:
        return m.group("domain"), m.group("slice"), "national"
    return None


def refresh_geo_widen(reports_dir: Path) -> dict[str, int]:
    """Rebuild Widen: on existing geo detail HTML after sibling suites exist."""
    import json as json_lib

    stats = {"pages": 0, "updated": 0, "skipped": 0}
    if not reports_dir.is_dir():
        return stats
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
            if not sidecar.is_file():
                stats["skipped"] += 1
                continue
            stats["pages"] += 1
            cluster: dict = {}
            try:
                cluster = json_lib.loads(sidecar.read_text(encoding="utf-8"))
            except Exception:
                stats["skipped"] += 1
                continue
            if not cluster.get("cluster_key"):
                stats["skipped"] += 1
                continue
            text = html_path.read_text(encoding="utf-8", errors="replace")
            cluster.setdefault("slice_by", slice_by)
            st_code = cluster.get("state")
            if level == "by_state" and html_path.parent.name != "data":
                parent = html_path.parent.name
                if len(parent) == 2:
                    st_code = parent
                    cluster["state"] = st_code
            nav = widen_html(
                domain=domain,
                slice_by=slice_by,
                cluster=cluster,
                reports_dir=reports_dir,
                output_dir=html_path.parent,
                context="by_state" if level == "by_state" else "national",
            )
            if not nav:
                stats["skipped"] += 1
                continue
            if _WIDEN_SECTION_RE.search(text):
                new_text = _WIDEN_SECTION_RE.sub(nav, text, count=1)
            else:
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
