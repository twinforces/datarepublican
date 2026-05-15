#!/usr/bin/env python3
"""
coverage_analyzer.py

Standalone analyzer for the name canonicalization rules output.
Run this locally against gzipped or plain JSON rules.

v1.2 - more robust loading for different export structures.
"""

import argparse
import json
import csv
import os
import gzip
from collections import defaultdict, Counter
from typing import Any, Dict, List, Set

TARGET_FAMILIES = [
    "BOY SCOUTS", "BOYSCOUTS", "BOY SCOUT OF AMERICA",
    "ROTARY", "KIWANIS", "SIERRA CLUB", "CHAMBER OF COMMERCE",
    "AMERICAN LEGION", "KNIGHTS OF COLUMBUS", "LOYAL ORDER OF MOOSE"
]

SUSPICIOUS_ROOTS = {
    "1", "A", "THE", "AND", "OF", "FOR", "IN", "NEW", "CITY", "COUNTY",
    "HIGH", "ST", "INC", "LLC", "ASSOCIATION", "FOUNDATION", "FUND",
    "PROJECT", "RESCUE", "SOCIETY", "MINISTRIES", "INSTITUTE"
}

SIMPLES_CONTAMINANTS = {"ATTACHED", "VARIOUS", "MISCELLANEOUS"}

PHARMA_KEYWORDS = ["PHARMA", "PHARMACEUTICAL", "SUBSIDY", "DRUG", "MEDICINE",
    "PFIZER", "MODERNA", "JOHNSON", "MERCK", "NOVARTIS"]


def normalize_canonicals(raw: Any) -> List[Dict]:
    """Turn whatever the JSON contained into a clean list of dicts with name/variants/ein."""
    if isinstance(raw, list):
        if raw and isinstance(raw[0], str):
            print("Detected list of strings - wrapping as canonicals with empty variants.")
            return [{"name": str(item), "variants": [], "ein": None} for item in raw]
        if raw and isinstance(raw[0], dict):
            return raw
        return [{"name": str(item), "variants": [], "ein": None} for item in raw]

    if isinstance(raw, dict):
        # Common patterns
        for key in ["canonicals", "data", "items", "rules"]:
            if key in raw and isinstance(raw[key], list):
                print(f"Found list under key '{key}'")
                return normalize_canonicals(raw[key])
        # Single dict that looks like one canonical
        if "name" in raw:
            return [raw]
        # Fallback: treat values as names if possible
        vals = list(raw.values())
        if vals and isinstance(vals[0], (str, dict)):
            return normalize_canonicals(vals)
    return [{"name": str(raw), "variants": [], "ein": None}]


def load_rules(rules_path: str) -> List[Dict]:
    """Load and normalize gzipped or plain JSON rules."""
    if not os.path.exists(rules_path):
        raise FileNotFoundError(f"Rules file not found: {rules_path}")

    is_gz = rules_path.endswith(".gz")
    open_func = gzip.open if is_gz else open
    mode = "rt" if is_gz else "r"

    try:
        with open_func(rules_path, mode, encoding="utf-8") as f:
            data = json.load(f)
        print(f"Loaded {'gzipped ' if is_gz else ''}JSON from {rules_path}")
        canonicals = normalize_canonicals(data)
        print(f"Normalized to {len(canonicals):,} canonical entries")
        return canonicals
    except Exception as e:
        print(f"Primary JSON load failed ({e}). Trying line fallback...")
        canonicals = []
        with open_func(rules_path, mode, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"): continue
                if "|" in line:
                    parts = [p.strip() for p in line.split("|")]
                    canonicals.append({
                        "name": parts[0],
                        "variants": [v.strip() for v in parts[1].split(",")] if len(parts) > 1 else [],
                        "ein": parts[2] if len(parts) > 2 else None
                    })
        print(f"Fallback produced {len(canonicals):,} entries")
        return canonicals


def load_grantee_names(tsv_path: str) -> Set[str]:
    if not tsv_path or not os.path.exists(tsv_path):
        return set()
    names = set()
    with open(tsv_path, "r", encoding="utf-8") as f:
        for row in csv.reader(f, delimiter="\t"):
            if row: names.add(row[0].upper().strip())
    print(f"Loaded {len(names):,} distinct grantee names")
    return names


def is_suspicious_root(name: str) -> bool:
    name_upper = name.upper().strip()
    if name_upper in SUSPICIOUS_ROOTS: return True
    if len(name_upper) <= 2 and name_upper.isalnum(): return True
    if len(name_upper.split()) == 1 and len(name_upper) < 4: return True
    return False


def check_simples_contamination(canonicals: List[Dict]) -> Dict:
    flags = []
    for c in canonicals:
        cname = str(c.get("name", "")).upper()
        if cname in SIMPLES_CONTAMINANTS:
            flags.append({"canonical": c.get("name"), "ein": c.get("ein"),
                        "reason": "Should have been pharma_sided"})
        for kw in PHARMA_KEYWORDS:
            if kw in cname:
                flags.append({"canonical": c.get("name"), "ein": c.get("ein"),
                            "reason": f"Contains pharma keyword '{kw}'"})
                break
    return {"count": len(flags), "examples": flags[:20]}


def check_family_coverage(canonicals: List[Dict], grantee_names: Set[str]) -> Dict:
    results = {}
    for family in TARGET_FAMILIES:
        family_upper = family.upper()
        captured = [c.get("name") for c in canonicals
                    if family_upper in str(c.get("name", "")).upper() or
                       family_upper in str(c.get("variants", [])).upper()]
        missed = []
        if grantee_names:
            for gname in list(grantee_names)[:30000]:
                if family_upper in gname:
                    if not any(family_upper in str(c.get("name", "")).upper() or
                               family_upper in str(c.get("variants", [])).upper() for c in canonicals):
                        missed.append(gname)
        results[family] = {
            "captured_count": len(captured),
            "examples_captured": captured[:8],
            "missed_sample": missed[:8] if missed else []
        }
    return results


def find_over_broad_roots(canonicals: List[Dict]) -> List[Dict]:
    return [{
        "root": c.get("name"),
        "ein": c.get("ein"),
        "variant_count": len(c.get("variants", [])),
        "note": "Broad root - review"
    } for c in canonicals if is_suspicious_root(str(c.get("name", "")))]


def compute_basic_stats(canonicals: List[Dict]) -> Dict:
    total = len(canonicals)
    variant_counts = [len(c.get("variants", [])) for c in canonicals if isinstance(c, dict)]
    avg = sum(variant_counts) / max(total, 1) if total else 0
    return {
        "total_canonicals": total,
        "avg_variants_per_canonical": round(avg, 2),
        "max_variants": max(variant_counts) if variant_counts else 0,
        "suspicious_root_count": len(find_over_broad_roots(canonicals))
    }


def generate_report(stats, broad_roots, simples_flags, family_results, output_dir):
    os.makedirs(output_dir, exist_ok=True)
    md_path = os.path.join(output_dir, "coverage_summary.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("# Name Canonicalization Coverage Analysis\n\n")
        f.write(f"**Total canonicals:** {stats['total_canonicals']:,}\n")
        f.write(f"**Avg variants per canonical:** {stats['avg_variants_per_canonical']}\n")
        f.write(f"**Max variants:** {stats['max_variants']:,}\n\n")
        f.write("## Over-broad Roots\n")
        for item in (broad_roots or [])[:15]:
            f.write(f"- {item.get('root')} (EIN {item.get('ein')}) - {item.get('note')} (variants: {item.get('variant_count')})\n")
        f.write("\n## SIMPLES Contamination\n")
        f.write(f"Flagged: {simples_flags.get('count', 0)}\n")
        for ex in simples_flags.get('examples', [])[:10]:
            f.write(f"- {ex.get('canonical')} - {ex.get('reason')}\n")
        f.write("\n## Family Coverage\n")
        for fam, d in family_results.items():
            f.write(f"\n### {fam}\nCaptured: {d['captured_count']}\nExamples: {', '.join(map(str, d.get('examples_captured', [])[:5]))}\n")
        f.write("\n## Recommendations\n- Fix broad roots and pharma siding first.\n- Strengthen patterns for families like BOY SCOUTS.\n")
    print(f"Reports written to {output_dir}/coverage_summary.md")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--rules", required=True)
    p.add_argument("--grantee_tsv", default="")
    p.add_argument("--output_dir", default="./coverage_analysis")
    args = p.parse_args()

    print("=== Coverage Analyzer v1.2 ===")
    canonicals = load_rules(args.rules)
    grantee_names = load_grantee_names(args.grantee_tsv)

    print("Running checks...")
    stats = compute_basic_stats(canonicals)
    broad = find_over_broad_roots(canonicals)
    simples = check_simples_contamination(canonicals)
    families = check_family_coverage(canonicals, grantee_names)

    generate_report(stats, broad, simples, families, args.output_dir)
    print("Done.")

if __name__ == "__main__":
    main()
