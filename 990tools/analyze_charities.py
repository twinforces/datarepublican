#!/usr/bin/env python3
import os
import glob
import subprocess
import argparse
import logging
import threading
import pandas as pd
import numpy as np
from tqdm import tqdm
from collections import defaultdict
from mako.template import Template
import gc

# Constants
TSV_COLUMNS = [
    "tax_year", "filer_ein", "filer_name", "receipt_amt", "govt_amt", "contrib_amt", "org_type",
    "total_exp", "prog_exp", "travel_amt", "conferences_amt", "officer_comp", "comp_pct", "comp_ptile",
    "travel_pct", "travel_ptile", "conferences_pct", "conferences_ptile", "grants_pct", "grants_ptile",
    "foreign_expenses_pct", "foreign_expenses_ptile", "grift_ratio", "total_assets", "form_type",
    "denominator", "foreign_office", "foreign_expenses", "grants_to_others", "domestic_misrep_flag", "xml_name"
]

PERCENTILE_COLS = {
    "comp_pct": "comp_ptile",
    "travel_pct": "travel_ptile",
    "conferences_pct": "conferences_ptile",
    "grants_pct": "grants_ptile",
    "foreign_expenses_pct": "foreign_expenses_ptile",
    "grift_ratio": None  # No corresponding ptile column
}

REPORT_COLS = [
    "grift_ratio", "grift", "denominator", "filer_ein", "filer_name", "tax_year", "total_exp", "officer_comp", 
    "comp_pct", "comp_ptile", "travel_amt", "travel_pct", "travel_ptile", "conferences_amt", "conferences_pct", 
    "conferences_ptile", "grants_to_others", "grants_pct", "grants_ptile", "foreign_expenses", 
    "foreign_expenses_pct", "foreign_expenses_ptile", "total_assets"
]

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('analyze_charities.log')
    ]
)
logger = logging.getLogger(__name__)

def get_file_line_count(file_path):
    try:
        result = subprocess.run(['wc', '-l', file_path], capture_output=True, text=True, check=True)
        return int(result.stdout.split()[0])
    except subprocess.CalledProcessError as e:
        logger.error(f"Error counting lines in {file_path}: {e}")
        return 0

def parse_float(value):
    if value in ("n/a", "", None, "nan"):
        return 0.0
    try:
        return float(value)
    except (ValueError, TypeError):
        return 0.0

def compute_percentiles(values, metric, exclude_zeros=False):
    """Compute percentiles and generate both standard histogram (20 bins) and percentile table (5% increments)."""
    if exclude_zeros:
        valid_values = [v for v in values if v > 0]
    else:
        valid_values = [v for v in values if v != 0]
    
    if len(valid_values) == 0:
        logger.warning(f"No valid values for {metric} computation")
        return np.array([np.nan] * len(values), dtype=np.float64), [], []
    if len(valid_values) == 1:
        logger.warning(f"Only one valid value for {metric}, skipping computation")
        return np.array([np.nan] * len(values), dtype=np.float64), [], []

    # Compute percentile ranks
    sorted_indices = np.argsort(valid_values)
    ranks = sorted_indices / (len(valid_values) - 1) * 100
    percentile_map = {val: rank for val, rank in zip(valid_values, ranks)}
    percentiles = np.array([percentile_map.get(v, np.nan) if (v > 0 if exclude_zeros else v != 0) else np.nan for v in values], dtype=np.float64)

    # Handle negative and >1000 (for grift_ratio) or >100 (for _pct metrics) values
    max_value = 1000.0 if metric == "grift_ratio" else 100.0
    negative_count = sum(1 for v in values if v < 0)
    over_max_count = sum(1 for v in values if v > max_value)
    valid_values = np.array([v for v in valid_values if 0 <= v <= max_value], dtype=np.float64)

    # Generate standard histogram (20 equal-width bins)
    bin_table = []
    if len(valid_values) > 0:
        bin_edges = np.linspace(0, max_value, 21)  # 20 bins from 0 to max_value
        counts, _ = np.histogram(valid_values, bins=bin_edges)
        total_valid = len(valid_values)
        cumulative_count = 0
        for i in range(20):
            count = counts[i]
            cumulative_count += count
            bin_values = valid_values[(valid_values >= bin_edges[i]) & (valid_values < bin_edges[i + 1])]
            if bin_values.size > 0:
                bin_table.append((f"{bin_edges[i]:.0f}-{bin_edges[i + 1]:.0f}", 
                                  (count, float(bin_values.min()), float(bin_values.max()), 
                                   cumulative_count / total_valid * 100)))
            else:
                bin_table.append((f"{bin_edges[i]:.0f}-{bin_edges[i + 1]:.0f}", 
                                  (0, None, None, cumulative_count / total_valid * 100)))
    
    # Add -0 and +max entries to bin table
    bin_table.insert(0, ('-0', (negative_count, None, None, 0.0)))
    bin_table.append((f'+{max_value:.0f}', (over_max_count, None, None, 100.0 if over_max_count > 0 else cumulative_count / total_valid * 100)))

    # Collapse zero-count bins
    collapsed_bin_table = []
    i = 0
    while i < len(bin_table):
        range_str, (count, min_val, max_val, cum_pct) = bin_table[i]
        if count == 0 and i < len(bin_table) - 1 and range_str not in ('-0', f'+{max_value:.0f}'):
            j = i + 1
            while j < len(bin_table) and bin_table[j][1][0] == 0 and bin_table[j][0] not in ('-0', f'+{max_value:.0f}'):
                j += 1
            if j < len(bin_table):
                next_range, (next_count, next_min, next_max, next_cum_pct) = bin_table[j]
                if next_count > 0:
                    collapsed_bin_table.append((f"{range_str}-{next_range}", 
                                               (next_count, next_min, next_max, next_cum_pct)))
                    i = j + 1
                    continue
        collapsed_bin_table.append((range_str, (count, min_val, max_val, cum_pct)))
        i += 1

    # Generate percentile table (0%, 5%, 10%, ..., 100%)
    percentile_table = []
    if len(valid_values) > 0:
        percentiles_range = np.arange(0, 101, 5)  # 0, 5, 10, ..., 100
        percentile_values = np.percentile(valid_values, percentiles_range)
        total_valid = len(valid_values)
        for i in range(len(percentiles_range) - 1):
            cum_pct = percentiles_range[i]
            next_cum_pct = percentiles_range[i + 1]
            lower_bound = percentile_values[i]
            upper_bound = percentile_values[i + 1]
            bin_values = valid_values[(valid_values >= lower_bound) & (valid_values < upper_bound if i < len(percentiles_range) - 2 else valid_values <= upper_bound)]
            count = len(bin_values)
            min_val = float(bin_values.min()) if bin_values.size > 0 else None
            max_val = float(bin_values.max()) if bin_values.size > 0 else None
            percentile_table.append((cum_pct, (float(lower_bound), count, min_val, max_val)))

    # Add -0 and +max entries to percentile table
    percentile_table.insert(0, ('-0', (None, negative_count, None, None)))
    percentile_table.append((f'+{max_value:.0f}', (None, over_max_count, None, None)))

    return percentiles, collapsed_bin_table, percentile_table

def clamp_value(value):
    if not isinstance(value, (int, float)) or np.isnan(value):
        return "0.00"
    if value < 0:
        return "-0"
    if value > 100:
        return "+100"
    return f"{value:.2f}"

def compute_percentage(numerator, denominator):
    if denominator == 0 or numerator == 0:
        return 0.0
    return (numerator / denominator) * 100

def process_file(file_path, output_dir, histograms, top_bottom, lock):
    line_count = get_file_line_count(file_path)
    if line_count < 101:
        logger.info(f"Skipping {file_path}: too small ({line_count} lines)")
        return

    logger.info(f"Processing {file_path} ({line_count} lines)")
    try:
        # Read filer_ein as string to preserve leading zeros
        df = pd.read_csv(file_path, sep='\t', low_memory=False, dtype={'filer_ein': str})
    except Exception as e:
        logger.error(f"Error reading {file_path}: {e}")
        return

    org_type = df['org_type'].iloc[0]
    tax_year = df['tax_year'].iloc[0]
    output_filename = f"charities_{org_type.replace('(', '').replace(')', '').replace(' ', '').lower()}_{tax_year}_analyzed.tsv"
    output_path = os.path.join(output_dir, output_filename)

    # Log available columns for debugging
    logger.debug(f"Columns in {file_path}: {list(df.columns)}")

    # Recompute percentages from raw amounts
    df['total_exp'] = df['total_exp'].apply(parse_float)
    df['prog_exp'] = df['prog_exp'].apply(parse_float)
    df['officer_comp'] = df['officer_comp'].apply(parse_float)
    df['travel_amt'] = df['travel_amt'].apply(parse_float)
    df['conferences_amt'] = df['conferences_amt'].apply(parse_float)
    df['grants_to_others'] = df['grants_to_others'].apply(parse_float)
    df['foreign_expenses'] = df['foreign_expenses'].apply(parse_float)
    df['denominator'] = df['denominator'].apply(parse_float)

    df['comp_pct'] = df.apply(lambda x: compute_percentage(x['officer_comp'], x['total_exp']), axis=1)
    df['travel_pct'] = df.apply(lambda x: compute_percentage(x['travel_amt'], x['total_exp']), axis=1)
    df['conferences_pct'] = df.apply(lambda x: compute_percentage(x['conferences_amt'], x['total_exp']), axis=1)
    df['grants_pct'] = df.apply(lambda x: compute_percentage(x['grants_to_others'], x['total_exp']), axis=1)
    df['foreign_expenses_pct'] = df.apply(lambda x: compute_percentage(x['foreign_expenses'], x['total_exp']), axis=1)
    df['grift'] = df['officer_comp'] + df['travel_amt'] + df['conferences_amt']
    df['grift_ratio'] = df.apply(
        lambda x: min(compute_percentage(x['grift'], 
                                         max(x['denominator'], 1000 if x['denominator'] > 0 else x['total_exp'] if x['total_exp'] > 0 else 1000)), 
                      1000.0),  # Cap at 1000%
        axis=1
    )

    # Log denominator statistics
    denom_stats = df['denominator'].describe()
    logger.info(f"Denominator stats for {file_path}: {denom_stats.to_string()}")
    problem_rows = df[df['denominator'] < 1000][['filer_ein', 'officer_comp', 'travel_amt', 'conferences_amt', 'grift', 'denominator', 'total_exp', 'grift_ratio']]
    if not problem_rows.empty:
        logger.warning(f"Low denominator values (< 1000) in {file_path}:\n{problem_rows.to_string()}")

    # Compute percentiles and tables
    metrics = {}
    for pct_col in tqdm(PERCENTILE_COLS.keys(), desc=f"Computing percentiles for {file_path}"):
        values = df[pct_col].values
        exclude_zeros = (pct_col == "foreign_expenses_pct")
        percentiles, bin_table, percentile_table = compute_percentiles(values, metric=pct_col, exclude_zeros=exclude_zeros)
        ptile_col = PERCENTILE_COLS[pct_col]
        if ptile_col:
            df[ptile_col] = [f"{x:.2f}" if not np.isnan(x) else "n/a" for x in percentiles]
        valid_rows = len([v for v in values if (v > 0 if exclude_zeros else v != 0)])

        valid_df = df[df[pct_col] > 0] if exclude_zeros else df[df[pct_col] != 0]
        available_cols = [col for col in REPORT_COLS if col in df.columns]
        top_rows = valid_df.sort_values(pct_col, ascending=False).head(100)[available_cols]
        bottom_rows = valid_df.sort_values(pct_col, ascending=True).head(10)[available_cols]

        metrics[pct_col] = {
            'bins': bin_table,
            'percentiles': percentile_table,
            'valid_rows': valid_rows,
            'top_rows': top_rows,
            'bottom_rows': bottom_rows
        }

    with lock:
        histograms[(tax_year, org_type)] = metrics

    with lock:
        for metric, data in metrics.items():
            top_bottom[(tax_year, org_type, metric)] = {
                'top': data['top_rows'],
                'bottom': data['bottom_rows']
            }

    # Clamp percentages for output
    df['comp_pct'] = df['comp_pct'].apply(clamp_value)
    df['travel_pct'] = df['travel_pct'].apply(clamp_value)
    df['conferences_pct'] = df['conferences_pct'].apply(clamp_value)
    df['grants_pct'] = df['grants_pct'].apply(clamp_value)
    df['foreign_expenses_pct'] = df['foreign_expenses_pct'].apply(clamp_value)
    df['grift_ratio'] = df['grift_ratio'].apply(clamp_value)

    # Ensure filer_ein is a 9-digit string with leading zeros
    df['filer_ein'] = df['filer_ein'].astype(str).str.zfill(9)

    try:
        df.to_csv(output_path, sep='\t', index=False)
        logger.info(f"Written output to {output_path}")
    except Exception as e:
        logger.error(f"Error writing {output_path}: {e}")

    del df
    gc.collect()
    
def generate_histogram_report(histograms, output_dir):
    template_path = "histogram_template.mako"
    try:
        with open(template_path, 'r') as f:
            template = Template(f.read())
    except Exception as e:
        logger.error(f"Error reading template {template_path}: {e}")
        return

    for (tax_year, org_type), metrics in sorted(histograms.items()):
        output_filename = f"histogram_{org_type.replace('(', '').replace(')', '').replace(' ', '').lower()}_{tax_year}.md"
        output_path = os.path.join(output_dir, output_filename)
        with open(output_path, 'w') as f:
            f.write(template.render(
                org_type=org_type,
                year=tax_year,
                metrics=metrics
            ))
        logger.info(f"Generated histogram report: {output_path}")

def generate_grift_report(top_bottom, output_dir):
    template_path = "grift_report_template.mako"
    try:
        with open(template_path, 'r') as f:
            template = Template(f.read())
    except Exception as e:
        logger.error(f"Error reading template {template_path}: {e}")
        return

    grouped_data = defaultdict(list)
    for (tax_year, org_type, metric), data in top_bottom.items():
        if metric != "grift_ratio":
            continue
        grouped_data[(tax_year, org_type)].append(data['top'])

    for (tax_year, org_type), high_grift_list in sorted(grouped_data.items()):
        high_grift = pd.concat(high_grift_list)
        # Limit to top 100, sorted by grift_ratio descending
        high_grift = high_grift.sort_values('grift_ratio', ascending=False).head(100)
        total_orgs = len(high_grift)
        output_filename = f"grift_{org_type.replace('(', '').replace(')', '').replace(' ', '').lower()}_{tax_year}.md"
        output_path = os.path.join(output_dir, output_filename)
        with open(output_path, 'w') as f:
            f.write(template.render(
                org_type=org_type,
                year=tax_year,
                total_orgs=total_orgs,
                high_grift=high_grift
            ))
        logger.info(f"Generated grift report: {output_path}")

def main():
    parser = argparse.ArgumentParser(description="Analyze charity TSV files, compute percentiles, and generate reports.")
    parser.add_argument('--input-dir', default='.', help='Directory containing input TSV files')
    parser.add_argument('--output-dir', default='analyzed', help='Directory for output TSV files and reports')
    parser.add_argument('--start-year', type=int, default=2016, help='Start year for processing')
    parser.add_argument('--stop-year', type=int, default=2024, help='Stop year for processing')
    args = parser.parse_args()

    os.makedirs(args.output_dir, exist_ok=True)
    histograms = {}
    top_bottom = {}
    lock = threading.Lock()
    threads = []

    tsv_files = []
    for year in range(args.start_year, args.stop_year + 1):
        tsv_files.extend(glob.glob(os.path.join(args.input_dir, f"charities_*_{year}.tsv")))
    logger.info(f"Found {len(tsv_files)} TSV files for years {args.start_year} to {args.stop_year}")

    for file_path in tsv_files:
        thread = threading.Thread(
            target=process_file,
            args=(file_path, args.output_dir, histograms, top_bottom, lock)
        )
        threads.append(thread)
        thread.start()

    for thread in tqdm(threads, desc="Waiting for threads"):
        thread.join()

    generate_histogram_report(histograms, args.output_dir)
    generate_grift_report(top_bottom, args.output_dir)

    logger.info("Analysis and report generation complete.")

if __name__ == "__main__":
    main()