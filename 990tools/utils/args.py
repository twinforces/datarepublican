"""
Argument parsing utilities for IRS 990 Tools
"""

def add_common_args(parser):
    """Add common arguments to a parser."""
    parser.add_argument('--verbose', action='store_true', help='Enable verbose output')
    parser.add_argument('--quiet', action='store_true', help='Suppress non-error output')
    parser.add_argument('--config', type=str, help='Path to configuration file')
    return parser

def add_year_args(parser):
    """Add year range arguments to a parser."""
    parser.add_argument('--start-year', type=int, help='Start year for processing')
    parser.add_argument('--end-year', type=int, help='End year for processing')
    return parser

def add_directory_args(parser):
    """Add common directory arguments to a parser."""
    parser.add_argument('--data-root', type=str, default='/Volumes/Data', help='Root directory for all data')
    parser.add_argument('--zips-dir', type=str, help='Directory containing ZIP files (default: {data-root}/irs_zips)')
    parser.add_argument('--output-dir', type=str, help='Directory for output files (default: {data-root}/final)')
    parser.add_argument('--source-dir', type=str, help='Directory containing source files (default: {data-root}/atsvs)')
    return parser

def add_processing_args(parser):
    """Add processing-related arguments to a parser."""
    parser.add_argument('--minimum-d', type=float, default=0, help='Minimum denominator value (default: 0 for all organizations)')
    parser.add_argument('--org-types', type=str, default='all', help='Comma-separated list of org types')
    parser.add_argument('--not-types', type=str, default='', help='Comma-separated list of org types to exclude')
    parser.add_argument('--worker-threads', type=int, default=16, help='Number of worker threads')
    return parser

def add_pipeline_dirs(parser):
    """Add pipeline directory arguments to a parser."""
    parser.add_argument('--tsvs-dir', type=str, help='Directory for TSV files (default: {data-root}/tsvs)')
    parser.add_argument('--analyzed-dir', type=str, help='Directory for analyzed files (default: {data-root}/atsvs)')
    parser.add_argument('--final-dir', type=str, help='Directory for final output (default: {data-root}/final)')
    parser.add_argument('--cache-dir', type=str, help='Cache directory (default: {data-root}/atsvs/_cache)')
    parser.add_argument('--browse-dir', type=str, help='Directory for browse files (default: {data-root}/../browse)')
    return parser

def resolve_directory_args(args):
    """Resolve directory arguments based on data-root."""
    import os

    # Get data root
    data_root = getattr(args, 'data_root', '/Volumes/Data')

    # Resolve directory paths
    if not hasattr(args, 'zips_dir') or args.zips_dir is None:
        args.zips_dir = os.path.join(data_root, 'irs_zips')
    if not hasattr(args, 'tsvs_dir') or args.tsvs_dir is None:
        args.tsvs_dir = os.path.join(data_root, 'tsvs')
    if not hasattr(args, 'analyzed_dir') or args.analyzed_dir is None:
        args.analyzed_dir = os.path.join(data_root, 'atsvs')
    if not hasattr(args, 'final_dir') or args.final_dir is None:
        args.final_dir = os.path.join(data_root, 'final')
    if not hasattr(args, 'cache_dir') or args.cache_dir is None:
        args.cache_dir = os.path.join(data_root, 'atsvs', '_cache')
    if not hasattr(args, 'browse_dir') or args.browse_dir is None:
        args.browse_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), '..', 'browse')

    # Also resolve other directory args for backward compatibility
    if not hasattr(args, 'output_dir') or args.output_dir is None:
        args.output_dir = args.final_dir
    if not hasattr(args, 'source_dir') or args.source_dir is None:
        args.source_dir = args.analyzed_dir

    return args

def find_file_path(base_dirs, filename, description="file"):
    """Find a file in multiple possible directories, returning the first match."""
    import os

    for base_dir in base_dirs:
        file_path = os.path.join(base_dir, filename)
        if os.path.isfile(file_path):
            return file_path

    # If not found, return the most likely path for error messages
    return os.path.join(base_dirs[0], filename) if base_dirs else filename