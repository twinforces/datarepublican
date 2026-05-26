#!/usr/bin/env python3
"""
spllink_pattern_miner.py - Unsupervised pattern discovery for name canonicalization rules.

Updated for full DuckDB schema with polymorphic Addresses join.

Usage:
  python splink_pattern_miner.py \
      --duckdb /path/to/irs990.duckdb \
      --grants-table Grants \
      --output suggested_patterns.json \
      --sample 200000
"""

import argparse
import json
import re
from collections import Counter
from typing import Optional, Tuple

import duckdb
import pandas as pd

# Lightweight geo logic (consistent with generate_name_rules.py)
SEPARATORS = {"OF", "IN", "FOR", "THE", "AT", "BY", "WITH", "AND", "TO", "-", ":"}
GEO_WORDS = {"ALABAMA", "AL", "CALIFORNIA", "CA", "NEW YORK", "NY", "LOS ANGELES", "CHICAGO", "COUNTY", "PARISH", "DISTRICT", "METRO", "VALLEY", "UNITED KINGDOM", "UK", "CANADA"}
GEO_EXCEPTIONS = {"UNIVERSITY OF CALIFORNIA", "BOY SCOUTS OF AMERICA", "SALVATION ARMY OF THE UNITED STATES"}

# ---------------------------------------------------------------------------
# Blocking helpers - refined during 2026-05 analysis
# ---------------------------------------------------------------------------

NOISE = {
    "THE", "A", "AN", "OF", "AND", "FOR", "TO", "IN", "ON", "AT", "BY", "WITH",
    "INC", "INCORPORATED", "LLC", "LTD", "LIMITED", "CO", "COMPANY", "CORP",
    "FOUNDATION", "FUND", "TRUST", "ASSOCIATION", "SOCIETY", "GROUP", "ALLIANCE",
    "PARTNERSHIP", "NETWORK", "PROJECT", "PROGRAM"
}

WEAK_PREFIXES = {
    "ST", "SAINT", "STS", "SAINTS",
    "FIRST", "SECOND", "THIRD",
    "CONGREGATION", "CHURCH", "TEMPLE", "PARISH",
    "CITY", "TOWN", "VILLAGE", "COUNTY", "STATE", "TOWNSHIP",
    "UNIVERSITY", "COLLEGE", "ACADEMY", "INSTITUTE",
    "GREATER", "NORTH", "SOUTH", "EAST", "WEST", "CENTRAL",
    "FRIENDS"
}

def first_two_sig_words(name: str) -> str:
    """
    Returns the first two significant (non-noise) tokens.
    Special handling for common weak organizational/religious prefixes
    (ST, CITY OF, FIRST, CONGREGATION, etc.) so we don't create giant blocks
    on those prefixes.

    This is the primary name signal used for blocking.
    """
    if not isinstance(name, str):
        return ""
    words = re.findall(r"[A-Z0-9]+", name.upper())
    words = [w for w in words if w not in NOISE]

    if not words:
        return ""

    first = words[0]
    if first in WEAK_PREFIXES and len(words) >= 2:
        if len(words) >= 3:
            return " ".join(words[1:3])
        return words[1]

    if len(words) >= 2:
        return " ".join(words[:2])
    return words[0]


REDACTION_NOISE = {
    "SEE", "ATTACHED", "ATTACHMENT", "ATTACH", "STATEMENT", "SCHEDULE",
    "PATIENT", "PATIENTS", "ELIGIBLE", "VARIOUS", "NEEDY", "PROGRAM",
    "PROGRAMS", "LIST", "REPORT", "DISTRIBUTIONS", "CONFIDENTIAL",
    "HIPAA", "HIPPA", "HEALTH", "INSURANCE", "PORTABILITY", "ACCOUNT",
    "GIFT", "GIFTS", "KIND", "THROUGH", "OPERATION", "SHARING"
}


def redaction_sig(name: str) -> str:
    """
    Redaction-aware signature for blocking on no-EIN subsidy/privacy files.

    Strips the very common redaction boilerplate so that variants like
    "SEE ATTACHED LIST", "See Attached Statement", "SEE ATTACHMENT C" etc.
    can block together more effectively than the normal first_two_sig_words.
    """
    if not isinstance(name, str):
        return ""
    words = re.findall(r"[A-Z0-9]+", name.upper())
    # Remove both general noise and redaction-specific noise
    words = [w for w in words if w not in NOISE and w not in REDACTION_NOISE]

    if not words:
        return "REDACTION"

    if len(words) >= 2:
        return " ".join(words[:2])
    return words[0]


def detect_geo_suffix(name: str) -> Optional[str]:
    if not name:
        return None
    words = name.upper().split()
    if len(words) < 3:
        return None
    for i in range(len(words) - 1, 1, -1):
        if words[i - 1] in SEPARATORS:
            geo_phrase = " ".join(words[i:])
            if geo_phrase in GEO_WORDS or any(w in GEO_WORDS for w in words[i:]):
                intro = " ".join(words[: i - 1])
                if intro and intro not in GEO_EXCEPTIONS:
                    return intro.strip()
    return None

def build_splink_settings(with_colocator: bool):
    """Return Splink 4 compatible settings dict + blocking rules."""
    from splink import block_on

    # Splink 4 uses class-based comparisons (ExactMatch, JaroWinklerAtThresholds)
    ComparisonLib = None
    for mod in ["splink.comparison_library", "splink.internals.comparison_library"]:
        try:
            ComparisonLib = __import__(mod, fromlist=["ExactMatch", "JaroWinklerAtThresholds"])
            break
        except ImportError:
            continue

    if ComparisonLib is None:
        raise ImportError("Could not find comparison_library in Splink 4.")

    ExactMatch = ComparisonLib.ExactMatch
    JaroWinklerAtThresholds = ComparisonLib.JaroWinklerAtThresholds

    comparisons = [
        ExactMatch("name"),
        JaroWinklerAtThresholds("name", [0.9, 0.8]),
    ]
    comparisons.append({
        "output_column_name": "geo_collapse",
        "comparison_levels": [
            {"sql_condition": "geo_collapse = 1", "label_for_charts": "Geo collapse match"},
            {"sql_condition": "geo_collapse = 0", "label_for_charts": "No geo collapse"},
        ],
    })

    blocking_rules = []
    if with_colocator:
        blocking_rules.append(block_on("loose_colocator"))
        blocking_rules.append(block_on("name", "loose_colocator"))
    else:
        blocking_rules.append("ltrim(substr(name,1,1)) || '_' || length(name) BETWEEN 5 AND 12")
        blocking_rules.append(block_on("name"))

    return {
        "blocking_rules_to_generate_predictions": blocking_rules,
        "comparisons": comparisons,
    }

def prepare_data(con: duckdb.DuckDBPyConnection, grants_table: str, sample: Optional[int] = None) -> Tuple[pd.DataFrame, bool]:
    """
    Build clean DataFrame for Splink name canonicalization / dedupe.

    Pulls both tight colocator (same building) and loose_colocator (0.5° same town)
    directly from the Grants table (populated by geolocate1 or later steps).
    Also joins Addresses for zip_code + canonical_address as extra blocking features.
    """
    sql = f"""
        SELECT 
            g.grant_id,
            UPPER(g.grantee_name) AS name,
            g.colocator,
            g.loose_colocator,
            a.canonical_address,
            a.zip_code,
            CASE WHEN g.colocator IS NOT NULL OR g.loose_colocator IS NOT NULL THEN 1 ELSE 0 END AS has_colocator
        FROM {grants_table} g
        LEFT JOIN Addresses a 
            ON a.owner_id = g.grant_id 
           AND a.address_type = 'grant'
        WHERE g.grantee_name IS NOT NULL
    """
    if sample:
        sql += f" USING SAMPLE {sample} ROWS"
    df = con.execute(sql).df()

    # Compute geo_collapse in Python
    df["geo_collapse"] = df["name"].apply(lambda x: 1 if detect_geo_suffix(x) else 0)

    # Primary name signal for blocking: first two significant words
    # (with weak prefix handling for ST, CITY OF, FIRST, etc.)
    df["sig_name"] = df["name"].apply(first_two_sig_words)

    # Redaction-aware signal (useful for no-EIN subsidy/privacy buckets)
    df["redaction_sig"] = df["name"].apply(redaction_sig)

    # ZIP blocking feature (very strong when combined with loose_colocator)
    if "zip_code" in df.columns and df["zip_code"].notna().any():
        df["zip_block"] = df["zip_code"].astype(str)
        print("Using ZIP code from Addresses table as blocking feature")
    else:
        df["zip_block"] = None

    # Decide whether we have real colocator signal for blocking
    loose_non_null = df["loose_colocator"].notna().sum() if "loose_colocator" in df.columns else 0
    tight_non_null = df["colocator"].notna().sum() if "colocator" in df.columns else 0

    if loose_non_null > 1000 or tight_non_null > 1000:
        with_colocator = True
        print(f"Real colocators available (loose={loose_non_null:,}, tight={tight_non_null:,}) — enabling colocator blocking")
    else:
        with_colocator = False
        print("Insufficient real colocator data — falling back to synthetic/weak blocking keys")
        if "loose_colocator" not in df.columns or df["loose_colocator"].isna().all():
            df["loose_colocator"] = df["name"].str[:1] + "_" + df["name"].str.len().astype(str)

    print(f"Prepared {len(df):,} records (with_colocator={with_colocator})")
    return df, with_colocator


def prepare_clean_tsv_data(path: str, sample: Optional[int] = None) -> Tuple[pd.DataFrame, bool]:
    """
    Load the clean distinct grantee names TSV directly for large-scale blocking tests.
    This bypasses the full DuckDB schema and is useful for testing how far
    first_two_sig_words blocking can scale.
    """
    print(f"Loading clean TSV for blocking test: {path}")
    df = pd.read_csv(path, sep="\t")

    # Normalize column names we care about
    if "grantee_name" in df.columns:
        df["name"] = df["grantee_name"].astype(str).str.upper()
    else:
        df["name"] = df.iloc[:, 0].astype(str).str.upper()

    # Synthetic unique ID for Splink
    df["grant_id"] = range(len(df))

    # Compute our primary blocking signal
    df["sig_name"] = df["name"].apply(first_two_sig_words)

    # Redaction-aware signal (for no-EIN redaction files)
    df["redaction_sig"] = df["name"].apply(redaction_sig)

    # Compute geo_collapse (same logic as the DuckDB path)
    df["geo_collapse"] = df["name"].apply(lambda x: 1 if detect_geo_suffix(x) else 0)

    if sample and len(df) > sample:
        df = df.sample(n=sample, random_state=42).reset_index(drop=True)
        print(f"Sampled down to {len(df):,} rows")

    print(f"Prepared {len(df):,} clean records (with_colocator=False)")
    return df, False


def run_splink_discovery(df: pd.DataFrame, with_colocator: bool, blocking_strategy: str = "sig_name") -> pd.DataFrame:
    """Correct Splink 4.0+ implementation (for 4.0.16)."""
    from splink import Linker, DuckDBAPI, block_on

    # DuckDB memory tuning (important for large runs)
    import duckdb
    con = duckdb.connect()
    con.execute("PRAGMA max_temp_directory_size='30GiB'")
    con.execute("SET threads=2")
    con.execute("SET preserve_insertion_order=false")
    con.close()

    settings_dict = build_splink_settings(with_colocator)

    # Blocking rule selection — now supports different strategies for special buckets
    # (especially the no-EIN redaction/subsidy files).
    if blocking_strategy == "redaction" and "redaction_sig" in df.columns:
        print("Using redaction_sig blocking (optimized for no-EIN redaction files)")
        blocking_rules = ["l.redaction_sig = r.redaction_sig"]
    elif blocking_strategy == "loose":
        print("Using loose first-letter blocking (very permissive)")
        blocking_rules = ["substr(l.name, 1, 1) = substr(r.name, 1, 1)"]
    elif blocking_strategy == "none":
        print("Using extremely loose blocking (length bucket) — suitable only for small files")
        blocking_rules = ["length(l.name) % 5 = length(r.name) % 5"]
    elif with_colocator and "loose_colocator" in df.columns:
        print("Using loose_colocator + first_two_sig_words for blocking (preferred path)")
        blocking_rules = [
            "l.loose_colocator = r.loose_colocator AND l.sig_name = r.sig_name",
            "l.colocator = r.colocator AND l.sig_name = r.sig_name",
        ]
    elif "zip_block" in df.columns and df["zip_block"].notna().any():
        print("Using ZIP + first_two_sig_words blocking")
        blocking_rules = ["l.zip_block = r.zip_block AND l.sig_name = r.sig_name"]
    else:
        # Default / sig_name strategy
        print("Falling back to first_two_sig_words only (no geo signal)")
        blocking_rules = ["l.sig_name = r.sig_name"]

    # Add null level for geo_collapse (required in Splink 4)
    geo_comparison = {
        "output_column_name": "geo_collapse",
        "comparison_levels": [
            {"sql_condition": "geo_collapse_l IS NULL OR geo_collapse_r IS NULL", "is_null_level": True},
            {"sql_condition": "geo_collapse_l = 1 AND geo_collapse_r = 1", "label_for_charts": "Both geo collapse"},
            {"sql_condition": "geo_collapse_l = 0 AND geo_collapse_r = 0", "label_for_charts": "Neither geo collapse"},
        ],
    }

    from splink.comparison_library import ExactMatch, JaroWinklerAtThresholds

    # Define a clean set of comparisons.
    # We compare on the original name (for quality) + the sig_name we block on.
    # This avoids duplicate/conflicting definitions that were causing
    # "No comparison level with comparison vector value X" errors.
    comparisons = [
        JaroWinklerAtThresholds("name", [0.9, 0.8]),
        ExactMatch("sig_name"),
        geo_comparison,
    ]

    settings = {
        "link_type": "dedupe_only",
        "unique_id_column_name": "grant_id",
        "blocking_rules_to_generate_predictions": blocking_rules,
        "comparisons": comparisons,
    }

    db_api = DuckDBAPI()
    linker = Linker(df, settings, db_api=db_api)

    # Splink 4 training API (fixed argument name)
    linker.training.estimate_u_using_random_sampling(max_pairs=5_000_000)
    linker.training.estimate_parameters_using_expectation_maximisation(
        blocking_rule=blocking_rules[0]
    )

    # Locked to Splink 4.0.16 working method
    df_predict = linker.inference.predict(threshold_match_probability=0.85)

    # Splink 4 clustering (under clustering namespace)
    clusters = linker.clustering.cluster_pairwise_predictions_at_threshold(df_predict, threshold_match_probability=0.85)
    cluster_df = clusters.as_pandas_dataframe()
    print(f"Splink found {cluster_df['cluster_id'].nunique():,} clusters")
    return cluster_df

def extract_high_value_patterns(cluster_df: pd.DataFrame, global_name_freq: Counter, max_suggestions: int = 300) -> list:
    """New mode: Suggest the most representative full name per cluster (much cleaner)."""
    suggestions = []
    cluster_names = cluster_df.groupby("cluster_id")["name"].apply(list).to_dict()

    for cid, names in cluster_names.items():
        if len(names) < 3:
            continue

        # Use original names (duplicates are fine for most_common)
        if len(names) < 3:
            continue

        # Most frequent full name in the cluster (best canonical candidate)
        from collections import Counter as NameCounter
        name_counts = NameCounter(names)
        most_common_name, count = name_counts.most_common(1)[0]

        # Always suggest the most common name per cluster (let reviewer decide)
        support = count / len(names)
        suggestions.append({
            "type": "canonical_name",
            "pattern": most_common_name,
            "cluster_id": int(cid),
            "cluster_size": len(names),
            "support": round(support, 2),
            "example_variants": list(dict.fromkeys(names))[:4],
        })

        # Still keep geo-collapse suggestions (they're high value)
        geo_roots = {detect_geo_suffix(n) for n in names if detect_geo_suffix(n)}
        for root in geo_roots:
            suggestions.append({
                "type": "geo_suffix",
                "pattern": root,
                "cluster_id": int(cid),
                "cluster_size": len(names),
                "lift": 99.0,
                "example_variants": [n for n in names if detect_geo_suffix(n)][:3],
            })

    # Sort by cluster size (bigger = more important) then support
    suggestions.sort(key=lambda x: (-x.get("cluster_size", 0), -x.get("support", 0)))

    # Deduplicate by pattern
    seen = set()
    unique = []
    for s in suggestions:
        if s["pattern"] not in seen:
            seen.add(s["pattern"])
            unique.append(s)
    return unique[:max_suggestions]

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--duckdb", default=None, help="Path to DuckDB (required unless --use-clean-tsv is used)")
    parser.add_argument("--grants-table", default="Grants")
    parser.add_argument("--output", default="suggested_patterns.json")
    parser.add_argument("--sample", type=int, default=None)
    parser.add_argument("--min-cluster-size", type=int, default=5, help="Minimum cluster size to generate a suggestion")
    parser.add_argument("--max-suggestions", type=int, default=300, help="Maximum number of suggestions to output")
    parser.add_argument("--dashboard", action="store_true", help="Launch Splink Cluster Studio dashboard after clustering")
    parser.add_argument("--use-clean-tsv", type=str, default=None,
                        help="Path to distinct_grantee_names_clean.tsv for large-scale blocking tests (bypasses full DB)")
    parser.add_argument("--blocking-strategy", choices=["sig_name", "redaction", "loose", "none"],
                        default="sig_name",
                        help="Blocking strategy to use. 'redaction' is useful for no-EIN redaction/subsidy files "
                             "(e.g. pharma_no_eins.tsv) where normal sig_name blocking fragments common redaction phrases. "
                             "'loose' or 'none' further relax blocking (suitable for small buckets ~5k rows).")
    args = parser.parse_args()

    print("=== Splink Pattern Miner (schema-aware) ===")

    if args.use_clean_tsv:
        print(f"Using clean TSV mode for large-scale testing")
        df, with_colocator = prepare_clean_tsv_data(args.use_clean_tsv, args.sample)
        con = None  # no DuckDB connection in clean TSV mode
    else:
        if not args.duckdb:
            parser.error("--duckdb is required unless --use-clean-tsv is provided")
        con = duckdb.connect(args.duckdb)
        df, with_colocator = prepare_data(con, args.grants_table, args.sample)

    global_name_freq = Counter(df["name"].tolist())
    cluster_df = run_splink_discovery(df, with_colocator, args.blocking_strategy)
    suggestions = extract_high_value_patterns(cluster_df, global_name_freq, args.max_suggestions)

    report = {
        "metadata": {
            "total_records": len(df),
            "clusters_found": cluster_df["cluster_id"].nunique(),
            "with_colocator": with_colocator,
            "sample_size": args.sample,
            "source": "clean_tsv" if args.use_clean_tsv else "duckdb",
            "blocking_strategy": args.blocking_strategy,
            "loose_colocator_non_null": int(df["loose_colocator"].notna().sum()) if "loose_colocator" in df.columns else 0,
            "colocator_non_null": int(df["colocator"].notna().sum()) if "colocator" in df.columns else 0,
        },
        "suggestions": suggestions,
    }

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)

    print(f"\nWrote {len(suggestions):,} suggestions to {args.output}")
    for s in suggestions[:5]:
        metric = s.get("lift", s.get("support", "?"))
        print(f"  {s['type']}: '{s['pattern']}' (metric={metric}, size={s['cluster_size']})")

    if args.dashboard:
        print("\nLaunching Splink Cluster Studio dashboard...")
        try:
            # Correct Splink 4 method
            linker.visualisations.cluster_studio_dashboard(
                df_predict,
                clusters,
                "cluster_studio.html"
            )
            print("Dashboard saved to cluster_studio.html")
        except Exception as e:
            print(f"Dashboard not available in this Splink version: {e}")

    if con:
        con.close()

if __name__ == "__main__":
    main()
