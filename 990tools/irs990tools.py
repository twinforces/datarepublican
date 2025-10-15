#!/usr/bin/env python3
"""
IRS 990 Tools - A unified module for processing IRS 990 tax filings

This module provides a command-line interface to process IRS 990 forms,
extract charity data, analyze filings, and generate reports.

All pipeline functionality is contained within this single module as internal functions,
eliminating the need for subprocess calls and providing unified argument handling.
"""

import argparse
import sys
import os
import json

# Import configuration
from config import get_config

# Import our modular command functions
from utils.args import add_common_args, add_year_args, add_directory_args, add_processing_args, add_pipeline_dirs, resolve_directory_args
from commands.download import download_irs_zips, recompress_zips
from commands.extract import extract_charities, extract_addresses, extract_grants, add_backfill
from commands.analyze import analyze_charities, get_latest_filings, filter_charities, check_grants
from commands.pipeline import run_all_pipeline, run_from_step
from commands.utilities import extract_ein_files, build_xml_index

# Add the current directory to Python path for imports
sys.path.insert(0, os.path.dirname(__file__))

# Constants
NAMESPACES = {'irs': 'http://www.irs.gov/efile'}
CSV_QUOTE_FIELDS = {
    'charity': ['filer_name', 'org_type', "form_type", 'xml_name', 'foreign_office', 'domestic_misrep_flag'],
    'grants': ['filer_name', 'grant_ein'],
    'contributions': ['filer_name', 'recipient_ein'],
    'backfill': ['name', 'canonical_address', 'po_box', 'zip_code']
}

# Global variables for logging
logger = None

def setup_module_logging(output_dir, log_filename, verbose, quiet):
    """Set up logging for the module."""
    return cu.setup_logging(output_dir, log_filename, verbose, quiet)

def log_error(msg_format, *args, ein=None, exc_info=False):
    """Log an error message."""
    cu.log_error(msg_format, *args, ein=ein, exc_info=exc_info)
def check_zips_directory_status(zips_dir, start_year, end_year, verbose=False, quiet=False):
    """Check if ZIP files are present and up-to-date for the given year range."""
    import os
    import glob

    if not os.path.exists(zips_dir):
        if not quiet:
            print(f"ZIP directory {zips_dir} does not exist")
        return False, 0

    # Count ZIP files in the year range
    zip_pattern = os.path.join(zips_dir, "*.zip")
    zip_files = glob.glob(zip_pattern)
    year_range_files = []

    for zip_path in zip_files:
        zip_filename = os.path.basename(zip_path)
        if zip_filename[:4].isdigit():
            zip_year = int(zip_filename[:4])
            if start_year <= zip_year <= end_year:
                year_range_files.append(zip_path)

    file_count = len(year_range_files)

    if not quiet:
        print(f"Found {file_count} ZIP files in {zips_dir} for years {start_year}-{end_year}")

    return file_count > 0, file_count

def should_skip_download(zips_dir, start_year, end_year, force=False, verbose=False, quiet=False):
    """Determine if download step should be skipped."""
    if force:
        if not quiet:
            print("Force download requested, proceeding...")
        return False

    has_files, file_count = check_zips_directory_status(zips_dir, start_year, end_year, verbose, quiet)

    if has_files:
        if not quiet:
            print(f"ZIP files already present ({file_count} files), skipping download step")
        return True

    if not quiet:
        print("No ZIP files found, download required")
    return False

def should_skip_recompress(zips_dir, start_year, end_year, force=False, verbose=False, quiet=False):
    """Determine if recompress step should be skipped."""
    if force:
        if not quiet:
            print("Force recompress requested, proceeding...")
        return False

    has_files, file_count = check_zips_directory_status(zips_dir, start_year, end_year, verbose, quiet)

    if not has_files:
        if not quiet:
            print("No ZIP files to recompress, skipping recompress step")
        return True

    # Check if any files need recompression (this is a simple check)
    # In a real implementation, you might check file sizes or corruption
    if not quiet:
        print(f"ZIP files present ({file_count} files), checking if recompression needed...")

    # For now, assume recompression is needed if files exist
    # Could be enhanced to check file integrity
    return False

def get_index_paths(zips_dir):
    """Get the standard index file paths within the zips directory."""
    xml_index_file = os.path.join(zips_dir, 'xml_zip_index.json')
    ein_index_file = os.path.join(zips_dir, 'ein_xml_index.json')
    return xml_index_file, ein_index_file

def check_index_status(zips_dir, start_year, end_year, verbose=False, quiet=False):
    """Check if indexes exist and are up-to-date."""
    xml_index_file, ein_index_file = get_index_paths(zips_dir)

    xml_index_exists = os.path.exists(xml_index_file) and os.path.getsize(xml_index_file) > 0
    ein_index_exists = os.path.exists(ein_index_file) and os.path.getsize(ein_index_file) > 0

    if not quiet:
        if xml_index_exists:
            print(f"XML index found: {xml_index_file}")
        else:
            print(f"XML index not found or empty: {xml_index_file}")

        if ein_index_exists:
            print(f"EIN index found: {ein_index_file}")
        else:
            print(f"EIN index not found or empty: {ein_index_file}")

    # Get list of ZIP files in the directory
    import glob
    zip_pattern = os.path.join(zips_dir, "*.zip")
    zip_files = glob.glob(zip_pattern)

    if not zip_files:
        if not quiet:
            print("No ZIP files found in directory")
        return True, xml_index_exists, ein_index_exists

    # Check if indexes are newer than ZIP files
    if xml_index_exists:
        # Get the newest ZIP file modification time (only consider ZIPs in year range)
        year_range_zips = []
        for zip_path in zip_files:
            zip_filename = os.path.basename(zip_path)
            if zip_filename[:4].isdigit():
                zip_year = int(zip_filename[:4])
                if start_year <= zip_year <= end_year:
                    year_range_zips.append(zip_path)

        if year_range_zips:
            newest_zip_time = max(os.path.getmtime(zip_file) for zip_file in year_range_zips)
            index_time = os.path.getmtime(xml_index_file)

            if verbose and not quiet:
                print(f"DEBUG: Index timestamp: {index_time} ({os.path.getctime(xml_index_file)})")
                print(f"DEBUG: Newest ZIP timestamp in range: {newest_zip_time}")
                print(f"DEBUG: Year range ZIPs: {[os.path.basename(z) for z in year_range_zips]}")

            if index_time < newest_zip_time:
                if not quiet:
                    print("Index is older than some ZIP files in year range, rebuild recommended")
                    if verbose:
                        print(f"  Index time: {index_time}")
                        print(f"  Newest ZIP time: {newest_zip_time}")
                        newest_zip = max(year_range_zips, key=os.path.getmtime)
                        print(f"  Newest ZIP: {os.path.basename(newest_zip)}")
                return False, xml_index_exists, ein_index_exists
        else:
            if verbose and not quiet:
                print(f"DEBUG: No ZIP files found in year range {start_year}-{end_year}")

        # Validate that index contains entries for all current ZIP files
        try:
            with open(xml_index_file, 'r') as f:
                xml_index = json.load(f)

            # Get list of ZIP files that should be in the index (within year range)
            expected_zips = set()
            for zip_path in zip_files:
                zip_filename = os.path.basename(zip_path)
                if zip_filename[:4].isdigit():
                    zip_year = int(zip_filename[:4])
                    if start_year <= zip_year <= end_year:
                        expected_zips.add(zip_path)

            # Check if all expected ZIPs are in the index
            indexed_zips = set(xml_index.values())
            missing_zips = expected_zips - indexed_zips

            if verbose and not quiet:
                print(f"DEBUG: Expected ZIPs in range: {len(expected_zips)}")
                print(f"DEBUG: Indexed ZIPs total: {len(indexed_zips)}")
                print(f"DEBUG: Missing ZIPs: {len(missing_zips)}")
                if missing_zips:
                    print(f"DEBUG: Missing: {[os.path.basename(z) for z in missing_zips]}")

            if missing_zips:
                if not quiet:
                    print(f"Index is missing {len(missing_zips)} ZIP files, rebuild recommended")
                    if verbose:
                        for missing in missing_zips:
                            print(f"  Missing: {os.path.basename(missing)}")
                return False, xml_index_exists, ein_index_exists

        except (json.JSONDecodeError, KeyError) as e:
            if not quiet:
                print(f"Index file is corrupted or invalid: {e}, rebuild recommended")
            return False, xml_index_exists, ein_index_exists

    return True, xml_index_exists, ein_index_exists




# ===== BUILD INDEX FUNCTIONALITY =====


# ===== EXTRACT EIN FUNCTIONALITY =====



def create_parser():
    """Create a single consolidated argument parser with all possible arguments."""
    parser = argparse.ArgumentParser(
        description="IRS 990 Tools - Process IRS tax filings and charity data",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Run the complete pipeline
  python irs990tools.py --command run-all --start-year 2017 --end-year 2025 --zips-dir /Volumes/Data/irs_zips --final-dir /Volumes/Data/final

  # Download IRS ZIP files
  python irs990tools.py --command download --start-year 2017 --end-year 2025 --dest /Volumes/Data/irs_zips

  # Extract charity data
  python irs990tools.py --command extract-charities --start-year 2017 --end-year 2025 --input-dir /Volumes/Data/irs_zips --output-dir /Volumes/Data/tsvs
        """
    )

    # Required command argument
    parser.add_argument('--command', type=str, required=True,
                       choices=['download', 'recompress', 'extract-charities', 'analyze-charities',
                               'get-latest', 'extract-addresses', 'add-backfill', 'extract-grants',
                               'filter-charities', 'check-grants', 'run-all', 'run-from',
                               'extract-ein', 'build-index'],
                       help='Command to execute')

    # Add all possible argument groups
    add_common_args(parser)
    add_year_args(parser)
    add_directory_args(parser)
    add_processing_args(parser)
    add_pipeline_dirs(parser)

    # Force option for overriding optimizations
    parser.add_argument('--force', action='store_true', help='Force execution of all steps, ignoring optimizations')

    # Add command-specific arguments
    config = get_config()

    # Download-specific
    parser.add_argument('--dest', type=str, default='/Volumes/Data/irs_zips', help='Destination directory for downloads')

    # Extract-charities specific
    parser.add_argument('--input-dir', type=str, default='/Volumes/Data/irs_zips', help='Directory containing ZIP files')
    parser.add_argument('--batch-size', type=int, default=500, help='Batch size for processing')
    parser.add_argument('--write-buffer-size', type=int, default=10000, help='TSV write buffer size')
    parser.add_argument('--writer-threads', type=int, default=1, help='Number of TSV writer threads')

    # Analyze-charities specific
    parser.add_argument('--stop-year', type=int, default=2024, help='End year for analysis')

    # Get-latest and extract-grants specific
    parser.add_argument('--zip-dir', type=str, default='/Volumes/Data/irs_zips', help='Directory containing ZIP files')

    # Extract-addresses specific
    parser.add_argument('--sample-xml', type=str, help='Directory for sample XML files')
    parser.add_argument('--backfill-source', type=str, help='Path to backfill TSV file')
    parser.add_argument('--force-reprocess', action='store_true', help='Force reprocessing of cached data')

    # Add-backfill specific
    parser.add_argument('--charity-tsv', type=str, help='Path to charity TSV file')
    parser.add_argument('--backfill-tsv', type=str, help='Path to backfill TSV file')

    # Extract-grants specific
    parser.add_argument('--charity-source', type=str, help='Path to charity source file')

    # Filter-charities specific
    parser.add_argument('--input-file', type=str, help='Input TSV file')
    parser.add_argument('--output-file', type=str, help='Output TSV file')
    parser.add_argument('--filter-column', type=str, default='denominator', help='Column to filter on')
    parser.add_argument('--filter-value', type=float, default=1000000, help='Filter value')
    parser.add_argument('--columns', nargs='+', help='Columns to keep',
                       default=["tax_year", "org_type", "form_type", "total_assets", "denominator",
                               "xml_name", "filer_ein", "receipt_amt", "govt_amt", "contrib_amt", "filer_name"])
    parser.add_argument('--analysis-md', type=str, default='analysis.md', help='Output Markdown file for analysis')

    # Check-grants specific
    parser.add_argument('--index-file', type=str, help='Index file for validation')
    parser.add_argument('--report-file', type=str, help='Path for report file')

    # Run-from specific
    parser.add_argument('--start-step', type=str,
                       choices=['download', 'recompress', 'extract', 'analyze', 'latest', 'addresses', 'backfill', 'grants', 'check', 'copy', 'report'],
                       help='Step to start from')

    # Extract-ein specific
    parser.add_argument('--ein', type=str, help='EIN to extract files for')

    # Build-index specific
    parser.add_argument('--ein-index-file', type=str, help='Path for the EIN->XML index file (optional)')

    # Handle positional arguments that were in subparsers
    # For extract-ein command, EIN can be provided as positional or --ein
    parser.add_argument('positional_ein', nargs='?', type=str, help='EIN to extract files for (alternative to --ein)')

    # For run-from command, start_step can be provided as positional or --start-step
    parser.add_argument('positional_start_step', nargs='?', type=str,
                       choices=['download', 'recompress', 'extract', 'analyze', 'latest', 'addresses', 'backfill', 'grants', 'check', 'copy', 'report'],
                       help='Step to start from (alternative to --start-step)')

    return parser

def main():
    """Main entry point."""
    parser = create_parser()
    args = parser.parse_args()

    # Resolve directory arguments based on data-root
    args = resolve_directory_args(args)

    # Handle positional arguments for backward compatibility
    if args.command == 'extract-ein':
        if args.positional_ein and not args.ein:
            args.ein = args.positional_ein
        elif not args.ein and not args.positional_ein:
            parser.error("--ein is required for extract-ein command")
    elif args.command == 'run-from':
        if args.positional_start_step and not args.start_step:
            args.start_step = args.positional_start_step
        elif not args.start_step and not args.positional_start_step:
            parser.error("--start-step is required for run-from command")

    # For commands that need years, ensure they're provided
    year_required_commands = ['download', 'extract-charities', 'get-latest', 'extract-addresses', 'extract-grants', 'run-all']
    if args.command in year_required_commands:
        if not hasattr(args, 'start_year') or args.start_year is None:
            parser.error(f"--start-year is required for {args.command} command")
        if not hasattr(args, 'end_year') or args.end_year is None:
            parser.error(f"--end-year is required for {args.command} command")

    # Load configuration
    config_file = getattr(args, 'config', None)
    config = get_config(config_file)

    # Handle common arguments
    verbose = getattr(args, 'verbose', False)
    quiet = getattr(args, 'quiet', False)

    # Dispatch to appropriate command handler
    try:
        if args.command == 'download':
            download_irs_zips(args.start_year, args.end_year, args.dest, verbose=verbose, quiet=quiet)
        elif args.command == 'recompress':
            recompress_zips(zips_dir=args.zips_dir, verbose=verbose, quiet=quiet)
        elif args.command == 'extract-charities':
            extract_charities(
                start_year=args.start_year,
                end_year=args.end_year,
                input_dir=args.input_dir,
                output_dir=args.output_dir,
                verbose=verbose,
                quiet=quiet,
                write_buffer_size=args.write_buffer_size,
                worker_threads=args.worker_threads,
                batch_size=args.batch_size,
                writer_threads=args.writer_threads
            )
        elif args.command == 'analyze-charities':
            # Call analyze_charities with required arguments
            analyze_charities(
                input_dir=args.input_dir,
                output_dir=args.output_dir,
                start_year=args.start_year,
                stop_year=args.end_year,
                verbose=verbose,
                quiet=quiet
            )
        elif args.command == 'get-latest':
            # Call get_latest_filings with required arguments
            get_latest_filings(
                start_year=args.start_year,
                end_year=args.end_year,
                source_dir=args.source_dir,
                zip_dir=args.zip_dir,
                output_dir=args.output_dir,
                minimum_d=getattr(args, 'minimum_d', 0),
                org_types=getattr(args, 'org_types', 'all'),
                not_types=getattr(args, 'not_types', ''),
                worker_threads=getattr(args, 'worker_threads', 16),
                verbose=verbose,
                quiet=quiet
            )
        elif args.command == 'extract-addresses':
            # Call extract_addresses with required arguments
            extract_addresses(
                start_year=args.start_year,
                end_year=args.end_year,
                zip_dir=args.zip_dir,
                cache_dir=args.cache_dir,
                output_dir=args.output_dir,
                sample_xml=getattr(args, 'sample_xml', None),
                backfill_source=getattr(args, 'backfill_source', None),
                force_reprocess=getattr(args, 'force_reprocess', False),
                verbose=verbose,
                quiet=quiet
            )
        elif args.command == 'add-backfill':
            add_backfill(args.charity_tsv, args.backfill_tsv, args.output_dir, verbose=verbose, quiet=quiet)
        elif args.command == 'extract-grants':
            # Call extract_grants with required arguments
            extract_grants(
                start_year=args.start_year,
                end_year=args.end_year,
                zip_dir=args.zip_dir,
                cache_dir=args.cache_dir,
                output_dir=args.output_dir,
                charity_source=args.charity_source,
                minimum_d=getattr(args, 'minimum_d', 0),
                org_types=getattr(args, 'org_types', 'all'),
                not_types=getattr(args, 'not_types', ''),
                worker_threads=getattr(args, 'worker_threads', 16),
                verbose=verbose,
                quiet=quiet
            )
        elif args.command == 'filter-charities':
            filter_charities(args.input_file, args.output_file, args.filter_column,
                           args.filter_value, getattr(args, 'columns', None), getattr(args, 'analysis_md', 'analysis.md'),
                           verbose=verbose, quiet=quiet)
        elif args.command == 'check-grants':
            check_grants()
        elif args.command == 'run-all':
            run_all_pipeline(args)
        elif args.command == 'run-from':
            run_from_step(args)
        elif args.command == 'extract-ein':
            extract_ein_files(args)
        elif args.command == 'build-index':
            build_xml_index(args)
        else:
            print(f"Unknown command: {args.command}")
            parser.print_help()
    except ImportError as e:
        print(f"Error importing module: {e}")
        print("Make sure all required files are present in the 990tools directory")
    except Exception as e:
        print(f"Error running command '{args.command}': {e}")
        if verbose:
            import traceback
            traceback.print_exc()




if __name__ == '__main__':
    main()