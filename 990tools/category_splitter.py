#!/usr/bin/env python3
"""
category_splitter.py

Repeatable tool to carve the master distinct names list into category-specific
buckets (churches, pharma, etc.) so we can run targeted Splink discovery on
each bucket.

This supports the "hybrid of a hybrid" workflow:
- Master list -> category buckets
- Run splink_pattern_miner.py --use-clean-tsv <bucket>.tsv on each
- Review with review_suggestions_tui.py (with per-category seeds for pre-bless)
- Curated output becomes high-quality seeds for the main pipeline.

Usage examples (dive into Splink mode):
  python category_splitter.py --input distinct_grantee_names_clean.tsv \
      --categories churches,pharma --out-dir category_buckets/

  # Then immediately dive into Splink on the pharma no-EIN-ish bucket:
  python splink_pattern_miner.py --use-clean-tsv category_buckets/pharma.tsv \
      --output suggestions_pharma.json --max-suggestions 200

  # Same for churches (leverages simple denomination keywords):
  python splink_pattern_miner.py --use-clean-tsv category_buckets/churches.tsv \
      --output suggestions_churches.json

The script also emits small <category>_seeds.json files that can be fed to the
TUI as --priority-canonicals for aggressive pre-blessing during review of that
bucket's suggestions.
"""
import argparse
import json
import re
import sys
from pathlib import Path
import pandas as pd

# Major simple denomination keywords (user note: CATHOLIC, LUTHERAN, BAPTIST are straightforward)
MAJOR_DENOMINATIONS = [
    "CATHOLIC", "LUTHERAN", "BAPTIST", "METHODIST", "PRESBYTERIAN",
    "EPISCOPAL", "SEVENTH DAY", "SEVENTH-DAY", "ADVENTIST",
    "ASSEMBLY OF GOD", "CHURCH OF CHRIST", "UNITED CHURCH OF CHRIST",
    "DISCIPLES OF CHRIST", "CONGREGATIONAL", "MENNONITE", "QUAKER",
    "MORAVIAN", "PENTECOSTAL", "HOLINESS", "SALVATION ARMY"
]

CHURCH_KEYWORDS = ["CHURCH"] + MAJOR_DENOMINATIONS

def load_pharma_patterns(pharma_json: str = "big_pharma_subsidy.json"):
    """Load the patterns used for pharma inference (including no-EIN subsidy rows)."""
    p = Path(pharma_json)
    if not p.exists():
        print(f"Warning: {pharma_json} not found. Pharma bucket will be empty.")
        return []
    with open(p) as f:
        data = json.load(f)
    patterns = []
    # The structure is {"BIG PHARMA SUBSIDY": {"patterns": [...]}, ...} or list of dicts
    if isinstance(data, dict):
        for k, v in data.items():
            if isinstance(v, dict) and "patterns" in v:
                patterns.extend(v["patterns"])
    elif isinstance(data, list):
        for item in data:
            if isinstance(item, dict) and "patterns" in item:
                patterns.extend(item["patterns"])
            elif isinstance(item, str):
                patterns.append(item)
    # Dedup and compile simple contains patterns
    patterns = sorted(set(p.upper() for p in patterns if p))
    print(f"Loaded {len(patterns)} pharma patterns from {pharma_json}")
    return patterns

def is_church_name(name: str) -> bool:
    if not isinstance(name, str):
        return False
    n = name.upper()
    return any(kw in n for kw in CHURCH_KEYWORDS)

def is_pharma_name(name: str, patterns: list) -> bool:
    if not isinstance(name, str) or not patterns:
        return False
    n = name.upper()
    return any(p in n for p in patterns)

def make_seeds_for_category(category: str) -> list:
    """Small curated seeds for TUI pre-blessing when reviewing this bucket's Splink output."""
    if category == "churches":
        return [
            "CATHOLIC CHARITIES",
            "LUTHERAN SERVICES",
            "BAPTIST HEALTH",
            "METHODIST HOSPITAL",
            "PRESBYTERIAN CHURCH",
            "EPISCOPAL CHURCH",
            "SALVATION ARMY",
            "SEVENTH DAY ADVENTIST",
            "CHURCH OF JESUS CHRIST OF LATTER-DAY SAINTS",
        ]
    if category == "pharma":
        return [
            "PFIZER", "MODERNA", "JOHNSON & JOHNSON", "MERCK", "NOVARTIS",
            "BRISTOL MYERS", "ABBOTT", "ELI LILLY", "GILEAD", "AMGEN",
        ]
    return []

def main():
    parser = argparse.ArgumentParser(
        description="Split master distinct names into category buckets for targeted Splink runs."
    )
    parser.add_argument("--input", default="distinct_grantee_names_clean.tsv",
                        help="Path to the clean distinct names TSV (or fuller version)")
    parser.add_argument("--categories", default="churches,pharma",
                        help="Comma-separated: churches,pharma")
    parser.add_argument("--out-dir", default="category_buckets",
                        help="Directory for output bucket TSVs and seeds files")
    parser.add_argument("--pharma-json", default="big_pharma_subsidy.json",
                        help="Source of pharma patterns (for no-EIN inference style)")
    parser.add_argument("--min-grants", type=int, default=1,
                        help="Minimum grant_count to include in a bucket (for noise reduction)")
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"Loading master list: {args.input}")
    df = pd.read_csv(args.input, sep="\t")
    print(f"Loaded {len(df):,} rows")

    # Normalize name column (consistent with miner + TUI)
    if "grantee_name" in df.columns:
        name_col = "grantee_name"
    else:
        name_col = df.columns[0]
    df["_norm_name"] = df[name_col].astype(str).str.upper()

    # Optional light filter
    if "grant_count" in df.columns:
        before = len(df)
        df = df[df["grant_count"] >= args.min_grants]
        print(f"After min-grants filter: {len(df):,} (dropped {before - len(df)})")

    pharma_patterns = []
    cats = [c.strip().lower() for c in args.categories.split(",") if c.strip()]

    for cat in cats:
        if cat == "churches":
            mask = df["_norm_name"].apply(is_church_name)
            bucket = df[mask].copy()
            print(f"Churches bucket: {len(bucket):,} rows")
            bucket_file = out_dir / "churches.tsv"
            bucket.drop(columns=["_norm_name"], errors="ignore").to_csv(
                bucket_file, sep="\t", index=False
            )
            print(f"  Wrote {bucket_file}")

            seeds = make_seeds_for_category("churches")
            seeds_file = out_dir / "churches_seeds.json"
            with open(seeds_file, "w") as f:
                json.dump({"canonicals": seeds}, f, indent=2)
            print(f"  Wrote seeds for TUI pre-bless: {seeds_file} ({len(seeds)} entries)")

        elif cat == "pharma":
            pharma_patterns = load_pharma_patterns(args.pharma_json)
            mask = df["_norm_name"].apply(lambda n: is_pharma_name(n, pharma_patterns))
            bucket = df[mask].copy()
            print(f"Pharma bucket (matching big_pharma patterns, suitable for no-EIN Splink exploration): {len(bucket):,} rows")
            bucket_file = out_dir / "pharma.tsv"
            bucket.drop(columns=["_norm_name"], errors="ignore").to_csv(
                bucket_file, sep="\t", index=False
            )
            print(f"  Wrote {bucket_file}")

            seeds = make_seeds_for_category("pharma")
            seeds_file = out_dir / "pharma_seeds.json"
            with open(seeds_file, "w") as f:
                json.dump({"canonicals": seeds}, f, indent=2)
            print(f"  Wrote seeds for TUI pre-bless: {seeds_file}")

        else:
            print(f"Unknown category: {cat}")

    print("\n=== Ready to dive into Splink (as requested) ===")
    print("Example commands:")
    for cat in cats:
        tsv = out_dir / f"{cat}.tsv"
        if tsv.exists():
            print(f"  python splink_pattern_miner.py --use-clean-tsv {tsv} \\")
            print(f"      --output suggestions_{cat}.json --max-suggestions 200")
            print(f"  # Then review:")
            print(f"  python review_suggestions_tui.py --suggestions suggestions_{cat}.json \\")
            print(f"      --priority-canonicals {out_dir / f'{cat}_seeds.json'} \\")
            print(f"      --clean-names-tsv {args.input}")

    print("\nTip for pharma no-EIN focus: The pharma bucket above contains names matching the")
    print("subsidy/privacy patterns. Run Splink on it to discover what common structures it finds")
    print("(exactly as you described). If you have a grants-level file with EIN flags, you can")
    print("further pre-filter the input TSV before running this splitter.")

if __name__ == "__main__":
    main()
