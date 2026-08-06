#!/usr/bin/env python3
"""Rebuild map_points.json and patch window.__LEAFLET_MAP__ embeds after map_points fixes.

Walks report suite trees that have clusters.json (or reuses existing map points'
cluster_key/sample fields). Prefer clusters.json when present.

Usage:
  python3 rehydrate_map_points.py --root reports
  python3 rehydrate_map_points.py --root ~/Development/grumpytechbro.com/fun
  python3 rehydrate_map_points.py --root reports --dry-run
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from map_points import clusters_to_map_points  # noqa: E402

_LEAFLET_RE = re.compile(
    r"(window\.__LEAFLET_MAP__\s*=\s*)(\{.*?\})(\s*;)",
    re.DOTALL,
)


def _infer_slice_by(path: Path) -> str | None:
    """Guess slice_by from suite directory name."""
    # walk up looking for suite-ish dirname
    for p in [path, *path.parents]:
        name = p.name.lower()
        for slice_name in (
            "loose_colocator",
            "colocator",
            "zipcode",
            "address",
        ):
            if f"_{slice_name}_" in f"_{name}_" or name.startswith(f"{slice_name}_"):
                return slice_name
    return None


def _clusters_from_map_points(old_points: list[dict]) -> list[dict]:
    """Minimal cluster dicts from prior map feature payloads."""
    out = []
    for pt in old_points:
        out.append(
            {
                "cluster_key": pt.get("cluster_key") or pt.get("label") or "",
                "canonical_address": pt.get("label") or pt.get("cluster_key") or "",
                "sample_address": pt.get("label") or "",
                "rank_metric_value": pt.get("metric"),
                "rank_metric_fmt": None,
                "detail_file": pt.get("href") or "",
                "focus_amount": pt.get("metric"),
                "focus_count": pt.get("metric"),
            }
        )
    return out


def _load_clusters(data_dir: Path) -> tuple[list[dict], str | None]:
    clusters_path = data_dir / "clusters.json"
    map_path = data_dir / "map_points.json"
    if clusters_path.is_file():
        raw = json.loads(clusters_path.read_text(encoding="utf-8"))
        if isinstance(raw, list):
            return raw, "clusters.json"
        if isinstance(raw, dict) and "clusters" in raw:
            return list(raw["clusters"]), "clusters.json"
    if map_path.is_file():
        pts = json.loads(map_path.read_text(encoding="utf-8"))
        if isinstance(pts, list) and pts:
            return _clusters_from_map_points(pts), "map_points.json→clusters"
    return [], None


def _patch_html(html_path: Path, points: list[dict], *, dry_run: bool) -> bool:
    text = html_path.read_text(encoding="utf-8", errors="replace")
    m = _LEAFLET_RE.search(text)
    if not m:
        return False
    try:
        payload = json.loads(m.group(2).replace("<\\/", "</"))
    except json.JSONDecodeError:
        # already has escaped form in file — try as stored
        try:
            payload = json.loads(m.group(2))
        except json.JSONDecodeError:
            print(f"  WARN: cannot parse LEAFLET_MAP in {html_path}", file=sys.stderr)
            return False
    old_points = payload.get("points") or []
    # Preserve rootId/height; replace points. If detail page has single point,
    # replace with rehydrated list (may be empty → leave alone).
    if not points and old_points:
        # recompute from old points' keys
        points = clusters_to_map_points(
            _clusters_from_map_points(old_points),
            slice_by=_infer_slice_by(html_path),
        )
    if not points:
        return False
    payload["points"] = points
    new_json = json.dumps(payload, ensure_ascii=False, default=str).replace("</", "<\\/")
    new_text = text[: m.start()] + m.group(1) + new_json + m.group(3) + text[m.end() :]
    if new_text == text:
        return False
    if not dry_run:
        html_path.write_text(new_text, encoding="utf-8")
    return True


def process_data_dir(data_dir: Path, *, dry_run: bool) -> dict[str, int]:
    stats = {"json": 0, "html": 0, "skip": 0}
    clusters, src = _load_clusters(data_dir)
    if not clusters:
        stats["skip"] += 1
        return stats
    slice_by = _infer_slice_by(data_dir)
    new_points = clusters_to_map_points(clusters, slice_by=slice_by)
    map_path = data_dir / "map_points.json"
    if new_points:
        if not dry_run:
            map_path.write_text(
                json.dumps(new_points, indent=2, ensure_ascii=False, default=str),
                encoding="utf-8",
            )
        stats["json"] += 1

    # Patch sibling index.html and any detail pages that embed maps.
    # State suite: data_dir = .../states/OR/data → parent has index + details
    # National: data_dir = .../data → parent has index + details
    parent = data_dir.parent
    html_files = list(parent.glob("*.html"))
    # Also patch if map is only on index
    for hp in html_files:
        # For multi-point index: use full list
        # For detail pages: recompute single cluster from filename/href match
        if hp.name == "index.html":
            if _patch_html(hp, new_points, dry_run=dry_run):
                stats["html"] += 1
            continue
        # detail: find matching point by href or recompute from clusters
        matched = [p for p in new_points if (p.get("href") or "") == hp.name]
        if not matched:
            # try cluster row with detail_file
            sub = [
                c
                for c in clusters
                if str(c.get("detail_file") or "").endswith(hp.name)
                or str(c.get("detail_file") or "") == hp.name
            ]
            if sub:
                matched = clusters_to_map_points(sub, slice_by=slice_by)
            else:
                # rehydrate from existing embed only
                matched = []
        if _patch_html(hp, matched, dry_run=dry_run):
            stats["html"] += 1
    return stats


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--root",
        type=Path,
        action="append",
        required=True,
        help="Report root to walk (repeatable)",
    )
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    totals = {"json": 0, "html": 0, "skip": 0, "dirs": 0}
    for root in args.root:
        root = root.expanduser().resolve()
        if not root.is_dir():
            print(f"ERROR: not a dir: {root}", file=sys.stderr)
            return 1
        data_dirs = sorted({p.parent for p in root.rglob("map_points.json")})
        # also clusters-only
        for p in root.rglob("clusters.json"):
            data_dirs.append(p.parent)
        data_dirs = sorted(set(data_dirs))
        print(f"Root {root}: {len(data_dirs)} data dirs")
        for dd in data_dirs:
            st = process_data_dir(dd, dry_run=args.dry_run)
            for k in ("json", "html", "skip"):
                totals[k] += st[k]
            totals["dirs"] += 1
    print(
        f"Done dirs={totals['dirs']} map_json={totals['json']} "
        f"html_patched={totals['html']} skipped={totals['skip']}"
        + (" (dry-run)" if args.dry_run else "")
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
