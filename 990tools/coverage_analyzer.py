#!/usr/bin/env python3
"""
coverage_analyzer.py

Standalone analyzer for the name canonicalization rules output.
Run this locally on your machine against the generated rules file (and optionally
distinct_grantee_names.tsv) because the rules file is too large for the repo.

It surfaces:
- Over-broad roots (e.g. '1', single chars, ultra-common words)
- Under-covered families (BOY SCOUTS variants, etc.)
- SIMPLES list contamination (ATTACHED, VARIOUS, MISCELLANEOUS, pharma leakage)
- Basic coverage stats and actionable recommendations

Usage example:
  python 990tools/coverage_analyzer.py \
      --rules /path/to/your_rules.json \
      --grantee_tsv /path/to/distinct_grantee_names.tsv \
      --output_dir ./analysis_output

Then review the generated .md and .json reports and paste key sections back here.

The script is intentionally dependency-light (stdlib only) and flexible on input format.
Adjust the load_rules() function if your rules export is TSV/CSV/pickle instead of JSON.
"""

import argparse
import json
import csv
import os
import re
from collections import defaultdict, Counter
from typing import Any, Dict, List, Set, Tuple

# ============================================================
# CONFIG - easy to extend
# ============================================================

TARGET_FAMILIES = [
    "BOY SCOUTS", "BOYSCOUTS", "BOY SCOUT OF AMERICA",
    "ROTARY", "KIWANIS", "SIERRA CLUB", "CHAMBER OF COMMERCE",
    "AMERICAN LEGION", "KNIGHTS OF COLUMBUS", "LOYAL ORDER OF MOOSE"
]

# Roots that are almost always too broad
SUSPICIOUS_ROOTS = {
    "1", "A", "THE", "AND", "OF", "FOR", "IN", "NEW", "CITY", "COUNTY",
    "HIGH", "ST", "INC", "LLC", "ASSOCIATION", "FOUNDATION", "FUND",
    "PROJECT", "RESCUE", "SOCIETY", "MINISTRIES", "INSTITUTE"
}

# Terms that should have been caught by pharma_siding and kept out of general SIMPLES
SIMPLES_CONTAMINANTS = {"ATTACHED", "VARIOUS", "MISCELLANEOUS"}

PHARMA_KEYWORDS = [
    "PHARMA", "PHARMACEUTICAL", "SUBSIDY", "DRUG", "MEDICINE",
    "PFIZER", "MODERNA", "JOHNSON", "MERCK", "NOVARTIS"
]


def load_rules(rules_path: str) -> Dict[str, Any]:
    """Load rules. Tries JSON first. Extend here for other formats."""
    if not os.path.exists(rules_path):
        raise FileNotFoundError(f"Rules file not found: {rules_path}")

    try:
        with open(rules_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        print(f"Loaded rules as JSON from {rules_path}")
        return data
    except json.JSONDecodeError:
        print("JSON load failed. Trying line-based fallback (customize as needed)...")
        # Fallback: very simple line parser. Customize for your actual format.
        canonicals = []
        with open(rules_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                # Example heuristic: lines like "CANONICAL_NAME | variant1, variant2 | EIN"
                if "|" in line:
                    parts = [p.strip() for p in line.split("|")]
                    if len(parts) >= 2:
                        canonicals.append({
                            "name": parts[0],
                            "variants": parts[1].split(",") if len(parts) > 1 else [],
                            "ein": parts[2] if len(parts) > 2 else None
                        })
        return {"canonicals": canonicals}
    except Exception as e:
        raise RuntimeError(f"Could not load rules file: {e}")


def load_grantee_names(tsv_path: str) -> Set[str]:
    """Load distinct grantee names if available (for missed variant detection)."""
    if not tsv_path or not os.path.exists(tsv_path):
        return set()
    names = set()
    with open(tsv_path, "r", encoding="utf-8") as f:
        reader = csv.reader(f, delimiter="\t")
        for row in reader:
            if row:
                names.add(row[0].upper().strip())
    print(f"Loaded {len(names):,} distinct grantee names")
    return names


def is_suspicious_root(name: str) -> bool:
    name_upper = name.upper().strip()
    if name_upper in SUSPICIOUS_ROOTS:
        return True
    if len(name_upper) <= 2 and name_upper.isalnum():
        return True
    # Very short or single-word ultra-common terms
    if len(name_upper.split()) == 1 and len(name_upper) < 4:
        return True
    return False


def check_simples_contamination(canonicals: List[Dict]) -> Dict:
    """Look for pharma/noise terms that leaked into SIMPLES-style canonicals."""
    flags = []
    for c in canonicals:
        cname = c.get("name", "").upper()
        if cname in SIMPLES_CONTAMINANTS:
            flags.append({
                "canonical": c.get("name"),
                "ein": c.get("ein"),
                "reason": "Should have been pharma_sided or excluded from general SIMPLES"
            })
        for kw in PHARMA_KEYWORDS:
            if kw in cname:
                flags.append({
                    "canonical": c.get("name"),
                    "ein": c.get("ein"),
                    "reason": f"Contains pharma keyword '{kw}' - verify siding"
                })
                break
    return {"count": len(flags), "examples": flags[:20]}


def check_family_coverage(canonicals: List[Dict], grantee_names: Set[str]) -> Dict:
    """Measure how well target families captured their variants."""
    results = {}
    for family in TARGET_FAMILIES:
        family_upper = family.upper()
        captured = []
        for c in canonicals:
            cname = c.get("name", "").upper()
            if family_upper in cname or family_upper in str(c.get("variants", [])).upper():
                captured.append(c.get("name"))
        # Simple missed detection if grantee names provided
        missed = []
        if grantee_names:
            for gname in list(grantee_names)[:50000]:  # sample for speed
                if family_upper in gname and not any(family_upper in str(captured).upper() for _ in [1]):
                    missed.append(gname)
        results[family] = {
            "captured_count": len(captured),
            "examples_captured": captured[:10],
            "missed_sample": missed[:10] if missed else []
        }
    return results


def find_over_broad_roots(canonicals: List[Dict]) -> List[Dict]:
    """Flag roots that are likely too broad."""
    broad = []
    for c in canonicals:
        name = c.get("name", "")
        if is_suspicious_root(name):
            broad.append({
                "root": name,
                "ein": c.get("ein"),
                "variant_count": len(c.get("variants", [])),
                "note": "Extremely broad root - review or add to blacklist"
            })
    return broad[:50]  # top offenders


def compute_basic_stats(canonicals: List[Dict]) -> Dict:
    """High-level numbers."""
    total = len(canonicals)
    variant_counts = [len(c.get("variants", [])) for c in canonicals]
    avg_variants = sum(variant_counts) / max(total, 1)
    max_variants = max(variant_counts) if variant_counts else 0
    return {
        "total_canonicals": total,
        "avg_variants_per_canonical": round(avg_variants, 2),
        "max_variants": max_variants,
        "suspicious_root_count": len(find_over_broad_roots(canonicals))
    }


def generate_report(stats: Dict, broad_roots: List, simples_flags: Dict,
                    family_results: Dict, output_dir: str) -> None:
    """Write human + machine readable reports."""
    os.makedirs(output_dir, exist_ok=True)

    # Markdown summary
    md_path = os.path.join(output_dir, "coverage_summary.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("# Name Canonicalization Coverage Analysis\n\n")
        f.write(f"**Total canonicals analyzed:** {stats['total_canonicals']:,}\n")
        f.write(f"**Average variants per canonical:** {stats['avg_variants_per_canonical']}\n")
        f.write(f"**Max variants for one canonical:** {stats['max_variants']:,}\n\n")

        f.write("## Over-broad / Suspicious Roots\n")
        if broad_roots:
            for item in broad_roots[:15]:
                f.write(f"- **{item['root']}** (EIN: {item.get('ein')}) - {item['note']} (variants: {item['variant_count']})\n")
        else:
            f.write("No obvious broad roots flagged.\n")

        f.write("\n## SIMPLES Contamination (pharma / noise leakage)\n")
        f.write(f"Flagged items: {simples_flags['count']}\n")
        for ex in simples_flags.get('examples', [])[:10]:
            f.write(f"- {ex['canonical']} (EIN: {ex.get('ein')}) - {ex['reason']}\n")

        f.write("\n## Target Family Coverage\n")
        for fam, data in family_results.items():
            f.write(f"\n### {fam}\n")
            f.write(f"Captured canonicals: {data['captured_count']}\n")
            if data['examples_captured']:
                f.write("Examples: " + ", ".join(data['examples_captured'][:5]) + "\n")
            if data.get('missed_sample'):
                f.write("Missed sample (if grantee names provided): " + ", ".join(data['missed_sample'][:5]) + "\n")

        f.write("\n## Recommendations\n")
        f.write("- Review any '1' or single-char roots immediately.\n")
        f.write("- Strengthen pharma_siding pass if ATTACHED/VARIOUS/MISCELLANEOUS appear.\n")
        f.write("- Add more specific patterns for under-covered families like BOY SCOUTS.\n")
        f.write("- Re-run generator after fixes and compare reports.\n")

    # JSON for further processing
    json_path = os.path.join(output_dir, "analysis_flags.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump({
            "stats": stats,
            "broad_roots": broad_roots,
            "simples_contamination": simples_flags,
            "family_coverage": family_results
        }, f, indent=2)

    print(f"\nReports written to {output_dir}")
    print(f"  - {md_path}")
    print(f"  - {json_path}")


def main():
    parser = argparse.ArgumentParser(description="Analyze name canonicalization rules for coverage and quality issues.")
    parser.add_argument("--rules", required=True, help="Path to generated rules file (JSON preferred)")
    parser.add_argument("--grantee_tsv", default="", help="Optional path to distinct_grantee_names.tsv for missed variant detection")
    parser.add_argument("--output_dir", default="./coverage_analysis", help="Where to write reports")
    args = parser.parse_args()

    print("=== Coverage Analyzer v1 ===")
    rules_data = load_rules(args.rules)
    canonicals = rules_data.get("canonicals", rules_data) if isinstance(rules_data, dict) else rules_data

    grantee_names = load_grantee_names(args.grantee_tsv)

    print("Running checks...")
    stats = compute_basic_stats(canonicals)
    broad_roots = find_over_broad_roots(canonicals)
    simples_flags = check_simples_contamination(canonicals)
    family_results = check_family_coverage(canonicals, grantee_names)

    generate_report(stats, broad_roots, simples_flags, family_results, args.output_dir)

    print("\nDone. Review the reports and share key findings or the JSON here for deeper discussion.")


if __name__ == "__main__":
    main()
