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
    parser.add_argument('start_year', type=int, help='Start year for processing')
    parser.add_argument('end_year', type=int, help='End year for processing')
    return parser

def add_directory_args(parser):
    """Add common directory arguments to a parser."""
    parser.add_argument('--zips-dir', type=str, default='/Volumes/Data/irs_zips', help='Directory containing ZIP files')
    parser.add_argument('--output-dir', type=str, default='/Volumes/Data/final', help='Directory for output files')
    parser.add_argument('--source-dir', type=str, default='/Volumes/Data/atsvs', help='Directory containing source files')
    return parser

def add_processing_args(parser):
    """Add processing-related arguments to a parser."""
    parser.add_argument('--minimum-d', type=float, default=10_000_000, help='Minimum denominator value')
    parser.add_argument('--org-types', type=str, default='all', help='Comma-separated list of org types')
    parser.add_argument('--not-types', type=str, default='', help='Comma-separated list of org types to exclude')
    parser.add_argument('--worker-threads', type=int, default=16, help='Number of worker threads')
    return parser

def add_pipeline_dirs(parser):
    """Add pipeline directory arguments to a parser."""
    parser.add_argument('--tsvs-dir', type=str, default='/Volumes/Data/tsvs', help='Directory for TSV files')
    parser.add_argument('--analyzed-dir', type=str, default='/Volumes/Data/atsvs', help='Directory for analyzed files')
    parser.add_argument('--final-dir', type=str, default='/Volumes/Data/final', help='Directory for final output')
    parser.add_argument('--cache-dir', type=str, default='/Volumes/Data/atsvs/_cache', help='Cache directory')
    parser.add_argument('--browse-dir', type=str, default='../browse', help='Directory for browse files')
    return parser