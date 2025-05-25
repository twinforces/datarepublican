import os
import pandas as pd
import numpy as np
import glob
import argparse
import logging
from collections import defaultdict
import psutil
from tqdm import tqdm
import re
from mako.template import Template

# Precompile regex for sanitizing Markdown
SANITIZE_RE = re.compile(r'[|`\n\r]')

# Setup logging
logging.basicConfig(
    filename='analyze_error_log.txt',
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - Line %(lineno)d - File %(filename)s - %(message)s'
)

def log_info(message, verbose=True):
    logging.info(message)
    if verbose:
        print(message)

def log_error(message, exc_info=False):
    logging.error(message, exc_info=exc_info)
    print(f"Error: {message}")

def calculate_percentiles(data, column):
    values = data[column].dropna()
    if len(values) < 10:
        log_error(f"Insufficient data for {column}: {len(values)} rows")
        return np.array([]), np.array([])
    
    unique_vals = np.unique(values)
    log_error(f"Unique values for {column}: {len(unique_vals)} (min={values.min()}, max={values.max()})")
    
    if values.var() < 1e-2 or len(unique_vals) <= 2:
        log_error(f"Low variance or few unique values for {column}: var={values.var()}, unique={len(unique_vals)}")
        min_val, max_val = values.min(), values.max()
        if min_val == max_val:
            return np.array([]), np.array([])
        bins = np.linspace(min_val, max_val, min(11, len(unique_vals) + 1))
    else:
        bins = np.percentile(values, np.arange(0, 101, 10))
        bins = np.unique(bins)
        if len(bins) < 3:
            log_error(f"Few percentile bins for {column}: {len(bins)}")
            min_val, max_val = values.min(), values.max()
            bins = np.linspace(min_val, max_val, 11)
    
    try:
        hist, bin_edges = np.histogram(values, bins=bins, density=False)
        log_error(f"Histogram for {column}: bins={hist.tolist()}, edges={bin_edges.tolist()}")
        return hist, bin_edges
    except Exception as e:
        log_error(f"Error calculating percentiles for {column}: {e}", exc_info=True)
        return np.array([]), np.array([])

def assign_percentiles(data, column, bin_edges):
    if len(bin_edges) < 2:
        return pd.Series('n/y', index=data.index)
    try:
        bin_edges = np.sort(np.unique(bin_edges))
        labels = np.arange(0, 100, 100 / len(bin_edges[1:]))
        percentiles = pd.cut(data[column], bins=bin_edges, labels=labels, include_lowest=True)
        return percentiles.astype(str).replace('nan', 'n/y')
    except Exception as e:
        log_error(f"Error assigning percentiles for {column}: {e}", exc_info=True)
        return pd.Series('n/y', index=data.index)

def generate_histogram_report(data, year, org_type):
    metrics = {}
    columns = ['comp_pct', 'travel_pct', 'conferences_pct', 'grants_pct', 'foreign_expenses_pct']
    
    for col in columns:
        hist, bin_edges = calculate_percentiles(data, col)
        valid_rows = len(data[data[col].notna()])
        top_rows = data.nlargest(2, col, keep='first')[['tax_year', 'filer_ein', 'filer_name', col]]
        bottom_rows = data.nsmallest(2, col, keep='first')[['tax_year', 'filer_ein', 'filer_name', col]]
        
        metrics[col] = {
            'bins': hist,
            'bin_edges': bin_edges,
            'valid_rows': valid_rows,
            'top_rows': top_rows,
            'bottom_rows': bottom_rows
        }
    
    try:
        template = Template(filename='histogram_template.mako')
        return template.render(year=year, org_type=org_type, metrics=metrics)
    except FileNotFoundError as e:
        log_error(f"Histogram template not found: {e}", exc_info=True)
        return f"## Histogram Report for {org_type} - {year}\n\nError: Histogram template not found.\n"

def generate_grift_report(data, year, org_type):
    high_grift = data[data['grift_ratio'] > 10][['tax_year', 'filer_ein', 'filer_name', 'grift_ratio']]
    high_grift['filer_name'] = high_grift['filer_name'].apply(
        lambda x: SANITIZE_RE.sub(' ', str(x)).strip() if pd.notnull(x) else ''
    )
    
    try:
        template = Template(filename='grift_report_template.mako')
        return template.render(year=year, org_type=org_type, total_orgs=len(high_grift), high_grift=high_grift)
    except FileNotFoundError as e:
        log_error(f"Grift report template not found: {e}", exc_info=True)
        return f"## Grift Report for {org_type} - {year}\n\nError: Grift report template not found.\n"

def process_tsv_files(start_year, end_year):
    years = range(int(start_year), int(end_year) + 1)
    search_eins = {'271414646', '520851555', '471203726', '464284638', '592965108', '486289145', '680005486'}
    found_eins = defaultdict(set)
    
    for year in years:
        tsv_files = glob.glob(f"charities_*_{year}.tsv")
        if not tsv_files:
            log_info(f"No TSV files found for year {year}", verbose=True)
            continue
        
        for tsv_file in tqdm(tsv_files, desc=f"Processing TSVs for {year}", unit="file"):
            org_type = tsv_file.split('_')[1]
            log_info(f"Processing {tsv_file}", verbose=True)
            
            try:
                chunks = []
                chunk_size = 10000
                total_rows = sum(1 for _ in open(tsv_file)) - 1
                with tqdm(total=total_rows, desc=f"Reading {tsv_file}", unit="rows") as pbar:
                    for chunk in pd.read_csv(tsv_file, sep='\t', low_memory=False, chunksize=chunk_size):
                        chunks.append(chunk)
                        pbar.update(len(chunk))
                
                data = pd.concat(chunks, ignore_index=True)
                if data.empty:
                    log_info(f"Empty TSV file: {tsv_file}", verbose=True)
                    continue
                
                log_info(f"Row count for {tsv_file}: {len(data)}", verbose=True)
                
                if len(data) < 10:
                    log_info(f"Skipping percentiles for {tsv_file} due to small size", verbose=False)
                    for col in ['comp_pct', 'travel_pct', 'conferences_pct', 'grants_pct', 'foreign_expenses_pct']:
                        data[f'{col}_ptile'] = 'n/y'
                else:
                    for col in ['comp_pct', 'travel_pct', 'conferences_pct', 'grants_pct', 'foreign_expenses_pct']:
                        hist, bin_edges = calculate_percentiles(data, col)
                        if len(bin_edges) > 1:
                            data[f'{col}_ptile'] = assign_percentiles(data, col, bin_edges)
                            top_rows = data.nlargest(5, col)
                            assignments = [{col: row[col], f'{col}_ptile': row[f'{col}_ptile']} for _, row in top_rows.iterrows()]
                            log_info(f"Non-zero percentile assignments for {col}: {assignments}", verbose=False)
                        else:
                            data[f'{col}_ptile'] = 'n/y'
                            log_info(f"Skipped percentile assignment for {col} due to insufficient data", verbose=False)
                
                output_tsv = tsv_file.replace('.tsv', '_analyzed.tsv')
                data.to_csv(output_tsv, sep='\t', index=False)
                log_info(f"Wrote final {output_tsv} with {len(data)} rows", verbose=True)
                
                histogram_report = generate_histogram_report(data, year, org_type)
                grift_report = generate_grift_report(data, year, org_type)
                
                with open(f'histogram_{year}.md', 'a') as f:
                    f.write(histogram_report + "\n")
                with open(f'grift_report_{year}.md', 'a') as f:
                    f.write(grift_report + "\n")
                
                found_eins[(org_type, year)].update(data['filer_ein'].astype(str))
                log_info(f"Processed {tsv_file}, memory usage: {psutil.virtual_memory().percent}%", verbose=True)
                log_info(f"Total orgs for {org_type}, year {year}: {len(data)}", verbose=True)
                log_info(f"Found EINs in {org_type, year {year}: {found_eins[(org_type, year)] & search_eins}", verbose=True)
            
            except Exception as e:
                log_error(f"Error processing {tsv_file}: {e}", exc_info=True)
                continue
    
    return found_eins

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Analyze charity TSV files and generate reports.")
    parser.add_argument("start_year", type=int, help="Start year for TSV files (e.g., 2016).")
    parser.add_argument("end_year", type=int, help="End year for TSV files (e.g., 2024).")
    parser.add_argument("--verbose", action="store_true", help="Enable verbose logging.")

    args = parser.parse_args()
    if args.start_year > args.end_year:
        raise argparse.ArgumentError(None, "Start year must be less than or equal to end year.")
    
    found_eins = process_tsv_files(args.start_year, args.end_year)
