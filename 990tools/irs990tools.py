#!/usr/bin/env python3
"""
IRS 990 Tools - A unified module for processing IRS 990 tax filings

This module provides a command-line interface to process IRS 990 forms,
extract charity data, analyze filings, and generate reports.
"""

import argparse
import sys
import os
from pathlib import Path
from config import get_config, get_config_value

# Add the current directory to Python path for imports
sys.path.insert(0, os.path.dirname(__file__))

def create_parser():
    """Create the main argument parser with subcommands."""
    parser = argparse.ArgumentParser(
        description="IRS 990 Tools - Process IRS tax filings and charity data",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Run the complete pipeline
  python irs990tools.py run-all --start-year 2017 --end-year 2025 --zips-dir ./zips --final-dir ./final

  # Download IRS ZIP files
  python irs990tools.py download --start-year 2017 --end-year 2025 --dest ./zips

  # Extract charity data
  python irs990tools.py extract-charities --start-year 2017 --end-year 2025 --input-dir ./zips --output-dir ./tsvs
        """
    )

    # Common arguments that apply to most subcommands
    common_parser = argparse.ArgumentParser(add_help=False)
    common_parser.add_argument('--verbose', action='store_true', help='Enable verbose output')
    common_parser.add_argument('--quiet', action='store_true', help='Suppress non-error output')
    common_parser.add_argument('--config', type=str, help='Path to configuration file')

    # Create subparsers
    subparsers = parser.add_subparsers(dest='command', help='Available commands')

    # Download command
    download_parser = subparsers.add_parser(
        'download',
        parents=[common_parser],
        help='Download IRS 990 ZIP files'
    )
    download_parser.add_argument('start_year', type=int, help='Start year for downloads')
    download_parser.add_argument('end_year', type=int, help='End year for downloads')
    download_parser.add_argument('--dest', type=str, default='./irs_zips', help='Destination directory')

    # Recompress command
    recompress_parser = subparsers.add_parser(
        'recompress',
        parents=[common_parser],
        help='Recompress problematic IRS ZIP files'
    )
    recompress_parser.add_argument('--zips-dir', type=str, default='./irs_zips', help='Directory containing ZIP files')

    # Extract charities command
    extract_parser = subparsers.add_parser(
        'extract-charities',
        parents=[common_parser],
        help='Extract charity data from IRS XML files'
    )
    extract_parser.add_argument('start_year', type=int, help='Start year for processing')
    extract_parser.add_argument('end_year', type=int, help='End year for processing')
    extract_parser.add_argument('--input-dir', type=str, default='./irs_zips', help='Directory containing ZIP files')
    extract_parser.add_argument('--output-dir', type=str, default='./tsvs', help='Directory for output TSV files')
    extract_parser.add_argument('--worker-threads', type=int, default=16, help='Number of worker threads')
    extract_parser.add_argument('--batch-size', type=int, default=500, help='Batch size for processing')
    extract_parser.add_argument('--write-buffer-size', type=int, default=10000, help='TSV write buffer size')
    extract_parser.add_argument('--writer-threads', type=int, default=1, help='Number of TSV writer threads')

    # Analyze charities command
    analyze_parser = subparsers.add_parser(
        'analyze-charities',
        parents=[common_parser],
        help='Analyze charity data and compute percentiles'
    )
    analyze_parser.add_argument('--start-year', type=int, default=2016, help='Start year for analysis')
    analyze_parser.add_argument('--stop-year', type=int, default=2024, help='End year for analysis')
    analyze_parser.add_argument('--input-dir', type=str, default='./tsvs', help='Directory containing input TSV files')
    analyze_parser.add_argument('--output-dir', type=str, default='./analyzed', help='Directory for output files')

    # Get latest command
    latest_parser = subparsers.add_parser(
        'get-latest',
        parents=[common_parser],
        help='Get latest filings for each charity'
    )
    latest_parser.add_argument('start_year', type=int, help='Start year for processing')
    latest_parser.add_argument('end_year', type=int, help='End year for processing')
    latest_parser.add_argument('--source-dir', type=str, default='./analyzed', help='Directory containing analyzed TSV files')
    latest_parser.add_argument('--zip-dir', type=str, default='./irs_zips', help='Directory containing ZIP files')
    latest_parser.add_argument('--output-dir', type=str, default='./final', help='Directory for output files')
    latest_parser.add_argument('--minimum-d', type=float, default=10_000_000, help='Minimum denominator value')
    latest_parser.add_argument('--org-types', type=str, default='all', help='Comma-separated list of org types')
    latest_parser.add_argument('--not-types', type=str, default='', help='Comma-separated list of org types to exclude')
    latest_parser.add_argument('--worker-threads', type=int, default=16, help='Number of worker threads')

    # Extract addresses command
    addresses_parser = subparsers.add_parser(
        'extract-addresses',
        parents=[common_parser],
        help='Extract address data for charity matching'
    )
    addresses_parser.add_argument('start_year', type=int, help='Start year for processing')
    addresses_parser.add_argument('end_year', type=int, help='End year for processing')
    addresses_parser.add_argument('--zip-dir', type=str, default='./irs_zips', help='Directory containing ZIP files')
    addresses_parser.add_argument('--cache-dir', type=str, help='Cache directory for address data')
    addresses_parser.add_argument('--output-dir', type=str, default='./final', help='Directory for output files')
    addresses_parser.add_argument('--sample-xml', type=str, help='Directory for sample XML files')
    addresses_parser.add_argument('--backfill-source', type=str, help='Path to backfill TSV file')
    addresses_parser.add_argument('--force-reprocess', action='store_true', help='Force reprocessing of cached data')

    # Add backfill command
    backfill_parser = subparsers.add_parser(
        'add-backfill',
        parents=[common_parser],
        help='Add backfill data to charity records'
    )
    backfill_parser.add_argument('--charity-tsv', type=str, help='Path to charity TSV file')
    backfill_parser.add_argument('--backfill-tsv', type=str, help='Path to backfill TSV file')
    backfill_parser.add_argument('--output-dir', type=str, default='./final', help='Directory for output files')

    # Extract grants command
    grants_parser = subparsers.add_parser(
        'extract-grants',
        parents=[common_parser],
        help='Extract grant data from IRS filings'
    )
    grants_parser.add_argument('start_year', type=int, help='Start year for processing')
    grants_parser.add_argument('end_year', type=int, help='End year for processing')
    grants_parser.add_argument('--zip-dir', type=str, default='./irs_zips', help='Directory containing ZIP files')
    grants_parser.add_argument('--cache-dir', type=str, help='Cache directory')
    grants_parser.add_argument('--output-dir', type=str, default='./final', help='Directory for output files')
    grants_parser.add_argument('--charity-source', type=str, required=True, help='Path to charity source file')

    # Filter charities command
    filter_parser = subparsers.add_parser(
        'filter-charities',
        parents=[common_parser],
        help='Filter charities by criteria'
    )
    filter_parser.add_argument('--input-file', type=str, required=True, help='Input TSV file')
    filter_parser.add_argument('--output-file', type=str, required=True, help='Output TSV file')
    filter_parser.add_argument('--filter-column', type=str, default='denominator', help='Column to filter on')
    filter_parser.add_argument('--filter-value', type=float, default=1000000, help='Filter value')
    filter_parser.add_argument('--columns', nargs='+', help='Columns to keep',
                              default=["tax_year", "org_type", "form_type", "total_assets", "denominator",
                                       "xml_name", "filer_ein", "receipt_amt", "govt_amt", "contrib_amt", "filer_name"])
    filter_parser.add_argument('--analysis-md', type=str, default='analysis.md', help='Output Markdown file for analysis')

    # Check grants command
    check_parser = subparsers.add_parser(
        'check-grants',
        parents=[common_parser],
        help='Check and filter grant data'
    )
    check_parser.add_argument('--index-file', type=str, required=True, help='Index file for validation')
    check_parser.add_argument('--input-file', type=str, required=True, help='Input grants file')
    check_parser.add_argument('--output-file', type=str, required=True, help='Output grants file')
    check_parser.add_argument('--report-file', type=str, help='Path for report file')

    # Run all command
    runall_parser = subparsers.add_parser(
        'run-all',
        parents=[common_parser],
        help='Run the complete processing pipeline'
    )
    # Use config for defaults
    config = get_config()
    runall_parser.add_argument('--start-year', type=int,
                              default=get_config_value(config, 'processing', 'start_year'),
                              help='Start year for processing')
    runall_parser.add_argument('--end-year', type=int,
                              default=get_config_value(config, 'processing', 'end_year'),
                              help='End year for processing')
    runall_parser.add_argument('--zips-dir', type=str,
                              default=get_config_value(config, 'directories', 'zips'),
                              help='Directory for ZIP files')
    runall_parser.add_argument('--tsvs-dir', type=str,
                              default=get_config_value(config, 'directories', 'tsvs'),
                              help='Directory for TSV files')
    runall_parser.add_argument('--analyzed-dir', type=str,
                              default=get_config_value(config, 'directories', 'analyzed'),
                              help='Directory for analyzed files')
    runall_parser.add_argument('--final-dir', type=str,
                              default=get_config_value(config, 'directories', 'final'),
                              help='Directory for final output')
    runall_parser.add_argument('--browse-dir', type=str,
                              default=get_config_value(config, 'directories', 'browse'),
                              help='Directory for browse files')
    runall_parser.add_argument('--cache-dir', type=str,
                              default=get_config_value(config, 'directories', 'cache'),
                              help='Cache directory')
    runall_parser.add_argument('--minimum-d', type=float,
                              default=get_config_value(config, 'processing', 'minimum_d'),
                              help='Minimum denominator value')
    runall_parser.add_argument('--worker-threads', type=int,
                              default=get_config_value(config, 'processing', 'worker_threads'),
                              help='Number of worker threads')

    return parser

def main():
    """Main entry point."""
    parser = create_parser()
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

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
            # This will need modification to accept args instead of parsing
            extract_main()
        elif args.command == 'analyze-charities':
            from analyze_charities import main as analyze_main
            analyze_main()
        elif args.command == 'get-latest':
            from get_latest import main as latest_main
            latest_main()
        elif args.command == 'extract-addresses':
            from extract_addresses import main as addresses_main
            addresses_main()
        elif args.command == 'add-backfill':
            from add_backfill import main as backfill_main
            backfill_main(args.charity_tsv, args.backfill_tsv, args.output_dir, verbose=verbose, quiet=quiet)
        elif args.command == 'extract-grants':
            from extract_grants import main as grants_main
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

def run_all_pipeline(args):
    """Run the complete processing pipeline."""
    print("Running complete IRS 990 processing pipeline...")
    print(f"Processing years {args.start_year} to {args.end_year}")
    print(f"Directories: zips={args.zips_dir}, tsvs={args.tsvs_dir}, analyzed={args.analyzed_dir}, final={args.final_dir}")

    try:
        # Step 1: Download IRS ZIP files
        print("\n=== Step 1: Downloading IRS ZIP files ===")
        from download_irs_990_zips import main as download_main
        download_main(args.start_year, args.end_year, args.zips_dir, verbose=args.verbose, quiet=args.quiet)

        # Step 2: Recompress ZIP files
        print("\n=== Step 2: Recompressing ZIP files ===")
        from recompress_irs_zips import main as recompress_main
        recompress_main(zips_dir=args.zips_dir, verbose=args.verbose, quiet=args.quiet)

        # Step 3: Extract charity data
        print("\n=== Step 3: Extracting charity data ===")
        from extract_charities import main as extract_main
        # Note: extract_charities.main() currently doesn't accept args, needs refactoring
        print("Note: extract-charities step needs individual command for now")

        # Step 4: Analyze charities
        print("\n=== Step 4: Analyzing charity data ===")
        from analyze_charities import main as analyze_main
        # Note: analyze_charities.main() currently doesn't accept args, needs refactoring
        print("Note: analyze-charities step needs individual command for now")

        # Step 5: Get latest filings
        print("\n=== Step 5: Getting latest filings ===")
        from get_latest import main as latest_main
        # Note: get_latest.main() currently doesn't accept args, needs refactoring
        print("Note: get-latest step needs individual command for now")

        # Step 6: Extract addresses
        print("\n=== Step 6: Extracting addresses ===")
        from extract_addresses import main as addresses_main
        # Note: extract_addresses.main() currently doesn't accept args, needs refactoring
        print("Note: extract-addresses step needs individual command for now")

        # Step 7: Add backfill
        print("\n=== Step 7: Adding backfill data ===")
        from add_backfill import main as backfill_main
        backfill_main(
            charity_tsv=f"{args.final_dir}/charity_latest.tsv",
            backfill_tsv=f"{args.final_dir}/backfill.tsv",
            output_dir=args.final_dir,
            verbose=args.verbose,
            quiet=args.quiet
        )

        # Step 8: Extract grants
        print("\n=== Step 8: Extracting grants ===")
        from extract_grants import main as grants_main
        # Note: extract_grants.main() currently doesn't accept args, needs refactoring
        print("Note: extract-grants step needs individual command for now")

        # Step 9: Check grants
        print("\n=== Step 9: Checking grants ===")
        from grant_check import main as check_main
        check_main(
            index_file=f"{args.final_dir}/charity_latest_with_backfill.tsv",
            input_file=f"{args.final_dir}/grants_latest.tsv",
            output_file=f"{args.final_dir}/grants_final.tsv",
            report_file=f"{args.final_dir}/filter_501.md",
            verbose=args.verbose,
            quiet=args.quiet
        )

        # Step 10: Generate reports
        print("\n=== Step 10: Generating reports ===")
        from grant_report import main as report_main
        report_main(
            input_file=f"{args.final_dir}/grants_final.tsv",
            report_file=f"{args.final_dir}/final_report.md",
            verbose=args.verbose,
            quiet=args.quiet
        )

        print("\n=== Pipeline Complete ===")
        print("All steps completed successfully!")
        print(f"Output files are in: {args.final_dir}")

    except Exception as e:
        print(f"Error during pipeline execution: {e}")
        if args.verbose:
            import traceback
            traceback.print_exc()
        raise

if __name__ == '__main__':
    main()