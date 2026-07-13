#!/usr/bin/env python3
"""Build "Widen:" navigation links: address → colocator → loose → state."""

from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any

from generate_address_reports import slugify_address  # type: ignore
from map_points import parse_ll, parse_zip5, zip_from_text  # type: ignore


def _round_half_away(n: float) -> float:
    """Match DuckDB ROUND half-away-from-zero for positive/negative."""
    if n >= 0:
        return math.floor(n + 0.5)
    return math.ceil(n - 0.5)


def tight_ll_to_loose_key(ll_key: str, step: float = 0.5) -> str | None:
    """LL:lat:lon → 0.5° grid key LL:rLat:rLon (same as generate_address_reports)."""
    parsed = parse_ll(ll_key)
    if not parsed:
        return None
    lat, lon = parsed
    rlat = _round_half_away(lat / step) * step
    rlon = _round_half_away(lon / step) * step
    return f"LL:{rlat:.1f}:{rlon:.1f}"


def suite_dirname(
    focus: str,
    slice_by: str,
    day: str,
    *,
    by_state: bool = False,
) -> str:
    if by_state:
        f = focus if focus != "dot" else "dot"
        return f"{f}_{slice_by}_by_state_{day}"
    if focus in ("", "dot", None):
        return f"{slice_by}_clusters_{day}"
    return f"{focus}_{slice_by}_clusters_{day}"


def detail_filename(slice_by: str, cluster_key: str, *, state: str | None = None) -> str:
    if state:
        return f"{slugify_address(f'{state}-{slice_by}-{cluster_key}')}.html"
    return f"{slugify_address(f'{slice_by}-{cluster_key}')}.html"


def fetch_effective_colocator(conn, canonical_address: str) -> str | None:
    """Best colocator for a street address (Addresses + Geocoding)."""
    try:
        # Geocoding is keyed by canonical_address (no address_id FK).
        row = conn.execute(
            """
            SELECT COALESCE(
                NULLIF(TRIM(a.colocator), ''),
                NULLIF(TRIM(g.colocator), ''),
                CASE
                    WHEN g.latitude IS NOT NULL AND g.longitude IS NOT NULL
                    THEN 'LL:' || CAST(g.latitude AS VARCHAR) || ':'
                         || CAST(g.longitude AS VARCHAR)
                END,
                CASE
                    WHEN a.latitude IS NOT NULL AND a.longitude IS NOT NULL
                    THEN 'LL:' || CAST(a.latitude AS VARCHAR) || ':'
                         || CAST(a.longitude AS VARCHAR)
                END
            )
            FROM Addresses a
            LEFT JOIN Geocoding g
              ON g.canonical_address = a.canonical_address
            WHERE a.canonical_address = ?
            LIMIT 1
            """,
            [canonical_address],
        ).fetchone()
        if row and row[0] and str(row[0]).strip():
            return str(row[0]).strip()
    except Exception:
        # Fallback: Addresses only
        try:
            row = conn.execute(
                """
                SELECT COALESCE(
                    NULLIF(TRIM(colocator), ''),
                    CASE
                        WHEN latitude IS NOT NULL AND longitude IS NOT NULL
                        THEN 'LL:' || CAST(latitude AS VARCHAR) || ':'
                             || CAST(longitude AS VARCHAR)
                    END
                )
                FROM Addresses
                WHERE canonical_address = ?
                LIMIT 1
                """,
                [canonical_address],
            ).fetchone()
            if row and row[0] and str(row[0]).strip():
                return str(row[0]).strip()
        except Exception:
            pass
    return None


def fetch_primary_state(
    conn,
    slice_by: str,
    cluster_key: str,
    *,
    lat_long: bool = False,
) -> str | None:
    """Most common Addresses.state for members of this cluster key."""
    from generate_address_reports import _member_filter_sql  # type: ignore

    try:
        key_expr, where, gjoin = _member_filter_sql(
            slice_by, conn, lat_long=lat_long
        )
        row = conn.execute(
            f"""
            SELECT UPPER(TRIM(a.state)) AS st, COUNT(*) AS c
            FROM Addresses a
            {gjoin}
            WHERE ({key_expr}) = ?
              AND ({where})
              AND a.state IS NOT NULL AND LENGTH(TRIM(a.state)) = 2
            GROUP BY 1
            ORDER BY c DESC
            LIMIT 1
            """,
            [cluster_key],
        ).fetchone()
        if row and row[0]:
            return str(row[0]).upper()
    except Exception:
        pass
    return None


def os_path_rel(from_dir: Path, to_path: Path) -> str:
    import os

    return os.path.relpath(str(to_path), str(from_dir))


def build_widen_links(
    *,
    focus: str,
    slice_by: str,
    cluster_key: str,
    sample_address: str | None = None,
    state: str | None = None,
    report_day: str,
    reports_dir: Path,
    output_dir: Path,
    conn=None,
    lat_long: bool = False,
    context: str = "national",  # national | by_state
) -> list[dict[str, str]]:
    """Return [{label, href, key, level}, ...] for Widen: UI."""
    focus = focus or "dot"
    links: list[dict[str, str]] = []
    key = str(cluster_key or "").strip()
    sample = sample_address or ""
    st = (state or "").upper() or None

    # Resolve colocator for address (or zip via sample)
    colocator_key: str | None = None
    if slice_by == "address" and conn is not None:
        colocator_key = fetch_effective_colocator(conn, key)
    elif slice_by == "colocator":
        colocator_key = key
    elif slice_by == "zipcode" and conn is not None and sample:
        # optional: colocator of sample street — skip if multi-homed
        colocator_key = fetch_effective_colocator(conn, sample)

    # Loose key from tight LL (or already loose)
    loose_key: str | None = None
    if slice_by == "loose_colocator":
        loose_key = key if parse_ll(key) else None
    elif colocator_key and parse_ll(colocator_key):
        loose_key = tight_ll_to_loose_key(colocator_key)
    elif parse_ll(key):
        loose_key = tight_ll_to_loose_key(key)

    # State
    if not st and conn is not None:
        st = fetch_primary_state(conn, slice_by, key, lat_long=lat_long)
    if not st and sample:
        # crude: last , XX, zip pattern
        m = re.search(r",\s*([A-Za-z]{2})\s*,\s*\d{5}", sample)
        if m:
            st = m.group(1).upper()

    def add(level: str, label: str, target_slice: str, target_key: str, *, by_state: bool = False, state_code: str | None = None):
        if by_state and state_code:
            suite = suite_dirname(focus, target_slice, report_day, by_state=True)
            # state index vs detail
            if target_key == "__STATE_INDEX__":
                target = reports_dir / suite / "states" / state_code / "index.html"
            else:
                fn = detail_filename(target_slice, target_key, state=state_code)
                target = reports_dir / suite / "states" / state_code / fn
        else:
            suite = suite_dirname(focus, target_slice, report_day, by_state=False)
            fn = detail_filename(target_slice, target_key)
            target = reports_dir / suite / fn
        href = os_path_rel(output_dir, target)
        # Prefer linking even if not generated yet (overnight lag)
        links.append(
            {
                "level": level,
                "label": label,
                "href": href,
                "key": target_key if target_key != "__STATE_INDEX__" else state_code or "",
                "exists": "1" if target.exists() else "0",
            }
        )

    # 1) Address → colocator
    if slice_by == "address" and colocator_key:
        add(
            "colocator",
            "Colocator (all address variants)",
            "colocator",
            colocator_key,
        )

    # 2) Colocator (or address with colocator) → loose
    if slice_by in ("address", "colocator") and loose_key:
        add(
            "loose_colocator",
            "Loose colocator (0.5° cell)",
            "loose_colocator",
            loose_key,
        )

    # 3) Zip / address / colocator / loose → state index
    if st and slice_by in (
        "address",
        "colocator",
        "loose_colocator",
        "zipcode",
    ):
        # If we're already on a by-state page for this state, link to state index only
        if context == "by_state":
            add(
                "state",
                f"State {st} (all clusters)",
                slice_by,
                "__STATE_INDEX__",
                by_state=True,
                state_code=st,
            )
        else:
            # National → by-state suite for same focus + current slice's "widest" geo
            # Prefer loose if we have it, else zip, else colocator, else address
            state_slice = (
                "loose_colocator"
                if loose_key
                else ("zipcode" if slice_by == "zipcode" or parse_zip5(key) or zip_from_text(sample) else slice_by)
            )
            # For address/colocator national, jump to state index on loose or colocator by-state
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
            )

    return links
