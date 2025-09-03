"""
Data analysis and filtering related commands
"""

def analyze_charities(input_dir, output_dir, start_year, stop_year, verbose=False, quiet=False):
    """Analyze charity data and compute percentiles."""
    import analyze_charities
    import logging
    import sys

    # Set up logging
    if not quiet:
        if verbose:
            logging.getLogger().setLevel(logging.DEBUG)
        else:
            logging.getLogger().setLevel(logging.INFO)
    else:
        logging.getLogger().setLevel(logging.ERROR)

    # Call the analysis function directly
    analyze_charities.analyze_charities_main(
        input_dir=input_dir,
        output_dir=output_dir,
        start_year=start_year,
        stop_year=stop_year,
        verbose=verbose,
        quiet=quiet
    )

def get_latest_filings(start_year, end_year, source_dir, zip_dir, output_dir,
                      minimum_d=10_000_000, org_types='all', not_types='',
                      worker_threads=16, verbose=False, quiet=False):
    """Get latest filings for each charity."""
    import get_latest
    import logging
    import sys

    # Set up logging
    if not quiet:
        if verbose:
            logging.getLogger().setLevel(logging.DEBUG)
        else:
            logging.getLogger().setLevel(logging.INFO)
    else:
        logging.getLogger().setLevel(logging.ERROR)

    # Call the get_latest function directly
    get_latest.get_latest_main(
        start_year=start_year,
        end_year=end_year,
        source_dir=source_dir,
        zip_dir=zip_dir,
        output_dir=output_dir,
        minimum_d=minimum_d,
        org_types=org_types,
        not_types=not_types,
        worker_threads=worker_threads,
        verbose=verbose,
        quiet=quiet
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
    grant_check.check_grants_main(
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

    # Call the grant_report function directly
    grant_report.generate_report_main(
        input_file=input_file,
        report_file=report_file,
        verbose=verbose,
        quiet=quiet
    )