import csv
import glob
import os
import re

def combine_charities(output_file):
    files = []
    for tsv_file in glob.glob("charities_*.tsv"):
        match = re.search(r'charities_(\d{4})\.tsv', tsv_file)
        if match:
            year = int(match.group(1))
            files.append((tsv_file, year))
    files.sort(key=lambda x: x[1], reverse=True)

    if not files:
        print("No charities_*.tsv files found.")
        return

    charities = {}
    for filename, year in files:
        with open(filename, mode='r', newline='', encoding='utf-8') as f:
            reader = csv.DictReader(f, delimiter='\t')
            for row in reader:
                ein = row['filer_ein']
                if ein not in charities:
                    row['tax_year'] = year
                    charities[ein] = row

    fieldnames = [
        'tax_year', 'filer_ein', 'filer_name', 'receipt_amt', 'govt_amt', 'contrib_amt', 'org_type',
        'total_exp', 'prog_exp', 'travel_amt', 'conferences_amt', 'officer_comp', 'comp_pct',
        'travel_pct', 'grift_pct', 'grift_pct_percentile', 'grift_pct_rating', 'grift_ratio',
        'grift_ratio_percentile', 'grift_ratio_rating', 'external_grants_ratio',
        'external_grants_percentile', 'external_grants_rating',
        'grift_pct_type_percentile', 'grift_ratio_type_percentile', 'external_grants_type_percentile',
        'total_assets', 'form_type', 'denominator', 'foreign_office', 'foreign_expenses', 'grants_to_others',
        'domestic_misrep_flag'
    ]

    with open(output_file, mode='w', newline='', encoding='utf-8') as outfile:
        writer = csv.DictWriter(outfile, fieldnames=fieldnames, delimiter='\t')
        writer.writeheader()
        for ein, data in charities.items():
            out_data = {field: data.get(field, '') for field in fieldnames}
            writer.writerow(out_data)

    print(f"Combined TSV file created: {output_file} with {len(charities)} records")

if __name__ == '__main__':
    combine_charities('charities_by_latest.tsv')