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
import logging
import zipfile
import glob
import threading
import queue
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from tqdm import tqdm
from collections import defaultdict
import re
from lxml import etree
from io import BytesIO
import csv
import time
import psutil
from countryCodes import lookupCC

# Import configuration
from config import get_config, get_config_value

# Import utility modules
import extract_utils as cu
from parse_utils import parse_grants, parse_contributions

# Import our modular command functions
from utils.args import add_common_args, add_year_args, add_directory_args, add_processing_args, add_pipeline_dirs
from commands.download import download_irs_zips, recompress_zips
from commands.extract import extract_charities, extract_addresses, extract_grants, add_backfill
from commands.analyze import analyze_charities, get_latest_filings, filter_charities, check_grants, generate_grant_report
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

    # Load configuration
    config_file = getattr(args, 'config', None)
    config = get_config(config_file)

    # Handle common arguments
    verbose = getattr(args, 'verbose', False)
    quiet = getattr(args, 'quiet', False)

    # Dispatch to appropriate command handler
    try:
        if args.command == 'download':
            from download_irs_990_zips import main as download_main
            download_main(args.start_year, args.end_year, args.dest, verbose=verbose, quiet=quiet)
        elif args.command == 'recompress':
            from recompress_irs_zips import main as recompress_main
            recompress_main(zips_dir=args.zips_dir, verbose=verbose, quiet=quiet)
        elif args.command == 'extract-charities':
            from extract_charities import main as extract_main
            extract_main(
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
            from analyze_charities import main as analyze_main
            # analyze_charities.main() handles its own argument parsing
            sys.argv = ['analyze_charities.py',
                       '--input-dir', args.input_dir,
                       '--output-dir', args.output_dir,
                       '--start-year', str(args.start_year),
                       '--stop-year', str(args.end_year)]
            analyze_main()
        elif args.command == 'get-latest':
            from get_latest import main as latest_main
            # get_latest.main() handles its own argument parsing
            sys.argv = ['get_latest.py', str(args.start_year), str(args.end_year),
                       '--source-dir', args.source_dir,
                       '--zip-dir', args.zip_dir,
                       '--output-dir', args.output_dir,
                       '--minimumD', str(args.minimum_d),
                       '--orgTypes', args.org_types,
                       '--NOTTypes', args.not_types,
                       '--worker-threads', str(args.worker_threads)]
            if verbose:
                sys.argv.append('--verbose')
            if quiet:
                sys.argv.append('--quiet')
            latest_main()
        elif args.command == 'extract-addresses':
            from extract_addresses import main as addresses_main
            # extract_addresses.main() handles its own argument parsing
            sys.argv = ['extract_addresses.py', str(args.start_year), str(args.end_year),
                       '--zip-dir', args.zip_dir,
                       '--cache-dir', args.cache_dir,
                       '--output-dir', args.output_dir]
            if verbose:
                sys.argv.append('--verbose')
            if quiet:
                sys.argv.append('--quiet')
            addresses_main()
        elif args.command == 'add-backfill':
            from add_backfill import main as backfill_main
            backfill_main(args.charity_tsv, args.backfill_tsv, args.output_dir, verbose=verbose, quiet=quiet)
        elif args.command == 'extract-grants':
            from extract_grants import main as grants_main
            # extract_grants.main() handles its own argument parsing
            sys.argv = ['extract_grants.py', str(args.start_year), str(args.end_year),
                       '--source-dir', args.source_dir,
                       '--zip-dir', args.zip_dir,
                       '--output-dir', args.output_dir,
                       '--minimumD', str(args.minimum_d),
                       '--orgTypes', args.org_types,
                       '--NOTTypes', args.not_types,
                       '--worker-threads', str(args.worker_threads)]
            if verbose:
                sys.argv.append('--verbose')
            if quiet:
                sys.argv.append('--quiet')
            grants_main()
        elif args.command == 'filter-charities':
            from charity_filter import main as filter_main
            filter_main(args.input_file, args.output_file, args.filter_column,
                       args.filter_value, getattr(args, 'columns', None), getattr(args, 'analysis_md', 'analysis.md'),
                       verbose=verbose, quiet=quiet)
        elif args.command == 'check-grants':
            from grant_check import main as check_main
            check_main()
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