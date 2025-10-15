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


def calculate_percentiles(args):
    """Calculate percentiles for charity financial metrics by org_type and tax_year."""
    import calculate_percentiles
    import logging

    # Set up logging
    if not args.quiet:
        if args.verbose:
            logging.getLogger().setLevel(logging.DEBUG)
        else:
            logging.getLogger().setLevel(logging.INFO)
    else:
        logging.getLogger().setLevel(logging.ERROR)

    # Call the calculate_percentiles function
    calculate_percentiles.main(db_path=getattr(args, 'db_path', "/Volumes/Data/final/irs990.db"))