"""
Utility commands for IRS 990 processing
"""

def extract_ein_files(ein, zips_dir, output_dir='./extracted_ein', tsvs_dir=None,
                    index_file=None, start_year=2017, end_year=2025, verbose=False, quiet=False):
    """Extract all XML files for a specific EIN from ZIP files."""
    import zipfile
    import os
    import json
    from pathlib import Path
    from lxml import etree

    if not quiet:
        print(f"Extracting XML files for EIN: {ein}")
        print(f"ZIP directory: {zips_dir}")
        print(f"Output directory: {output_dir}")

    # Create output directory
    os.makedirs(output_dir, exist_ok=True)

    # Load or build index
    xml_index = {}
    ein_index = {}

    # First try to load indexes from the TSVs directory (created by extract-charities)
    if tsvs_dir:
        ein_index_file = os.path.join(tsvs_dir, 'ein_xml_index.json')
        xml_index_file = os.path.join(tsvs_dir, 'xml_zip_index.json')
    # Then try the output directory
    elif output_dir != './extracted_ein':
        ein_index_file = os.path.join(output_dir, 'ein_xml_index.json')
        xml_index_file = os.path.join(output_dir, 'xml_zip_index.json')
    else:
        # Fall back to current directory or specified index file
        xml_index_file = index_file or './xml_zip_index.json'
        ein_index_file = index_file.replace('.json', '_ein.json') if index_file else './xml_zip_index_ein.json'

    # Try to load EIN index first (faster) - this is created during extract-charities
    if os.path.exists(ein_index_file):
        if not quiet:
            print(f"Loading existing EIN index: {ein_index_file}")
        with open(ein_index_file, 'r') as f:
            ein_index = json.load(f)
        if not quiet:
            print(f"Loaded EIN index with {len(ein_index)} EINs")
    elif os.path.exists(xml_index_file):
        if not quiet:
            print(f"Loading existing XML index: {xml_index_file}")
        with open(xml_index_file, 'r') as f:
            xml_index = json.load(f)
        if not quiet:
            print(f"Loaded XML index with {len(xml_index)} files")
    else:
        if not quiet:
            print("No existing indexes found. Building XML index (this may take a while)...")
        xml_index, ein_index = build_xml_index_internal(zips_dir, start_year, end_year, verbose, quiet, build_ein_index=True)
        with open(xml_index_file, 'w') as f:
            json.dump(xml_index, f, indent=2)
        if ein_index:
            with open(ein_index_file, 'w') as f:
                json.dump(ein_index, f, indent=2)
        if not quiet:
            print(f"Saved XML index to: {xml_index_file}")
            if ein_index:
                print(f"Saved EIN index to: {ein_index_file}")

    # Find all XML files for this EIN
    ein_files = []

    # Use EIN index if available (much faster) - this is created during extract-charities
    if ein_index and ein in ein_index:
        if not quiet:
            print(f"Using EIN index: found {len(ein_index[ein])} files for EIN {ein}")
        for entry in ein_index[ein]:
            ein_files.append((entry['xml_file'], entry['zip_path'], "ein_index"))
            if verbose:
                print(f"Found EIN {ein}: {entry['xml_file']} (tax_year: {entry.get('tax_year', 'N/A')}, form: {entry.get('form_type', 'N/A')})")
    else:
        # Fall back to searching through XML index
        searched_files = 0
        content_matches = 0

        if not quiet:
            print(f"Searching {len(xml_index)} XML files for EIN {ein}...")
            print("Note: For faster searches, run 'extract-charities' first to build EIN index")

        for xml_file, zip_path in xml_index.items():
            searched_files += 1
            found_in_filename = False
            found_in_content = False

            # First check filename
            if ein in xml_file:
                ein_files.append((xml_file, zip_path, "filename"))
                found_in_filename = True
                if verbose:
                    print(f"Found EIN {ein} in filename: {xml_file}")

            # Also check content if not found in filename
            if not found_in_filename:
                try:
                    with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                        with zip_ref.open(xml_file) as xml_file_obj:
                            content = xml_file_obj.read().decode('utf-8', errors='ignore')

                            # Search for EIN in various XML element formats
                            ein_patterns = [
                                f'<EIN>{ein}</EIN>',
                                f'<irs:EIN>{ein}</irs:EIN>',
                                f'<RecipientEIN>{ein}</RecipientEIN>',
                                f'<irs:RecipientEIN>{ein}</irs:RecipientEIN>',
                                f'<BusinessNameLine1Txt>{ein}</BusinessNameLine1Txt>',  # Sometimes EIN appears in business name
                            ]

                            for pattern in ein_patterns:
                                if pattern in content:
                                    ein_files.append((xml_file, zip_path, "content"))
                                    found_in_content = True
                                    content_matches += 1
                                    if verbose:
                                        print(f"Found EIN {ein} in content of: {xml_file}")
                                    break

                except Exception as e:
                    if verbose:
                        print(f"Error reading content of {xml_file}: {e}")

            # Progress update
            if searched_files % 1000 == 0 and not getattr(args, 'quiet', False):
                print(f"Searched {searched_files} files, found {len(ein_files)} matches so far...")

        if not quiet:
            print(f"Search complete: examined {searched_files} files")
            print(f"- Filename matches: {len([f for f in ein_files if f[2] == 'filename'])}")
            print(f"- Content matches: {len([f for f in ein_files if f[2] == 'content'])}")

    if not quiet:
        print(f"Found {len(ein_files)} XML files for EIN {ein}")
        if ein_files:
            print(f"- EIN index matches: {len([f for f in ein_files if f[2] == 'ein_index'])}")
            print(f"- Filename matches: {len([f for f in ein_files if f[2] == 'filename'])}")
            print(f"- Content matches: {len([f for f in ein_files if f[2] == 'content'])}")

    if not ein_files:
        if not quiet:
            print(f"No XML files found for EIN {ein}")
            print("This could mean:")
            print("1. The EIN doesn't exist in the dataset")
            print("2. The EIN format in the XML is different")
            print("3. The files are in a different year range")
        return

    # Extract files
    extracted_count = 0
    for xml_file, zip_path, match_type in ein_files:
        try:
            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                # Extract to output directory
                zip_ref.extract(xml_file, output_dir)
                extracted_count += 1

                if verbose:
                    print(f"Extracted: {xml_file} from {os.path.basename(zip_path)} (match: {match_type})")

        except Exception as e:
            print(f"Error extracting {xml_file} from {zip_path}: {e}")

    if not quiet:
        print(f"Successfully extracted {extracted_count} XML files to {output_dir}")

    # Save EIN index for future use
    ein_index_file = os.path.join(output_dir, f"ein_{ein}_index.json")
    ein_index = {xml_file: {"zip_path": zip_path, "match_type": match_type}
                 for xml_file, zip_path, match_type in ein_files}
    with open(ein_index_file, 'w') as f:
        json.dump(ein_index, f, indent=2)
    if not quiet:
        print(f"Saved EIN index to: {ein_index_file}")

def build_xml_index(zips_dir, index_file='./xml_zip_index.json', ein_index_file=None,
                   start_year=2017, end_year=2025, verbose=False, quiet=False):
    """Build XML to ZIP file index and optionally EIN to XML index for faster lookups."""
    xml_index, ein_index = build_xml_index_internal(zips_dir, start_year, end_year, verbose, quiet, build_ein_index=bool(ein_index_file))

    # Save XML->ZIP index
    with open(index_file, 'w') as f:
        json.dump(xml_index, f, indent=2)

    if not quiet:
        print(f"XML->ZIP index saved to: {index_file}")
        print(f"Total XML files indexed: {len(xml_index)}")

    # Save EIN->XML index if requested
    if ein_index_file and ein_index:
        with open(ein_index_file, 'w') as f:
            json.dump(ein_index, f, indent=2)
        if not quiet:
            print(f"EIN->XML index saved to: {ein_index_file}")
            print(f"Total EINs indexed: {len(ein_index)}")

def build_xml_index_internal(zips_dir, start_year, end_year, verbose=False, quiet=False, build_ein_index=False):
    """Internal function to build XML and optionally EIN index."""
    import zipfile
    import os
    import glob
    import re
    from lxml import etree

    xml_index = {}
    ein_index = {} if build_ein_index else None

    # Find all ZIP files in the directory
    zip_pattern = os.path.join(zips_dir, "*.zip")
    zip_files = sorted(glob.glob(zip_pattern))

    if not zip_files:
        if not quiet:
            print(f"No ZIP files found in {zips_dir}")
        return xml_index, ein_index

    for zip_path in zip_files:
        zip_filename = os.path.basename(zip_path)

        # Check if ZIP file is within year range
        if zip_filename[:4].isdigit():
            zip_year = int(zip_filename[:4])
            if zip_year < start_year or zip_year > end_year:
                continue

        if verbose and not quiet:
            print(f"Indexing: {zip_filename}")

        try:
            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                xml_files = [f for f in zip_ref.namelist() if f.endswith('.xml')]

                for xml_file in xml_files:
                    xml_index[xml_file] = zip_path

                    # Build EIN index if requested
                    if build_ein_index:
                        try:
                            with zip_ref.open(xml_file) as xml_file_obj:
                                content = xml_file_obj.read().decode('utf-8', errors='ignore')

                                # Extract EIN from XML content
                                ein_match = re.search(r'<(?:irs:)?EIN>(\d{9})</(?:irs:)?EIN>', content)
                                if ein_match:
                                    ein = ein_match.group(1)
                                    if ein not in ein_index:
                                        ein_index[ein] = []
                                    ein_index[ein].append({
                                        'xml_file': xml_file,
                                        'zip_path': zip_path
                                    })

                                # Also check filename for EIN (common pattern)
                                filename_ein_match = re.search(r'_(\d{9})_', xml_file)
                                if filename_ein_match:
                                    filename_ein = filename_ein_match.group(1)
                                    if filename_ein not in ein_index:
                                        ein_index[filename_ein] = []
                                    # Only add if not already present for this EIN
                                    if not any(entry['xml_file'] == xml_file for entry in ein_index[filename_ein]):
                                        ein_index[filename_ein].append({
                                            'xml_file': xml_file,
                                            'zip_path': zip_path
                                        })

                        except Exception as e:
                            if verbose:
                                print(f"Error extracting EIN from {xml_file}: {e}")

        except Exception as e:
            if not quiet:
                print(f"Error indexing {zip_path}: {e}")

    return xml_index, ein_index