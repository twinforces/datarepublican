"""
Data extraction related commands
"""

def extract_charities(start_year, end_year, input_dir, output_dir, verbose=False, quiet=False,
                     worker_threads=16, batch_size=500, write_buffer_size=10000, writer_threads=1):
    """Extract charity data from IRS XML files."""
    from extract_charities import main as extract_main
    return extract_main(
        start_year=start_year,
        end_year=end_year,
        input_dir=input_dir,
        output_dir=output_dir,
        verbose=verbose,
        quiet=quiet,
        write_buffer_size=write_buffer_size,
        worker_threads=worker_threads,
        batch_size=batch_size,
        writer_threads=writer_threads
    )

def extract_addresses(args):
    """Extract address data for charity matching."""
    import extract_addresses

    # Extract arguments from args object
    start_year = args.start_year
    end_year = args.end_year
    zip_dir = getattr(args, 'zip_dir', args.zips_dir)
    cache_dir = getattr(args, 'cache_dir', args.cache_dir)
    output_dir = getattr(args, 'output_dir', args.final_dir)
    sample_xml = getattr(args, 'sample_xml', None)
    backfill_source = getattr(args, 'backfill_source', None)
    force_reprocess = getattr(args, 'force_reprocess', False)
    verbose = args.verbose
    quiet = args.quiet

    extract_addresses.main(
        start_year=start_year,
        end_year=end_year,
        zip_dir=zip_dir,
        cache_dir=cache_dir,
        output_dir=output_dir,
        sample_xml=sample_xml,
        backfill_source=backfill_source,
        force_reprocess=force_reprocess,
        verbose=verbose,
        quiet=quiet
    )

def extract_grants(args):
    """Extract grant data from IRS filings."""
    import extract_grants

    # Extract arguments from args object
    start_year = args.start_year
    end_year = args.end_year
    zip_dir = getattr(args, 'zip_dir', args.zips_dir)
    cache_dir = getattr(args, 'cache_dir', args.cache_dir)
    output_dir = getattr(args, 'output_dir', args.final_dir)
    charity_source = f"{args.final_dir}/charity_latest_with_backfill.tsv"
    minimum_d = getattr(args, 'minimum_d', 0)
    org_types = 'all'
    not_types = ''
    worker_threads = getattr(args, 'worker_threads', 16)
    verbose = args.verbose
    quiet = args.quiet

    extract_grants.main(
        start_year=start_year,
        end_year=end_year,
        zip_dir=zip_dir,
        cache_dir=cache_dir,
        output_dir=output_dir,
        charity_source=charity_source,
        minimum_d=minimum_d,
        org_types=org_types,
        not_types=not_types,
        worker_threads=worker_threads,
        verbose=verbose,
        quiet=quiet
    )

def add_backfill(charity_tsv, backfill_tsv, output_dir, verbose=False, quiet=False):
    """Add backfill data to charity records."""
    from add_backfill import main as backfill_main
    return backfill_main(charity_tsv, backfill_tsv, output_dir, verbose=verbose, quiet=quiet)