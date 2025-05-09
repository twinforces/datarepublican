import csv
import argparse
import numpy as np
from tqdm import tqdm
import logging
from mako.template import Template
from mako.exceptions import MakoException
import pandas as pd
from collections import defaultdict
import os
import psutil
import gc
import glob
import re

logging.basicConfig(
    filename='error_log.txt',
    level=logging.ERROR,
    format='%(asctime)s - %(levelname)s - Line %(lineno)d - File %(filename)s - %(message)s'
)

def log_error(message, exc_info=False):
    global skip_log_count, add_log_count
    if "Skipping" in message and skip_log_count >= 5:
        return
    if "Adding" in message and add_log_count >= 5:
        return
    logging.error(message, exc_info=exc_info)
    if "Skipping" in message:
        skip_log_count += 1
    if "Adding" in message:
        add_log_count += 1

def assign_grift_rating(percentile):
    if pd.isna(percentile):
        return 'N/A'
    if percentile >= 80:
        return 'A'
    elif percentile >= 60:
        return 'B'
    elif percentile >= 40:
        return 'C'
    elif percentile >= 20:
        return 'D'
    else:
        return 'F'

ORG_TYPE_DESCRIPTIONS = {
    "501(c)(1)": "Corporations organized under an Act of Congress (e.g., Federal Credit Unions, Federal Reserve Banks). Serve public purposes like banking or housing.",
    "501(c)(2)": "Title-holding corporations for exempt organizations. Hold property and turn over income to a tax-exempt parent organization.",
    "501(c)(3)": "Charitable, religious, educational, scientific, literary organizations, or those testing for public safety, fostering amateur sports, or preventing cruelty to children/animals.",
    "501(c)(4)": "Civic leagues or social welfare organizations promoting community welfare (e.g., homeowners associations, volunteer fire companies).",
    "501(c)(5)": "Labor, agricultural, or horticultural organizations (e.g., unions, farm bureaus). Promote worker rights or agricultural interests.",
    "501(c)(6)": "Business leagues, chambers of commerce, or professional associations (e.g., American Solar Grazing Association). Improve business conditions.",
    "501(c)(7)": "Social or recreational clubs (e.g., country clubs, fraternities). Provide leisure activities for members.",
    "501(c)(8)": "Fraternal beneficiary societies operating under a lodge system, providing life, sickness, or other benefits (e.g., Elks).",
    "501(c)(9)": "Voluntary employees’ beneficiary associations providing life, sickness, or accident benefits to members (e.g., labor union benefits).",
    "501(c)(10)": "Domestic fraternal societies without insurance, devoting earnings to charitable, religious, or educational purposes (e.g., Masons).",
    "501(c)(11)": "Teachers’ retirement fund associations. Pay retirement benefits to local teachers/school employees.",
    "501(c)(12)": "Local benevolent life insurance associations, mutual irrigation, or telephone companies. Serve members’ needs.",
    "501(c)(13)": "Cemetery companies operated for members or charitable purposes.",
    "501(c)(14)": "State-chartered credit unions or mutual reserve funds. Provide financial services to members.",
    "501(c)(15)": "Mutual insurance companies or associations (small insurers). Provide insurance to members.",
    "501(c)(16)": "Cooperative organizations financing crop operations. Support agricultural activities.",
    "501(c)(17)": "Supplemental unemployment benefit trusts. Pay benefits to employees during layoffs.",
    "501(c)(18)": "Employee-funded pension trusts (pre-1959). Provide pension benefits.",
    "501(c)(19)": "Veterans’ organizations providing benefits or social activities for members.",
    "501(c)(20)": "Group legal services organizations (discontinued after 1992). Provided legal benefits.",
    "501(c)(21)": "Black lung benefit trusts. Fund benefits for coal miners.",
    "501(c)(22)": "Withdrawal liability payment funds. Manage employer pension liabilities.",
    "501(c)(23)": "Veterans’ organizations (pre-1880, benefits-focused). Similar to 501(c)(19) but older.",
    "501(c)(24)": "Trusts for ERISA Section 4049 plans (post-1980 groundwork for organizations with significant grants or foreign expenses).",
    "501(c)(25)": "Title-holding corporations with multiple parents. Hold property for exempt orgs.",
    "501(c)(26)": "State-sponsored high-risk health insurance organizations. Provide coverage to high-risk individuals.",
    "501(c)(27)": "State-sponsored workers’ compensation reinsurance organizations. Manage insurance risks.",
    "501(c)(28)": "National Railroad Retirement Investment Trust. Manage railroad retirement funds.",
    "501(c)(29)": "CO-OP health insurance issuers under ACA. Provide consumer-oriented health plans."
}

INTERNATIONAL_EINS = ["271414646", "520851555"]  # CHAI, Amnesty

def compute_type_percentiles(input_tsv, chunk_size=10000):
    if not os.path.exists(input_tsv):
        return None

    numeric_cols = {
        'receipt_amt': float, 'govt_amt': float, 'contrib_amt': float, 'total_exp': float,
        'prog_exp': float, 'travel_amt': float, 'conferences_amt': float, 'officer_comp': float,
        'comp_pct': float, 'travel_pct': float, 'conferences_pct': float, 'grants_pct': float,
        'foreign_exp_pct': float, 'grift_ratio': float, 'total_assets': float, 'denominator': float,
        'foreign_expenses': float, 'grants_to_others': float
    }

    total_rows = sum(1 for _ in open(input_tsv)) - 1  # Subtract header
    if total_rows < 10:
        return None

    comp_pcts = []
    travel_pcts = []
    conferences_pcts = []
    grants_pcts = []
    foreign_exp_pcts = []
    grift_ratios = []

    with tqdm(total=total_rows, desc=f"Reading {input_tsv} for percentiles") as pbar:
        for chunk in pd.read_csv(input_tsv, delimiter='\t', chunksize=chunk_size, low_memory=False, dtype=numeric_cols, na_values=['n/y', '']):
            for col in numeric_cols:
                chunk[col] = pd.to_numeric(chunk[col], errors='coerce').fillna(0)
            comp_pcts.extend(chunk['comp_pct'].values)
            travel_pcts.extend(chunk['travel_pct'].values)
            conferences_pcts.extend(chunk['conferences_pct'].values)
            grants_pcts.extend(chunk['grants_pct'].values)
            foreign_exp_pcts.extend(chunk['foreign_exp_pct'].values)
            grift_ratios.extend(chunk['grift_ratio'].values)
            pbar.update(len(chunk))

    comp_pcts = np.array(comp_pcts, dtype=np.float32)
    travel_pcts = np.array(travel_pcts, dtype=np.float32)
    conferences_pcts = np.array(conferences_pcts, dtype=np.float32)
    grants_pcts = np.array(grants_pcts, dtype=np.float32)
    foreign_exp_pcts = np.array(foreign_exp_pcts, dtype=np.float32)
    grift_ratios = np.array(grift_ratios, dtype=np.float32)
    return (comp_pcts, travel_pcts, conferences_pcts, grants_pcts, foreign_exp_pcts, grift_ratios, total_rows)

def analyze_type(year, org_type, input_tsv):
    output_tsv = input_tsv.replace('.tsv', '_analyzed.tsv')
    chunk_size = 10000
    numeric_cols = {
        'receipt_amt': float, 'govt_amt': float, 'contrib_amt': float, 'total_exp': float,
        'prog_exp': float, 'travel_amt': float, 'conferences_amt': float, 'officer_comp': float,
        'comp_pct': float, 'travel_pct': float, 'conferences_pct': float, 'grants_pct': float,
        'foreign_exp_pct': float, 'grift_ratio': float, 'total_assets': float, 'denominator': float,
        'foreign_expenses': float, 'grants_to_others': float
    }

    if not os.path.exists(input_tsv):
        print(f"Skipping {input_tsv}: TSV not found")
        return [], [], None

    type_stats = compute_type_percentiles(input_tsv, chunk_size)
    if type_stats is None:
        print(f"Skipping {input_tsv}: Too few rows")
        return [], [], None

    comp_pcts, travel_pcts, conferences_pcts, grants_pcts, foreign_exp_pcts, grift_ratios, total_rows = type_stats
    grift_candidates = []
    org_types_seen = set()
    org_type_data = {
        'org_type': org_type,
        'description': ORG_TYPE_DESCRIPTIONS.get(org_type, f'No description available for {org_type}.'),
        'count': total_rows,
        'metrics': []
    }

    chunks = []
    with tqdm(total=total_rows, desc=f"Assigning percentiles for {input_tsv}") as pbar:
        for chunk in pd.read_csv(input_tsv, delimiter='\t', chunksize=chunk_size, low_memory=False, dtype=numeric_cols, na_values=['n/y', '']):
            for col in numeric_cols:
                chunk[col] = pd.to_numeric(chunk[col], errors='coerce').fillna(0)
            chunk['comp_pct_percentile'] = chunk['comp_pct'].apply(
                lambda x: min(100, 100 * (1 - np.sum(comp_pcts < x) / total_rows)) if total_rows > 0 else np.nan
            ).round(2)
            chunk['travel_pct_percentile'] = chunk['travel_pct'].apply(
                lambda x: min(100, 100 * (1 - np.sum(travel_pcts < x) / total_rows)) if total_rows > 0 else np.nan
            ).round(2)
            chunk['conferences_pct_percentile'] = chunk['conferences_pct'].apply(
                lambda x: min(100, 100 * (1 - np.sum(conferences_pcts < x) / total_rows)) if total_rows > 0 else np.nan
            ).round(2)
            chunk['grants_pct_percentile'] = chunk['grants_pct'].apply(
                lambda x: min(100, 100 * (1 - np.sum(grants_pcts < x) / total_rows)) if total_rows > 0 else np.nan
            ).round(2)
            chunk['foreign_exp_pct_percentile'] = chunk['foreign_exp_pct'].apply(
                lambda x: min(100, 100 * (1 - np.sum(foreign_exp_pcts < x) / total_rows)) if total_rows > 0 else np.nan
            ).round(2)
            chunk['grift_ratio_percentile'] = chunk['grift_ratio'].apply(
                lambda x: min(100, 100 * (1 - np.sum(grift_ratios < x) / total_rows)) if total_rows > 0 else np.nan
            ).round(2)
            chunk['comp_pct_rating'] = chunk['comp_pct_percentile'].apply(assign_grift_rating)
            chunk['travel_pct_rating'] = chunk['travel_pct_percentile'].apply(assign_grift_rating)
            chunk['conferences_pct_rating'] = chunk['conferences_pct_percentile'].apply(assign_grift_rating)
            chunk['grants_pct_rating'] = chunk['grants_pct_percentile'].apply(assign_grift_rating)
            chunk['foreign_exp_pct_rating'] = chunk['foreign_exp_pct_percentile'].apply(assign_grift_rating)
            chunk['grift_ratio_rating'] = chunk['grift_ratio_percentile'].apply(assign_grift_rating)

            for _, row in chunk.iterrows():
                org_types_seen.add(row['org_type'])
                if (row['denominator'] > 100_000) or (row['filer_ein'] in INTERNATIONAL_EINS and row['grift_ratio'] < 10 and row['foreign_expenses'] > 0.1 * row['total_exp']):
                    log_error(f"Adding {row['org_type']} to grift_candidates: EIN {row['filer_ein']}, Name {row['filer_name']}, Denominator {row['denominator']}, Grift Ratio {row['grift_ratio']}, Foreign Expenses {row['foreign_expenses']}")
                    grift_candidates.append({
                        'filer_ein': row['filer_ein'],
                        'filer_name': row['filer_name'],
                        'org_type': row['org_type'],
                        'grift_ratio': round(row['grift_ratio'], 2),
                        'grift_rating': row['grift_ratio_rating'],
                        'grants_pct': round(row['grants_pct'], 2),
                        'grants_rating': row['grants_pct_rating'],
                        'foreign_exp_pct': round(row['foreign_exp_pct'], 2),
                        'foreign_exp_rating': row['foreign_exp_pct_rating'],
                        'grift_ratio_percentile': row['grift_ratio_percentile'] if not pd.isna(row['grift_ratio_percentile']) else np.nan,
                        'grants_pct_percentile': row['grants_pct_percentile'] if not pd.isna(row['grants_pct_percentile']) else np.nan,
                        'foreign_exp_pct_percentile': row['foreign_exp_pct_percentile'] if not pd.isna(row['foreign_exp_pct_percentile']) else np.nan,
                        'domestic_misrep_pct': round(row['grift_ratio'], 2) if row['domestic_misrep_flag'] else 0
                    })
                else:
                    log_error(f"Skipping {row['org_type']} for grift_candidates: EIN {row['filer_ein']}, Denominator {row['denominator']}, Grift Ratio {row['grift_ratio']}, Foreign Expenses {row['foreign_expenses']}")

            chunks.append(chunk)
            pbar.update(len(chunk))

    if chunks:
        df = pd.concat(chunks, ignore_index=True)
        df.to_csv(output_tsv, sep='\t', index=False)
        print(f"Wrote analyzed TSV to {output_tsv}")

    chunks = []
    with tqdm(total=total_rows, desc=f"Reading {input_tsv} for histograms") as pbar:
        for chunk in pd.read_csv(input_tsv, delimiter='\t', chunksize=chunk_size, low_memory=False, dtype=numeric_cols, na_values=['n/y', '']):
            for col in numeric_cols:
                chunk[col] = pd.to_numeric(chunk[col], errors='coerce').fillna(0)
            chunks.append(chunk)
            pbar.update(len(chunk))

    type_df = pd.concat(chunks, ignore_index=True) if chunks else pd.DataFrame()
    if len(type_df) < 10:
        return grift_candidates, org_types_seen, org_type_data

    histogram_chunk_size = 500
    for chunk_start in range(0, len(type_df), histogram_chunk_size):
        chunk_df = type_df.iloc[chunk_start:chunk_start + histogram_chunk_size]
        mem_usage = psutil.Process().memory_info().rss / 1024 / 1024
        print(f"Memory usage for {org_type} chunk {chunk_start}-{chunk_start + len(chunk_df)}: {mem_usage:.2f} MB")
        type_comp_pcts = chunk_df['comp_pct'].values
        if len(type_comp_pcts) >= 10 and np.var(type_comp_pcts) > 0:
            with tqdm(total=25, desc=f"Generating comp_pct histogram for {org_type}") as pbar_hist:
                comp_bins, bin_edges = np.histogram(type_comp_pcts, bins=25, range=(0, 100))
                cumulative_comp = np.cumsum(comp_bins)
                total_comp = sum(comp_bins)
                percentiles_comp = (cumulative_comp / total_comp * 100) if total_comp > 0 else np.zeros_like(cumulative_comp)
                data = []
                i = 0
                while i < len(comp_bins):
                    start = bin_edges[i]
                    count = comp_bins[i]
                    percentile = percentiles_comp[i]
                    end = bin_edges[i + 1] if i + 1 < len(bin_edges) else 100
                    if count == 0 and i < len(comp_bins) - 1:
                        j = i
                        while j < len(comp_bins) - 1 and comp_bins[j] == 0:
                            j += 1
                        if j > i:
                            end = bin_edges[j]
                            i = j
                        else:
                            i += 1
                    else:
                        i += 1
                    data.append((f"{start:.1f}", f"{end:.1f}", count, percentile))
                    pbar_hist.update(1)
            org_type_data['metrics'].append({'title': f"Officer Compensation Percentage (Tax Year {year})", 'data': data})

        type_travel_pcts = chunk_df['travel_pct'].values
        if len(type_travel_pcts) >= 10 and np.var(type_travel_pcts) > 0:
            with tqdm(total=25, desc=f"Generating travel_pct histogram for {org_type}") as pbar_hist:
                travel_bins, bin_edges = np.histogram(type_travel_pcts, bins=25, range=(0, 100))
                cumulative_travel = np.cumsum(travel_bins)
                total_travel = sum(travel_bins)
                percentiles_travel = (cumulative_travel / total_travel * 100) if total_travel > 0 else np.zeros_like(cumulative_travel)
                data = []
                i = 0
                while i < len(travel_bins):
                    start = bin_edges[i]
                    count = travel_bins[i]
                    percentile = percentiles_travel[i]
                    end = bin_edges[i + 1] if i + 1 < len(bin_edges) else 100
                    if count == 0 and i < len(travel_bins) - 1:
                        j = i
                        while j < len(travel_bins) - 1 and travel_bins[j] == 0:
                            j += 1
                        if j > i:
                            end = bin_edges[j]
                            i = j
                        else:
                            i += 1
                    else:
                        i += 1
                    data.append((f"{start:.1f}", f"{end:.1f}", count, percentile))
                    pbar_hist.update(1)
            org_type_data['metrics'].append({'title': f"Travel Percentage (Tax Year {year})", 'data': data})

        type_conferences_pcts = chunk_df['conferences_pct'].values
        if len(type_conferences_pcts) >= 10 and np.var(type_conferences_pcts) > 0:
            with tqdm(total=25, desc=f"Generating conferences_pct histogram for {org_type}") as pbar_hist:
                conferences_bins, bin_edges = np.histogram(type_conferences_pcts, bins=25, range=(0, 100))
                cumulative_conferences = np.cumsum(conferences_bins)
                total_conferences = sum(conferences_bins)
                percentiles_conferences = (cumulative_conferences / total_conferences * 100) if total_conferences > 0 else np.zeros_like(cumulative_conferences)
                data = []
                i = 0
                while i < len(conferences_bins):
                    start = bin_edges[i]
                    count = conferences_bins[i]
                    percentile = percentiles_conferences[i]
                    end = bin_edges[i + 1] if i + 1 < len(bin_edges) else 100
                    if count == 0 and i < len(conferences_bins) - 1:
                        j = i
                        while j < len(conferences_bins) - 1 and conferences_bins[j] == 0:
                            j += 1
                        if j > i:
                            end = bin_edges[j]
                            i = j
                        else:
                            i += 1
                    else:
                        i += 1
                    data.append((f"{start:.1f}", f"{end:.1f}", count, percentile))
                    pbar_hist.update(1)
            org_type_data['metrics'].append({'title': f"Conferences Percentage (Tax Year {year})", 'data': data})

        type_grants_pcts = chunk_df['grants_pct'].values
        if len(type_grants_pcts) >= 10 and np.var(type_grants_pcts) > 0:
            with tqdm(total=25, desc=f"Generating grants_pct histogram for {org_type}") as pbar_hist:
                grants_bins, bin_edges = np.histogram(type_grants_pcts, bins=25, range=(0, 100))
                cumulative_grants = np.cumsum(grants_bins)
                total_grants = sum(grants_bins)
                percentiles_grants = (cumulative_grants / total_grants * 100) if total_grants > 0 else np.zeros_like(cumulative_grants)
                data = []
                i = 0
                while i < len(grants_bins):
                    start = bin_edges[i]
                    count = grants_bins[i]
                    percentile = percentiles_grants[i]
                    end = bin_edges[i + 1] if i + 1 < len(bin_edges) else 100
                    if count == 0 and i < len(grants_bins) - 1:
                        j = i
                        while j < len(grants_bins) - 1 and grants_bins[j] == 0:
                            j += 1
                        if j > i:
                            end = bin_edges[j]
                            i = j
                        else:
                            i += 1
                    else:
                        i += 1
                    data.append((f"{start:.1f}", f"{end:.1f}", count, percentile))
                    pbar_hist.update(1)
            org_type_data['metrics'].append({'title': f"Grants to Others Percentage (Tax Year {year})", 'data': data})

        type_foreign_exp_pcts = chunk_df['foreign_exp_pct'].values
        if len(type_foreign_exp_pcts) >= 10 and np.var(type_foreign_exp_pcts) > 0:
            with tqdm(total=25, desc=f"Generating foreign_exp_pct histogram for {org_type}") as pbar_hist:
                foreign_exp_bins, bin_edges = np.histogram(type_foreign_exp_pcts, bins=25, range=(0, 100))
                cumulative_foreign_exp = np.cumsum(foreign_exp_bins)
                total_foreign_exp = sum(foreign_exp_bins)
                percentiles_foreign_exp = (cumulative_foreign_exp / total_foreign_exp * 100) if total_foreign_exp > 0 else np.zeros_like(cumulative_foreign_exp)
                data = []
                i = 0
                while i < len(foreign_exp_bins):
                    start = bin_edges[i]
                    count = foreign_exp_bins[i]
                    percentile = percentiles_foreign_exp[i]
                    end = bin_edges[i + 1] if i + 1 < len(bin_edges) else 100
                    if count == 0 and i < len(foreign_exp_bins) - 1:
                        j = i
                        while j < len(foreign_exp_bins) - 1 and foreign_exp_bins[j] == 0:
                            j += 1
                        if j > i:
                            end = bin_edges[j]
                            i = j
                        else:
                            i += 1
                    else:
                        i += 1
                    data.append((f"{start:.1f}", f"{end:.1f}", count, percentile))
                    pbar_hist.update(1)
            org_type_data['metrics'].append({'title': f"Foreign Expenses Percentage (Tax Year {year})", 'data': data})

    return grift_candidates, org_types_seen, org_type_data

def analyze_year(year):
    global skip_log_count, add_log_count
    skip_log_count = 0
    add_log_count = 0
    all_grift_candidates = []
    all_org_types_seen = set()
    org_types_data = []

    org_types = [f"501(c)({i})" for i in range(1, 30)] + ["Unknown"]
    for org_type in org_types:
        org_type_clean = re.sub(r'[^a-zA-Z0-9]', '_', org_type)
        input_tsv = f"charities_{org_type_clean}_{year}.tsv"
        print(f"\nAnalyzing {input_tsv}")
        grift_candidates, org_types_seen, org_type_data = analyze_type(year, org_type, input_tsv)
        all_grift_candidates.extend(grift_candidates)
        all_org_types_seen.update(org_types_seen)
        if org_type_data:
            org_types_data.append(org_type_data)

    if org_types_data:
        script_dir = os.path.dirname(__file__)
        histogram_template = Template(filename=os.path.join(script_dir, 'histogram_template.mako'))
        try:
            histogram_output = histogram_template.render(org_types=org_types_data)
            with open(f"histogram_report_{year}.md", "w", encoding="utf-8") as md_file:
                md_file.write(histogram_output)
            print(f"Wrote histogram report to histogram_report_{year}.md")
        except MakoException as e:
            log_error(f"Error rendering histogram for tax year {year}: {e}", exc_info=True)

    if all_grift_candidates:
        unique_candidates = []
        seen = set()
        for cand in all_grift_candidates:
            key = (cand['filer_ein'], cand['org_type'])
            if key not in seen:
                unique_candidates.append(cand)
                seen.add(key)

        top_grift = sorted(unique_candidates, key=lambda x: x['grift_ratio_percentile'] if not pd.isna(x['grift_ratio_percentile']) else -1, reverse=True)[:10]
        top_grants = sorted(unique_candidates, key=lambda x: x['grants_pct_percentile'] if not pd.isna(x['grants_pct_percentile']) else -1, reverse=True)[:10]
        grift_report_template = Template(filename=os.path.join(script_dir, 'grift_report_template.mako'))
        try:
            report_output = grift_report_template.render(tax_year=year, top_grift=top_grift, top_grants=top_grants, ORG_TYPE_DESCRIPTIONS=ORG_TYPE_DESCRIPTIONS)
            with open(f"grift_report_{year}.md", "w", encoding="utf-8") as md_file:
                md_file.write(report_output)
            print(f"Wrote grift report to grift_report_{year}.md")
        except MakoException as e:
            log_error(f"Error rendering Mako template for tax year {year}: {e}", exc_info=True)
            print(f"Failed to generate grift_report_{year}.md due to template error")

    log_error(f"Org types processed for grift_candidates: {sorted(all_org_types_seen)}")
    gc.collect()
    print(f"Cleared memory for {year}")

def main():
    global skip_log_count, add_log_count
    skip_log_count = 0
    add_log_count = 0
    parser = argparse.ArgumentParser(description="Analyze charity TSVs to compute percentiles and generate reports.")
    parser.add_argument("start_year", type=int, help="Start year for TSV analysis (e.g., 2016).")
    parser.add_argument("end_year", type=int, help="End year for TSV analysis (e.g., 2024).")

    args = parser.parse_args()
    if args.start_year > args.end_year:
        raise argparse.ArgumentError(None, "Start year must be less than or equal to end year.")

    for year in range(args.start_year, args.end_year + 1):
        print(f"\nAnalyzing charities for tax year {year}")
        try:
            analyze_year(year)
        except Exception as e:
            log_error(f"Error analyzing tax year {year}: {e}", exc_info=True)
            print(f"Failed to analyze tax year {year}, continuing to next year")

if __name__ == "__main__":
    main()