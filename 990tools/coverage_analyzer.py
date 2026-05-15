#!/usr/bin/env python3
"""
coverage_analyzer.py v1.3

Robust analyzer for large gzipped name canonicalization rules.
- Handles many JSON shapes
- Limits output size (top-N + max examples)
- Good debug prints when structure is unexpected

Usage:
  python 990tools/coverage_analyzer.py --rules name_rules_v19.1.json.gz --top-n 50
"""

import argparse
import json
import csv
import os
import gzip
from typing import Any, Dict, List, Set

TARGET_FAMILIES = ["BOY SCOUTS", "ROTARY", "KIWANIS", "SIERRA CLUB",
    "CHAMBER OF COMMERCE", "AMERICAN LEGION", "KNIGHTS OF COLUMBUS"]

SUSPICIOUS_ROOTS = {"1", "A", "THE", "AND", "OF", "FOR", "IN", "NEW", "CITY", "COUNTY",
    "HIGH", "ST", "INC", "LLC", "ASSOCIATION", "FOUNDATION"}

SIMPLES_CONTAMINANTS = {"ATTACHED", "VARIOUS", "MISCELLANEOUS"}
PHARMA_KEYWORDS = ["PHARMA", "SUBSIDY", "DRUG"]


def normalize_canonicals(raw: Any) -> List[Dict]:
    if isinstance(raw, list):
        if raw and isinstance(raw[0], (str, int)):
            print("[normalize] list of primitives -> wrapping")
            return [{"name": str(x), "variants": [], "ein": None} for x in raw]
        return [c if isinstance(c, dict) else {"name": str(c)} for c in raw]

    if isinstance(raw, dict):
        print(f"[normalize] dict with keys: {list(raw.keys())[:10]}... (total {len(raw)} keys)")
        # Try common list containers
        for k in ["canonicals", "data", "items", "rules", "entries", "results"]:
            if k in raw and isinstance(raw[k], list):
                print(f"[normalize] using key '{k}' with {len(raw[k])} items")
                return normalize_canonicals(raw[k])
        # If values look like canonical objects
        sample_vals = list(raw.values())[:5]
        if sample_vals and isinstance(sample_vals[0], dict) and "name" in sample_vals[0]:
            print("[normalize] treating dict values as canonicals")
            return list(raw.values())
        # Fallback: treat keys as names
        print("[normalize] treating dict keys as canonical names")
        return [{"name": str(k), "variants": [], "ein": None} for k in list(raw.keys())[:100000]]

    return [{"name": str(raw), "variants": [], "ein": None}]


def load_rules(path: str) -> List[Dict]:
    is_gz = path.endswith(".gz")
    opener = gzip.open if is_gz else open
    mode = "rt" if is_gz else "r"
    with opener(path, mode, encoding="utf-8") as f:
        data = json.load(f)
    print(f"Loaded {'gzipped ' if is_gz else ''}JSON")
    canonicals = normalize_canonicals(data)
    print(f"Normalized to {len(canonicals):,} canonical entries")
    return canonicals


def load_grantee_names(path: str) -> Set[str]:
    if not path or not os.path.exists(path): return set()
    s = set()
    with open(path, "r", encoding="utf-8") as f:
        for row in csv.reader(f, delimiter="\t"):
            if row: s.add(row[0].upper().strip())
    print(f"Loaded {len(s):,} grantee names")
    return s


def is_broad(name: str) -> bool:
    u = str(name).upper().strip()
    return u in SUSPICIOUS_ROOTS or (len(u) <= 2 and u.isalnum()) or (len(u.split()) == 1 and len(u) < 4)


def check_contamination(cans: List[Dict]) -> Dict:
    flags = []
    for c in cans:
        nm = str(c.get("name", "")).upper()
        if nm in SIMPLES_CONTAMINANTS:
            flags.append({"name": c.get("name"), "reason": "pharma_siding candidate"})
        if any(kw in nm for kw in PHARMA_KEYWORDS):
            flags.append({"name": c.get("name"), "reason": "pharma keyword"})
    return {"count": len(flags), "examples": flags[:30]}


def check_families(cans: List[Dict], gnames: Set[str]) -> Dict:
    out = {}
    for fam in TARGET_FAMILIES:
        fu = fam.upper()
        caps = [c.get("name") for c in cans if fu in str(c.get("name", "")).upper()]
        out[fam] = {"count": len(caps), "examples": caps[:8]}
    return out


def find_broad_roots(cans: List[Dict], top_n: int) -> List[Dict]:
    broad = []
    for c in cans:
        if is_broad(c.get("name", "")):
            broad.append({"name": c.get("name"), "variants": len(c.get("variants", []))})
    return sorted(broad, key=lambda x: -x["variants"])[:top_n]


def compute_stats(cans: List[Dict]) -> Dict:
    n = len(cans)
    vcounts = [len(c.get("variants", [])) for c in cans]
    return {
        "total": n,
        "avg_variants": round(sum(vcounts)/max(n,1), 2),
        "max_variants": max(vcounts) if vcounts else 0
    }


def write_report(stats, broad, contam, families, outdir, top_n):
    os.makedirs(outdir, exist_ok=True)
    path = os.path.join(outdir, "coverage_summary.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write("# Coverage Analysis (top-N limited)\n\n")
        f.write(f"Total canonicals: {stats['total']:,}\n")
        f.write(f"Avg variants: {stats['avg_variants']} | Max: {stats['max_variants']}\n\n")

        f.write(f"## Top {top_n} Broad Roots\n")
        for b in broad:
            f.write(f"- {b['name']} ({b['variants']} variants)\n")

        f.write("\n## Contamination (SIMPLES + pharma)\n")
        f.write(f"Flagged: {contam['count']}\n")
        for ex in contam.get("examples", [])[:20]:
            f.write(f"- {ex.get('name')} - {ex.get('reason')}\n")

        f.write("\n## Family Coverage\n")
        for fam, d in families.items():
            f.write(f"{fam}: {d['count']} captured | e.g. {d['examples']}\n")

        f.write("\n## Next steps\nFix broad roots and contamination, then re-run.\n")
    print(f"Report written: {path} (kept small)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rules", required=True)
    ap.add_argument("--grantee_tsv", default="")
    ap.add_argument("--output_dir", default="./coverage_analysis")
    ap.add_argument("--top-n", type=int, default=50, help="Limit broad roots & examples")
    args = ap.parse_args()

    print("=== Coverage Analyzer v1.3 ===")
    cans = load_rules(args.rules)
    gnames = load_grantee_names(args.grantee_tsv)

    stats = compute_stats(cans)
    broad = find_broad_roots(cans, args.top_n)
    contam = check_contamination(cans)
    fams = check_families(cans, gnames)

    write_report(stats, broad, contam, fams, args.output_dir, args.top_n)
    print("Done.")

if __name__ == "__main__":
    main()
