#!/usr/bin/env python3
import pandas as pd
import sys
import argparse
from pathlib import Path

def filter_tsv(input_file, output_file, filter_column, filter_value, selected_columns):
    try:
        # Read TSV file
        df = pd.read_csv(input_file, sep='\t',dtype={'filer_ein': str})
        
        # Verify filter column exists
        if filter_column not in df.columns:
            raise ValueError(f"Filter column '{filter_column}' not found in TSV")
        
        # Verify all selected columns exist
        missing_cols = [col for col in selected_columns if col not in df.columns]
        if missing_cols:
            raise ValueError(f"Selected columns not found in TSV: {missing_cols}")
        
        # Filter rows based on value
        filtered_df = df[df[filter_column] > filter_value]
        
        # Select specified columns
        filtered_df = filtered_df[selected_columns]
        
        # Write to output file
        filtered_df.to_csv(output_file, sep='\t', index=False)
        
        print(f"Filtered TSV written to {output_file}")
        print(f"Original rows: {len(df)}, Filtered rows: {len(filtered_df)}")
        
        return df, filtered_df
    
    except Exception as e:
        print(f"Error: {str(e)}")
        sys.exit(1)

def analyze_tsv(input_df, output_df, output_md):
    try:
        # Verify columns exist
        required_cols = ['org_type', 'form_type']
        for df, name in [(input_df, 'input'), (output_df, 'output')]:
            missing_cols = [col for col in required_cols if col not in df.columns]
            if missing_cols:
                raise ValueError(f"Columns {missing_cols} not found in {name} TSV")

        # Group by org_type and form_type, count occurrences
        input_counts = input_df.groupby(['org_type', 'form_type']).size().reset_index(name='input_count')
        output_counts = output_df.groupby(['org_type', 'form_type']).size().reset_index(name='output_count')

        # Merge counts, fill missing with 0
        merged = input_counts.merge(output_counts, on=['org_type', 'form_type'], how='outer').fillna(0)
        merged['input_count'] = merged['input_count'].astype(int)
        merged['output_count'] = merged['output_count'].astype(int)

        # Sort for readability
        merged = merged.sort_values(['org_type', 'form_type'])

        # Generate Markdown table
        markdown = "| org_type | form_type | Input Count | Output Count |\n"
        markdown += "|----------|-----------|-------------|--------------|\n"
        for _, row in merged.iterrows():
            markdown += f"| {row['org_type']} | {row['form_type']} | {row['input_count']} | {row['output_count']} |\n"

        # Write to output file
        with open(output_md, 'w') as f:
            f.write(markdown)

        print(f"Analysis written to {output_md}")

    except Exception as e:
        print(f"Analysis Error: {str(e)}")
        sys.exit(1)

def main():
    parser = argparse.ArgumentParser(description='Filter TSV file by column value and select specific columns')
    parser.add_argument('--input-file', help='Input TSV file', required=True)
    parser.add_argument('--output-file', help='Output TSV file', required=True)
    parser.add_argument('--filter-column', help='Column to filter on', default="denominator")
    parser.add_argument('--filter-value', type=float, help='Minimum value for filter column', default=1000000)
    parser.add_argument('--columns', nargs='+', help='Columns to keep', 
                      default=["tax_year", "org_type", "form_type", "total_assets", "denominator", 
                               "xml_name", "filer_ein", "receipt_amt", "govt_amt", "contrib_amt", "filer_name"])
    parser.add_argument('--analysis-md', help='Output Markdown file for analysis', default="analysis.md")

    args = parser.parse_args()

    # Filter TSV and get input/output DataFrames
    input_df, output_df = filter_tsv(args.input_file, args.output_file, args.filter_column, 
                                    args.filter_value, args.columns)

    # Analyze org_type and form_type
    analyze_tsv(input_df, output_df, args.analysis_md)

if __name__ == '__main__':
    main()