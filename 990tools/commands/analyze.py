"""
Data analysis and filtering related commands
"""

def analyze_charities(args):
    """Analyze charity data and compute percentiles."""
    import analyze_charities
    import logging
    import sys
    import os
    import glob

    # Extract arguments from args object
    # For analyze step, prioritize tsvs_dir over input_dir since we need TSV files, not ZIP files
    input_dir = args.tsvs_dir if hasattr(args, 'tsvs_dir') and args.tsvs_dir else getattr(args, 'input_dir', args.tsvs_dir)
    # Analyze step should output to analyzed_dir, not output_dir
    output_dir = args.analyzed_dir if hasattr(args, 'analyzed_dir') and args.analyzed_dir else getattr(args, 'output_dir', args.analyzed_dir)
    start_year = args.start_year
    stop_year = args.end_year
    verbose = args.verbose
    quiet = args.quiet


    # Set up logging
    if not quiet:
        if verbose:
            logging.getLogger().setLevel(logging.DEBUG)
        else:
            logging.getLogger().setLevel(logging.INFO)
    else:
        logging.getLogger().setLevel(logging.ERROR)

    # Call the analysis function with arguments
    analyze_charities.main(
        input_dir=input_dir,
        output_dir=output_dir,
        start_year=start_year,
        stop_year=stop_year,
        verbose=verbose,
        quiet=quiet
    )

def get_latest_filings(args):
    """Get latest filings for each charity."""
    import get_latest
    import logging
    import sys
    from io import StringIO

    # Set up logging
    if not args.quiet:
        if args.verbose:
            logging.getLogger().setLevel(logging.DEBUG)
        else:
            logging.getLogger().setLevel(logging.INFO)
    else:
        logging.getLogger().setLevel(logging.ERROR)

    # Prepare arguments for get_latest.main() which expects positional args
    # We need to temporarily replace sys.argv to make argparse work
    original_argv = sys.argv
    try:
        sys.argv = [
            'get_latest.py',
            str(args.start_year),
            str(args.end_year),
            '--source-dir', args.analyzed_dir,  # Use analyzed_dir where analyze step outputs files
            '--zip-dir', getattr(args, 'zip_dir', args.zips_dir),
            '--output-dir', getattr(args, 'output_dir', args.final_dir),
            '--minimumD', str(getattr(args, 'minimum_d', 0)),
            '--orgTypes', 'all',
            '--NOTTypes', '',
            '--worker-threads', str(getattr(args, 'worker_threads', 16))
        ]

        if args.verbose:
            sys.argv.append('--verbose')
        if args.quiet:
            sys.argv.append('--quiet')

        # Call the get_latest function
        get_latest.main()

    finally:
        # Restore original sys.argv
        sys.argv = original_argv

def extract_addresses(args):
    """Extract address data for charity matching."""
    import extract_addresses

    # Call the extract_addresses function with arguments
    extract_addresses.main(
        start_year=args.start_year,
        end_year=args.end_year,
        zip_dir=getattr(args, 'zip_dir', args.zips_dir),
        cache_dir=getattr(args, 'cache_dir', args.cache_dir),
        output_dir=getattr(args, 'output_dir', args.final_dir),
        sample_xml=getattr(args, 'sample_xml', None),
        backfill_source=getattr(args, 'backfill_source', None),
        force_reprocess=getattr(args, 'force_reprocess', False),
        verbose=args.verbose,
        quiet=args.quiet
    )

def extract_grants(args):
    """Extract grant data from IRS filings."""
    import extract_grants

    # Call the extract_grants function with arguments
    extract_grants.main(
        start_year=args.start_year,
        end_year=args.end_year,
        zip_dir=getattr(args, 'zip_dir', args.zips_dir),
        output_dir=getattr(args, 'output_dir', args.final_dir),
        minimum_d=getattr(args, 'minimum_d', 0),
        org_types='all',
        not_types='',
        worker_threads=getattr(args, 'worker_threads', 16),
        verbose=args.verbose,
        quiet=args.quiet
    )

def filter_charities(input_file, output_file, filter_column='denominator', filter_value=1000000,
                    columns=None, analysis_md='analysis.md', verbose=False, quiet=False):
    """Filter charities by criteria."""
    from charity_filter import main as filter_main
    return filter_main(input_file, output_file, filter_column, filter_value, columns, analysis_md, verbose=verbose, quiet=quiet)

def check_grants(index_file, input_file, output_file, report_file=None, report_depth=1000,
                verbose=False, quiet=False):
    """Check and filter grant data."""
    import grant_check
    import logging

    # Set up logging
    if not quiet:
        if verbose:
            logging.getLogger().setLevel(logging.DEBUG)
        else:
            logging.getLogger().setLevel(logging.INFO)
    else:
        logging.getLogger().setLevel(logging.ERROR)

    # Call the grant_check function directly
    grant_check.main(
        index_file=index_file,
        input_file=input_file,
        output_file=output_file,
        report_file=report_file,
        report_depth=report_depth,
        verbose=verbose,
        quiet=quiet
    )

def generate_grant_report(input_file, report_file='report.md', verbose=False, quiet=False):
    """Generate grant reports."""
    import grant_report
    import logging

    # Set up logging
    if not quiet:
        if verbose:
            logging.getLogger().setLevel(logging.DEBUG)
        else:
            logging.getLogger().setLevel(logging.INFO)
    else:
        logging.getLogger().setLevel(logging.ERROR)

    # Call the grant_report function with arguments
    grant_report.main(
        input_file=input_file,
        report_file=report_file,
        verbose=verbose,
        quiet=quiet
    )