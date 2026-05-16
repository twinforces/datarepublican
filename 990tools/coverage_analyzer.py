#!/usr/bin/env python3
"""
coverage_analyzer.py v1.5

Correctly handles the actual rules JSON structure (dict of name -> list or dict with variants).
Stricter numeric/ordinal detection + full processing (no artificial cap).
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

DICT_PATHS = ["/usr/share/dict/words", "/usr/share/dict/propernames"]


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
            except Exception:
                pass
    return words


def has_real_word(name: str, dictionary: Set[str]) -> bool:
    if not dictionary:
        return True
    tokens = [t.lower() for t in str(name).replace("-", " ").replace("_", " ").split() if t]
    return any(t in dictionary or (len(t) >= 4 and any(c.isalpha() for c in t)) for t in tokens)


def is_problematic(name: str, dictionary: Set[str]) -> bool:
    u = str(name).upper().strip()
    if u in SUSPICIOUS_ROOTS:
        return True
    if len(u) <= 3 and u.isalnum():
        return True
    # numeric-heavy or address-like
    digits = sum(c.isdigit() for c in u)
    if digits > len(u) * 0.4:
        return True
    if any(p in u for p in ["1ST", "2ND", "3RD", "4TH", " E ", " W ", " N ", " S "]):
        return True
    if not has_real_word(name, dictionary):
        return True
    return False


def normalize_canonicals(raw: Any) -> List[Dict]:
    if isinstance(raw, dict):
        result = []
        for key, val in raw.items():
            name = key.strip('"')
            if isinstance(val, list) and len(val) >= 2 and isinstance(val[1], dict):
                info = val[1]
            elif isinstance(val, dict):
                info = val
            else:
                info = {}
            result.append({
                "name": name,
                "variants": info.get("variants", []),
                "ein": info.get("ein", ""),
                "source_count": info.get("source_count", 0)
            })
        return result
    if isinstance(raw, list):
        return [c if isinstance(c, dict) else {"name": str(c), "variants": [], "ein": "", "source_count": 0} for c in raw]
    return [{"name": str(raw), "variants": [], "ein": "", "source_count": 0}]


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


def check_contamination(cans: List[Dict]) -> Dict:
    flags = []
    for c in cans:
        nm = str(c.get("name", "")).upper()
        for kw in PHARMA_KEYWORDS:
            if kw in nm:
                flags.append({"name": c.get("name"), "reason": "contextual pharma siding candidate"})
                break
    return {"count": len(flags), "examples": flags[:25]}


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
        f.write("# Coverage Analysis v1.5 (correct structure + stricter heuristics)\n\n")
        f.write(f"Total canonicals: {stats['total']:,} | Dictionary used: {dict_used}\n")
        f.write(f"Avg variants: {stats['avg_variants']} | Max: {stats['max_variants']}\n\n")

        f.write(f"## Top {top_n} Problematic Roots (short / numeric / non-word)\n")
        for p in probs:
            f.write(f"- {p['name']} ({p['variants']} variants)\n")

        f.write("\n## Contextual Pharma / Siding Candidates\n")
        f.write(f"Flagged: {contam['count']}\n")
        for ex in contam.get("examples", [])[:15]:
            f.write(f"- {ex.get('name')} - {ex.get('reason')}\n")

        f.write("\n## Family Coverage\n")
        for fam, d in families.items():
            f.write(f"{fam}: {d['count']} captured | e.g. {d.get('examples', [])}\n")

        rec = ("\n## Recommendations\n"
               "- Add short/non-word + numeric-heavy roots to generator blacklist.\n"
               "- Port the hybrid (dictionary + numeric) guard into generator STAGE 1/3.\n"
               "- Re-run generator and analyzer after fixes.\n")
        f.write(rec)
    print(f"Report written: {path}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rules", required=True)
    ap.add_argument("--grantee_tsv", default="")
    ap.add_argument("--output_dir", default="./coverage_analysis")
    ap.add_argument("--top-n", type=int, default=60)
    ap.add_argument("--use-macos-dict", action="store_true", default=platform.system() == "Darwin")
    ap.add_argument("--no-use-macos-dict", dest="use_macos_dict", action="store_false")
    args = ap.parse_args()

    print("=== Coverage Analyzer v1.5 ===")
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
