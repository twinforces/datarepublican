import re
import csv
from typing import List, Dict
import requests
from bs4 import BeautifulSoup
from pathlib import Path
import time
import logging
import argparse
from urllib.parse import quote

def setup_logging(log_file: str) -> None:
    """Set up logging to file and console."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(message)s",
        handlers=[
            logging.FileHandler(log_file),
            logging.StreamHandler()
        ]
    )

def extract_eins(input_file: str) -> List[str]:
    """Extract EINs from input file using regex."""
    ein_pattern = r'\d{2}-\d{7}'
    eins = set()
    try:
        with open(input_file, 'r', encoding='utf-8') as f:
            text = f.read()
            eins.update(re.findall(ein_pattern, text))
        logging.info(f"Extracted {len(eins)} unique EINs from {input_file}")
        return sorted(list(eins))
    except FileNotFoundError:
        logging.error(f"Input file {input_file} not found")
        return []

def scrape_google_search(ein: str) -> Dict[str, str]:
    """Scrape Google search results for EIN, prioritizing FDPClearinghouse, .gov, or university sites."""
    url = f"https://www.google.com/search?q={quote(ein + ' ein name address')}"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"}
    try:
        time.sleep(1)  # 1-second delay to avoid rate limits
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')

        name = "Data Unavailable"
        zip_code = "Not Found"
        address = f"No public nonprofit record, issued in {get_issuance_location(ein)}"
        source = "Google search"

        # Priority 1: FDPClearinghouse
        fdp_link = soup.find('a', href=re.compile(r'fdpclearinghouse\.org/organizations/\d+'))
        if fdp_link:
            fdp_url = fdp_link['href']
            time.sleep(1)  # Delay for next request
            fdp_response = requests.get(fdp_url, headers=headers, timeout=10)
            fdp_response.raise_for_status()
            fdp_soup = BeautifulSoup(fdp_response.text, 'html.parser')
            name_tag = fdp_soup.find('h1') or fdp_soup.find(string=re.compile(r'(University|College|Foundation|National|Institute)'))
            if name_tag:
                name = name_tag.text.strip()
            address_tag = fdp_soup.find('address') or fdp_soup.find(string=re.compile(r'\d{5}(-\d{4})?'))
            if address_tag:
                address_text = address_tag.text if hasattr(address_tag, 'text') else address_tag
                address = address_text.strip()
                zip_match = re.search(r'\d{5}(-\d{4})?', address)
                zip_code = zip_match.group(0) if zip_match else "Not Found"
                source = "FDPClearinghouse"
            if name != "Data Unavailable":
                logging.info(f"Found FDPClearinghouse data for EIN {ein}: {name}")
                return {"name": name, "zip": zip_code, "address": address, "source": source}

        # Priority 2: .gov sites (e.g., NSF, state registries)
        gov_link = soup.find('a', href=re.compile(r'\.gov'))
        if gov_link:
            gov_url = gov_link['href']
            time.sleep(1)  # Delay for next request
            gov_response = requests.get(gov_url, headers=headers, timeout=10)
            gov_response.raise_for_status()
            gov_soup = BeautifulSoup(gov_response.text, 'html.parser')
            name_tag = gov_soup.find('h1') or gov_soup.find(string=re.compile(r'(National|Institute|University|College|Foundation)'))
            if name_tag:
                name = name_tag.text.strip()
            address_tag = gov_soup.find('address') or gov_soup.find(string=re.compile(r'\d{5}(-\d{4})?'))
            if address_tag:
                address_text = address_tag.text if hasattr(address_tag, 'text') else address_tag
                address = address_text.strip()
                zip_match = re.search(r'\d{5}(-\d{4})?', address)
                zip_code = zip_match.group(0) if zip_match else "Not Found"
                source = "Government website"
            if name != "Data Unavailable":
                logging.info(f"Found government website data for EIN {ein}: {name}")
                return {"name": name, "zip": zip_code, "address": address, "source": source}

        # Priority 3: University websites
        uni_link = soup.find('a', href=re.compile(r'(edu|university|college)'))
        if uni_link:
            uni_url = uni_link['href']
            time.sleep(1)  # Delay for next request
            uni_response = requests.get(uni_url, headers=headers, timeout=10)
            uni_response.raise_for_status()
            uni_soup = BeautifulSoup(uni_response.text, 'html.parser')
            name_tag = uni_soup.find('h1') or uni_soup.find(string=re.compile(r'(University|College|Foundation)'))
            if name_tag:
                name = name_tag.text.strip()
            address_tag = uni_soup.find('address') or uni_soup.find(string=re.compile(r'\d{5}(-\d{4})?'))
            if address_tag:
                address_text = address_tag.text if hasattr(address_tag, 'text') else address_tag
                address = address_text.strip()
                zip_match = re.search(r'\d{5}(-\d{4})?', address)
                zip_code = zip_match.group(0) if zip_match else "Not Found"
                source = "University website"
            if name != "Data Unavailable":
                logging.info(f"Found university website data for EIN {ein}: {name}")
                return {"name": name, "zip": zip_code, "address": address, "source": source}

        # Priority 4: NCES College Navigator
        nces_url = f"https://nces.ed.gov/collegenavigator/?q={quote(ein)}"
        time.sleep(1)  # Delay for next request
        nces_response = requests.get(nces_url, headers=headers, timeout=10)
        nces_response.raise_for_status()
        nces_soup = BeautifulSoup(nces_response.text, 'html.parser')
        name_tag = nces_soup.find('h1') or nces_soup.find(string=re.compile(r'(University|College|Foundation)'))
        if name_tag:
            name = name_tag.text.strip()
        address_tag = nces_soup.find('address') or nces_soup.find(string=re.compile(r'\d{5}(-\d{4})?'))
        if address_tag:
            address_text = address_tag.text if hasattr(address_tag, 'text') else address_tag
            address = address_text.strip()
            zip_match = re.search(r'\d{5}(-\d{4})?', address)
            zip_code = zip_match.group(0) if zip_match else "Not Found"
            source = "NCES College Navigator"
        if name != "Data Unavailable":
            logging.info(f"Found NCES data for EIN {ein}: {name}")
            return {"name": name, "zip": zip_code, "address": address, "source": source}

        # Fallback: Google search snippet
        result = soup.find('div', class_='BNeawe iBp4i AP7Wnd') or soup.find('div', class_='BNeawe s3v9rd AP7Wnd')
        if result:
            text = result.text
            name_match = re.search(r'^(.*?)(?:\s+\|)', text) or re.search(r'(University|College|Foundation|National|Institute).*?(?=\s|$)', text)
            name = name_match.group(0).strip() if name_match else "Data Unavailable"
            zip_match = re.search(r'\d{5}(-\d{4})?', text)
            zip_code = zip_match.group(0) if zip_match else "Not Found"
            address = text if zip_match else f"No public nonprofit record, issued in {get_issuance_location(ein)}"
            source = "Google search"

        logging.info(f"Scraped data for EIN {ein}: {name}")
        return {"name": name, "zip": zip_code, "address": address, "source": source}
    except Exception as e:
        logging.error(f"Error scraping Google for EIN {ein}: {e}")
        return {
            "name": "Data Unavailable",
            "zip": "Not Found",
            "address": f"No public nonprofit record, issued in {get_issuance_location(ein)}",
            "source": "Google search"
        }

def get_issuance_location(ein: str) -> str:
    """Determine IRS issuance location based on EIN prefix."""
    prefix = ein.split('-')[0]
    locations = {
        "04": "Andover, MA", "16": "Brookhaven, NY", "22": "Philadelphia, PA",
        "35": "Andover, MA", "37": "Philadelphia, PA", "41": "Philadelphia, PA",
        "42": "Philadelphia, PA", "47": "Kansas City, MO", "51": "Philadelphia, PA",
        "54": "Richmond, VA", "56": "Charlotte, NC", "58": "Atlanta, GA",
        "59": "Jacksonville, FL", "74": "Austin, TX", "84": "Denver, CO",
        "86": "Phoenix, AZ", "87": "Salt Lake City, UT", "88": "Ogden, UT",
        "91": "Seattle, WA", "92": "Anchorage, AK", "93": "Portland, OR",
        "95": "Los Angeles, CA"
    }
    return locations.get(prefix, "Unknown")

def generate_tsv(eins: List[str], output_file: str) -> None:
    """Generate a TSV file for the provided EINs."""
    output_path = Path(output_file)
    output_path.parent.mkdir(exist_ok=True)
    with open(output_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f, delimiter='\t')
        writer.writerow(['EIN', 'Name', 'Zip Code', 'Address', 'Source'])
        for ein in eins:
            data = scrape_google_search(ein)
            writer.writerow([ein, data['name'], data['zip'], data['address'], data['source']])
        logging.info(f"Generated {output_file} with {len(eins)} EINs")

def verify_tsv(output_file: str) -> int:
    """Verify the number of lines in the TSV file."""
    try:
        with open(output_file, 'r', encoding='utf-8') as f:
            line_count = sum(1 for _ in f)
        logging.info(f"Verified {output_file} with {line_count} lines")
        return line_count
    except FileNotFoundError:
        logging.error(f"Output file {output_file} not found")
        return 0

def main():
    parser = argparse.ArgumentParser(description="Generate TSV files from EINs using Google search.")
    parser.add_argument('--input-file', required=True, help="Path to input file with EINs")
    parser.add_argument('--output-dir', required=True, help="Directory for output TSV files")
    parser.add_argument('--log-file', required=True, help="Path to log file")
    parser.add_argument('--output-file', default="grokset1.tsv", help="Name of output TSV file (default: grokset1.tsv)")
    args = parser.parse_args()

    setup_logging(args.log_file)

    # Extract EINs from input file
    input_eins = extract_eins(args.input_file)
    if not input_eins:
        logging.error("No EINs extracted. Exiting.")
        return

    # Deduplicate EINs
    input_eins = sorted(list(set(input_eins)))
    logging.info(f"Deduplicated to {len(input_eins)} unique EINs")

    # Generate TSV
    output_file = Path(args.output_dir) / args.output_file
    generate_tsv(input_eins, output_file)

    # Verify line count
    line_count = verify_tsv(output_file)
    print(f"Generated {output_file} with {line_count} lines (1 header + {line_count - 1} EINs)")

if __name__ == "__main__":
    main()