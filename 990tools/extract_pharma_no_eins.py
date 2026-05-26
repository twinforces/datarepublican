#!/usr/bin/env python3
"""
extract_pharma_no_eins.py

Takes the full (unfiltered) distinct_grantee_names.tsv and produces
a pharma_no_eins.tsv containing only rows that:
  - Have no recipient_ein (NaN / missing / empty)
  - Match the big pharma subsidy/privacy patterns (the same ones used
    for the original no-EIN inference)

This gives you a clean, repeatable input for:
    python splink_pattern_miner.py --use-clean-tsv pharma_no_eins.tsv ...

Exactly what you asked for to "run Splink on those [no-EIN pharma] to see
what common patterns it spits out".
"""
import argparse
import json
import re
from pathlib import Path

import pandas as pd

DEFAULT_INPUT = "distinct_grantee_names.tsv"
DEFAULT_OUTPUT = "pharma_no_eins.tsv"
BIG_PHARMA_JSON = "big_pharma_subsidy.json"


def load_pharma_patterns(path: str = BIG_PHARMA_JSON) -> list[str]:
    """Load the patterns exactly as used in the original pharma siding."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"{path} not found")

    with open(p, encoding="utf-8") as f:
        data = json.load(f)

    patterns: list[str] = []
    if isinstance(data, dict):
        for _canon, info in data.items():
            if isinstance(info, dict):
                patterns.extend(info.get("patterns", []))
    elif isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                patterns.extend(item.get("patterns", []))
            elif isinstance(item, str):
                patterns.append(item)

    # Normalize: uppercase for matching
    patterns = sorted({p.upper() for p in patterns if p})
    print(f"Loaded {len(patterns)} pharma patterns from {path}")
    return patterns


def name_matches_pharma(name: str, patterns: list[str]) -> bool:
    if not isinstance(name, str):
        return False
    n = name.upper()
    return any(p in n for p in patterns)


def main():
    parser = argparse.ArgumentParser(
        description="Extract no-EIN pharma/subsidy rows from the full distinct names TSV"
    )
    parser.add_argument("--input", default=DEFAULT_INPUT,
                        help="Path to the full (unfiltered) distinct_grantee_names.tsv")
    parser.add_argument("--output", default=DEFAULT_OUTPUT,
                        help="Output TSV for Splink --use-clean-tsv")
    parser.add_argument("--pharma-json", default=BIG_PHARMA_JSON)
    parser.add_argument("--include-ein-column", action="store_true",
                        help="Keep recipient_ein column in the output (default: drop it)")
    args = parser.parse_args()

    print(f"Loading full distinct names: {args.input}")
    # Let pandas follow the symlink
    df = pd.read_csv(args.input, sep="\t")
    print(f"Loaded {len(df):,} rows")

    # Identify the EIN column (we saw "recipient_ein" in the full TSV)
    ein_col = None
    for candidate in ["recipient_ein", "ein", "grant_ein", "filer_ein"]:
        if candidate in df.columns:
            ein_col = candidate
            break
    if ein_col is None:
        raise ValueError("Could not find an EIN column in the input TSV")

    print(f"Using EIN column: {ein_col}")

    # No EIN filter
    no_ein_mask = df[ein_col].isna() | (df[ein_col].astype(str).str.strip() == "")
    print(f"Rows with no recipient_ein: {no_ein_mask.sum():,}")

    # Load patterns
    patterns = load_pharma_patterns(args.pharma_json)

    # Pharma name match
    pharma_mask = df["grantee_name"].apply(lambda n: name_matches_pharma(n, patterns))
    print(f"Rows matching pharma patterns: {pharma_mask.sum():,}")

    # Combined filter
    final_mask = no_ein_mask & pharma_mask
    result = df[final_mask].copy()
    print(f"Final no-EIN + pharma matches: {len(result):,}")

    # Prepare output columns
    # The Splink clean loader mainly needs grantee_name, grant_count, dollars
    # We keep the original columns by default for maximum flexibility
    cols_to_drop = [] if args.include_ein_column else [ein_col]
    out_df = result.drop(columns=cols_to_drop, errors="ignore")

    # Make sure we have a clean "grantee_name" column for the miner
    if "grantee_name" not in out_df.columns and "name" in out_df.columns:
        out_df = out_df.rename(columns={"name": "grantee_name"})

    out_path = Path(args.output)
    out_df.to_csv(out_path, sep="\t", index=False)
    print(f"\nWrote {out_path} with {len(out_df):,} rows")

    # Also emit a tiny "three-column" version that is guaranteed to work with the miner
    three_col = out_df[["grantee_name", "grant_count", "dollars"]].copy() \
        if {"grantee_name", "grant_count", "dollars"}.issubset(out_df.columns) else None
    if three_col is not None:
        three_path = out_path.with_name(out_path.stem + "_3col.tsv")
        three_col.to_csv(three_path, sep="\t", index=False)
        print(f"Also wrote minimal version for the miner: {three_path}")

    print("\nNext (dive into Splink):")
    print(f"  python splink_pattern_miner.py --use-clean-tsv {out_path} \\")
    print("      --output suggestions_pharma_no_ein.json --max-suggestions 300")


if __name__ == "__main__":
    main()
