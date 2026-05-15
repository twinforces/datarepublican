#!/usr/bin/env python3
"""
coverage_analyzer.py v1.4

MacOS dictionary-enhanced analyzer for name canonicalization rules.
- Uses /usr/share/dict/words (and friends) when available for real-word validation
- Enforces minimum meaningful content in non-SIMPLES canonicals
- Keeps reports small with --top-n

Usage on Mac:
  python 990tools/coverage_analyzer.py --rules name_rules_v19.1.json.gz --top-n 50

The dictionary check is enabled by default on MacOS and can be disabled with --no-use-macos-dict.
"""

import argparse
import json
import csv
import os
import gzip
import platform
from typing import Any, Dict, List, Set

TARGET_FAMILIES = ["BOY SCOUTS", "ROTARY", "KIWANIS", "SIERRA CLUB",
    "CHAMBER OF COMMERCE", "AMERICAN LEGION", "KNIGHTS OF COLUMBUS"]

SUSPICIOUS_ROOTS = {"1", "A", "THE", "AND", "OF", "FOR", "IN", "NEW", "CITY", "COUNTY",
    "HIGH", "ST", "INC", "LLC", "ASSOCIATION", "FOUNDATION"}

SIMPLES_CONTAMINANTS = {"ATTACHED", "VARIOUS", "MISCELLANEOUS"}
PHARMA_KEYWORDS = ["ANTI-DRUG", "DRUG COURT", "ALCOHOLDRUG", "PHARMA SUBSIDY"]

DICT_PATHS = [
    "/usr/share/dict/words",
    "/usr/share/dict/words.pre-dictionaries",
    "/usr/share/dict/propernames"
]


def load_dictionary() -> Set[str]:
    words = set()
    for p in DICT_PATHS:
        if os.path.exists(p):
            try:
                with open(p, "r", encoding="utf-8", errors="ignore") as f:
                    for line in f:
                        w = line.strip().lower()
                        if w and len(w) > 1:
                            words.add(w)
                print(f"Loaded dictionary from {p} ({len(words):,} words so far)")
            except Exception:
                pass
    return words


def has_real_word(name: str, dictionary: Set[str]) -> bool:
    if not dictionary:
        return True  # fallback if no dict
    tokens = [t.lower() for t in name.replace("-", " ").replace("_", " ").split() if t]
    return any(t in dictionary or len(t) >= 4 for t in tokens)


def normalize_canonicals(raw: Any) -> List[Dict]:
    if isinstance(raw, list):
        if raw and isinstance(raw[0], (str, int)):
            return [{"name": str(x), "variants": [], "ein": None} for x in raw]
        return [c if isinstance(c, dict) else {"name": str(c)} for c in raw]
    if isinstance(raw, dict):
        for k in ["canonicals", "data", "items", "rules", "entries"]:
            if k in raw and isinstance(raw[k], list):
                return normalize_canonicals(raw[k])
        sample = list(raw.values())[:3]
        if sample and isinstance(sample[0], dict) and "name" in sample[0]:
            return list(raw.values())
        return [{"name": str(k), "variants": [], "ein": None} for k in list(raw.keys())[:200000]]
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


def is_problematic(name: str, dictionary: Set[str]) -> bool:
    u = str(name).upper().strip()
    if u in SUSPICIOUS_ROOTS:
        return True
    if len(u) <= 2 and u.isalnum():
        return True
    if not has_real_word(name, dictionary):
        return True
    return False


def check_contamination(cans: List[Dict]) -> Dict:
    flags = []
    for c in cans:
        nm = str(c.get("name", "")).upper()
        for kw in PHARMA_KEYWORDS:
            if kw in nm:
                flags.append({"name": c.get("name"), "reason": "contextual pharma siding candidate"})
                break
    return {"count": len(flags), "examples": flags[:30]}


def check_families(cans: List[Dict]) -> Dict:
    out = {}
    for fam in TARGET_FAMILIES:
        fu = fam.upper()
        caps = [c.get("name") for c in cans if fu in str(c.get("name", "")).upper()]
        out[fam] = {"count": len(caps), "examples": caps[:6]}
    return out


def find_problematic_roots(cans: List[Dict], dictionary: Set[str], top_n: int) -> List[Dict]:
    probs = []
    for c in cans:
        if is_problematic(c.get("name", ""), dictionary):
            probs.append({"name": c.get("name"), "variants": len(c.get("variants", []))})
    return sorted(probs, key=lambda x: -x["variants"])[:top_n]


def compute_stats(cans: List[Dict]) -> Dict:
    n = len(cans)
    vcounts = [len(c.get("variants", [])) for c in cans]
    return {
        "total": n,
        "avg_variants": round(sum(vcounts) / max(n, 1), 2),
        "max_variants": max(vcounts) if vcounts else 0
    }


def write_report(stats, probs, contam, families, outdir, top_n, dict_used):
    os.makedirs(outdir, exist_ok=True)
    path = os.path.join(outdir, "coverage_summary.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write("# Coverage Analysis v1.4 (MacOS dictionary enhanced)\n\n")
        f.write(f"Total canonicals: {stats['total']:,} | Dictionary used: {dict_used}\n")
        f.write(f"Avg variants: {stats['avg_variants']} | Max: {stats['max_variants']}\n\n")

        f.write(f"## Top {top_n} Problematic Roots (short / non-word / blacklisted)\n")
        for p in probs:
            f.write(f"- {p['name']} ({p['variants']} variants)\n")

        f.write("\n## Contextual Pharma / Siding Candidates\n")
        f.write(f"Flagged: {contam['count']}\n")
        for ex in contam.get("examples", [])[:15]:
            f.write(f"- {ex.get('name')} - {ex.get('reason')}\n")

        f.write("\n## Family Coverage\n")
        for fam, d in families.items():
            f.write(f"{fam}: {d['count']} captured | e.g. {d.get('examples', [])}\n")

        f.write("\n## Recommendations\n- Add short/non-word roots to generator blacklist.
- Strengthen pharma siding with context patterns.
- Re-run generator and analyzer after fixes.\n")
    print(f"Report written: {path}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rules", required=True)
    ap.add_argument("--grantee_tsv", default="")
    ap.add_argument("--output_dir", default="./coverage_analysis")
    ap.add_argument("--top-n", type=int, default=50)
    ap.add_argument("--use-macos-dict", action="store_true", default=platform.system() == "Darwin")
    ap.add_argument("--no-use-macos-dict", dest="use_macos_dict", action="store_false")
    args = ap.parse_args()

    print("=== Coverage Analyzer v1.4 (MacOS dictionary) ===")
    dictionary = load_dictionary() if args.use_macos_dict else set()
    print(f"Dictionary mode: {'ON' if dictionary else 'OFF'}")

    cans = load_rules(args.rules)
    stats = compute_stats(cans)
    probs = find_problematic_roots(cans, dictionary, args.top_n)
    contam = check_contamination(cans)
    fams = check_families(cans)

    write_report(stats, probs, contam, fams, args.output_dir, args.top_n, bool(dictionary))
    print("Done.")

if __name__ == "__main__":
    main()
