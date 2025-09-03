"""
Download and compression related commands
"""

def download_irs_zips(start_year, end_year, dest_dir, verbose=False, quiet=False):
    """Download IRS 990 ZIP files for the specified year range."""
    from download_irs_990_zips import main as download_main
    return download_main(start_year, end_year, dest_dir, verbose=verbose, quiet=quiet)

def recompress_zips(zips_dir, verbose=False, quiet=False):
    """Recompress problematic IRS ZIP files."""
    from recompress_irs_zips import main as recompress_main
    return recompress_main(zips_dir=zips_dir, verbose=verbose, quiet=quiet)