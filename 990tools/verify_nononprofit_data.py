import os
import pandas as pd
from googlesearch import search
import time
import logging
from datetime import datetime
import requests

# Set up logging
logging.basicConfig(
    filename='/Volumes/Data/final/verification_log.txt',
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

# Directory containing TSV files
data_dir = '/Volumes/Data/final/'
tsv_files = [f'grokset{i}.tsv' for i in range(1, 11)]  # Adjust for available files
output_file = '/Volumes/Data/final/nonprofit_data_combined.tsv'

def combine_tsv_files():
    """Combine available TSV files, keeping only the first header."""
    combined_data = []
    header = None
    total_lines = 0
    file_counts = {}

    for tsv_file in tsv_files:
        file_path = os.path.join(data_dir, tsv_file)
        if not os.path.exists(file_path):
            logging.warning(f"File {tsv_file} not found, skipping")
            continue
        
        try:
            df = pd.read_csv(file_path, sep='\t')
            line_count = len(df) + 1  # Including header
            file_counts[tsv_file] = line_count
            total_lines += line_count
            if not combined_data:
                header = df.columns
                combined_data.append(df)
            else:
                combined_data.append(df)
        except Exception as e:
            logging.error(f"Error reading {tsv_file}: {str(e)}")
            continue
    
    if not combined_data:
        logging.error("No valid TSV files found")
        return None, 0, file_counts

    combined_df = pd.concat(combined_data, ignore_index=True)
    combined_df.to_csv(output_file, sep='\t', index=False)
    logging.info(f"Combined file saved: {output_file}, Total lines: {len(combined_df) + 1}")
    
    return combined_df, total_lines, file_counts

def check_uniqueness(combined_df):
    """Verify uniqueness of EINs and log duplicates."""
    eins = combined_df['EIN'].dropna()
    unique_eins = eins.unique()
    duplicates = eins[eins.duplicated()].tolist()
    
    if duplicates:
        logging.warning(f"Found {len(duplicates)} duplicate EINs: {duplicates}")
        for dup in duplicates:
            dup_rows = combined_df[combined_df['EIN'] == dup][['EIN', 'Name', 'Zip Code']]
            logging.warning(f"Duplicate EIN {dup} details:\n{dup_rows.to_string()}")
    else:
        logging.info(f"All {len(unique_eins)} EINs are unique")
    
    return len(unique_eins), duplicates

def validate_with_google(combined_df, sample_size=20, rate_limit=10, max_retries=3):
    """Validate a sample of organization names via Google search with retry logic."""
    validated = []
    sample_df = combined_df[combined_df['Name'] != 'Data Unavailable'].sample(
        n=min(sample_size, len(combined_df[combined_df['Name'] != 'Data Unavailable'])),
        random_state=42
    )
    
    for index, row in sample_df.iterrows():
        query = f"{row['Name']} nonprofit site:*.edu | site:*.org | site:*.gov"
        for attempt in range(max_retries):
            try:
                time.sleep(0.12)  # Stricter delay for 10 requests/second
                results = list(search(query, num_results=3))
                status = "Found" if results else "Not Found"
                logging.info(f"Validated {row['EIN']} - {row['Name']}: {status}, Results: {results[:1]}")
                validated.append((row['EIN'], row['Name'], status, results))
                break
            except requests.exceptions.HTTPError as e:
                if "429" in str(e):
                    logging.warning(f"429 Too Many Requests for {row['EIN']} - {row['Name']}, retrying ({attempt + 1}/{max_retries})")
                    time.sleep(2 ** attempt)  # Exponential backoff
                    if attempt == max_retries - 1:
                        logging.error(f"Failed to validate {row['EIN']} - {row['Name']}: {str(e)}")
                        validated.append((row['EIN'], row['Name'], "Error", [str(e)]))
                else:
                    logging.error(f"Error validating {row['EIN']} - {row['Name']}: {str(e)}")
                    validated.append((row['EIN'], row['Name'], "Error", [str(e)]))
                    break
            except Exception as e:
                logging.error(f"Error validating {row['EIN']} - {row['Name']}: {str(e)}")
                validated.append((row['EIN'], row['Name'], "Error", [str(e)]))
                break
    
    return validated

def main():
    logging.info(f"Starting verification at {datetime.now()}")
    
    # Combine TSV files
    combined_df, total_lines, file_counts = combine_tsv_files()
    if combined_df is None:
        logging.error("Verification aborted due to no valid files")
        return
    
    # Log individual file counts
    for file, count in file_counts.items():
        logging.info(f"{file}: {count} lines")
    
    # Check uniqueness
    unique_count, duplicates = check_uniqueness(combined_df)
    
    # Validate with Google
    validated_results = validate_with_google(combined_df)
    
    # Summary
    logging.info(f"Verification Summary:")
    logging.info(f"Total lines across files: {total_lines}")
    logging.info(f"Unique EINs: {unique_count}")
    logging.info(f"Duplicates: {len(duplicates)}")
    logging.info(f"Validated {len(validated_results)} entries via Google")
    for ein, name, status, results in validated_results:
        logging.info(f"EIN: {ein}, Name: {name}, Status: {status}, First Result: {results[:1]}")
    
    print(f"Verification complete. Check /Volumes/Data/final/verification_log.txt for details.")

if __name__ == "__main__":
    main()