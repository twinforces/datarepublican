import os
import pandas as pd
from googlesearch import search
import time
import logging
from datetime import datetime

# Set up logging
logging.basicConfig(
    filename='/Volumes/Data/final/verification_log.txt',
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

# Directory containing TSV files
data_dir = '/Volumes/Data/final/'
tsv_files = [f'grokset{i}.tsv' for i in range(1, 11)]  # Adjust range if fewer files
output_file = '/Volumes/Data/final/nonprofit_data_combined.tsv'

def combine_tsv_files():
    """Combine TSV files, keeping only the first header."""
    combined_data = []
    header = None
    total_lines = 0
    
    for i, tsv_file in enumerate(tsv_files, 1):
        file_path = os.path.join(data_dir, tsv_file)
        if not os.path.exists(file_path):
            logging.error(f"File {tsv_file} not found")
            continue
        
        # Read TSV file
        df = pd.read_csv(file_path, sep='\t')
        line_count = len(df) + 1  # Including header
        logging.info(f"{tsv_file}: {line_count} lines")
        total_lines += line_count
        
        if i == 1:
            header = df.columns
            combined_data.append(df)
        else:
            combined_data.append(df)
    
    # Combine all data
    combined_df = pd.concat(combined_data, ignore_index=True)
    combined_df.to_csv(output_file, sep='\t', index=False)
    logging.info(f"Combined file saved: {output_file}, Total lines: {len(combined_df) + 1}")
    
    return combined_df, total_lines

def check_uniqueness(combined_df):
    """Verify uniqueness of EINs."""
    eins = combined_df['EIN'].dropna()
    unique_eins = eins.unique()
    duplicates = eins[eins.duplicated()].tolist()
    
    if len(unique_eins) == len(eins):
        logging.info(f"All {len(unique_eins)} EINs are unique")
    else:
        logging.warning(f"Found {len(duplicates)} duplicate EINs: {duplicates}")
    
    return len(unique_eins)

def validate_with_google(combined_df, sample_size=50, rate_limit=1):
    """Validate a sample of organization names via Google search."""
    validated = []
    sample_df = combined_df[combined_df['Name'] != 'Data Unavailable'].sample(
        n=min(sample_size, len(combined_df)), random_state=42
    )
    
    for index, row in sample_df.iterrows():
        query = f"{row['Name']} nonprofit site:*.edu | site:*.org | site:*.gov"
        try:
            # Rate limiting: 10 requests per second
            time.sleep(1.0 / rate_limit)
            results = list(search(query, num_results=3))
            status = "Found" if results else "Not Found"
            logging.info(f"Validated {row['EIN']} - {row['Name']}: {status}")
            validated.append((row['EIN'], row['Name'], status, results))
        except Exception as e:
            logging.error(f"Error validating {row['EIN']} - {row['Name']}: {str(e)}")
            validated.append((row['EIN'], row['Name'], "Error", [str(e)]))
    
    return validated

def main():
    logging.info(f"Starting verification at {datetime.now()}")
    
    # Combine TSV files
    combined_df, total_lines = combine_tsv_files()
    
    # Check uniqueness
    unique_count = check_uniqueness(combined_df)
    
    # Validate with Google
    validated_results = validate_with_google(combined_df)
    
    # Summary
    logging.info(f"Verification Summary:")
    logging.info(f"Total lines across files: {total_lines}")
    logging.info(f"Unique EINs: {unique_count}")
    logging.info(f"Validated {len(validated_results)} entries via Google")
    for ein, name, status, results in validated_results:
        logging.info(f"EIN: {ein}, Name: {name}, Status: {status}, Results: {results[:1]}")
    
    print(f"Verification complete. Check /Volumes/Data/final/verification_log.txt for details.")

if __name__ == "__main__":
    main()