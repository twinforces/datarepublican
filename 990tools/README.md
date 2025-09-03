# IRS 990 Tools

A unified Python module for processing IRS 990 tax filings, extracting charity data, analyzing filings, and generating reports.

## Overview

This module transforms the collection of individual Python scripts into a cohesive, command-line tool that can process IRS 990 forms from download to final report generation. It provides both individual stage execution and complete pipeline orchestration.

## Features

- **Unified Interface**: Single command-line tool with consistent arguments
- **Modular Design**: Run individual stages or complete pipeline
- **Configuration Management**: JSON-based configuration with sensible defaults
- **Error Handling**: Proper exception handling and progress reporting
- **Extensible**: Easy to add new processing stages
- **Enhanced Data Extraction**: Contractors, consultants, political contributions, and canonical addresses
- **Google Street View Integration**: Canonical addresses for location-based analysis
- **PAC Data Linking**: EIN and name/address data for joining with political action committee data

## Installation

### Option 1: Install as Package
```bash
cd 990tools
pip install -e .
```

### Option 2: Run Directly
```bash
cd 990tools
python irs990tools.py --help
```

## Quick Start

### Run Complete Pipeline
```bash
python irs990tools.py run-all \
  --start-year 2017 \
  --end-year 2025 \
  --zips-dir /Volumes/Data/irs_zips \
  --final-dir /Volumes/Data/final \
  --worker-threads 16
```

### Individual Commands

#### Download IRS ZIP files
```bash
python irs990tools.py download \
  --start-year 2017 \
  --end-year 2025 \
  --dest /Volumes/Data/irs_zips
```

#### Extract charity data
```bash
python irs990tools.py extract-charities \
  --start-year 2017 \
  --end-year 2025 \
  --input-dir /Volumes/Data/irs_zips \
  --output-dir /Volumes/Data/tsvs \
  --worker-threads 16
```

#### Analyze charities
```bash
python irs990tools.py analyze-charities \
  --start-year 2017 \
  --stop-year 2025 \
  --input-dir /Volumes/Data/tsvs \
  --output-dir /Volumes/Data/atsvs
```

#### Get latest filings
```bash
python irs990tools.py get-latest \
  --start-year 2017 \
  --end-year 2025 \
  --source-dir /Volumes/Data/atsvs \
  --zip-dir /Volumes/Data/irs_zips \
  --output-dir /Volumes/Data/final \
  --minimum-d 0
```

#### Filter charities
```bash
python irs990tools.py filter-charities \
  --input-file /Volumes/Data/final/charity_latest.tsv \
  --output-file /Volumes/Data/final/charities_1M.tsv \
  --filter-column denominator \
  --filter-value 1000000
```

#### Check grants
```bash
python irs990tools.py check-grants \
  --index-file /Volumes/Data/final/charity_latest.tsv \
  --input-file /Volumes/Data/final/grants.tsv \
  --output-file /Volumes/Data/final/grants_final.tsv \
  --report-file /Volumes/Data/final/filter_report.md
```

#### Run pipeline from specific step
```bash
python irs990tools.py run-from extract \
  --start-year 2020 \
  --end-year 2023 \
  --zips-dir /Volumes/Data/irs_zips \
  --final-dir /Volumes/Data/final
```

#### Extract XML files for specific EIN
```bash
python irs990tools.py extract-ein 271414646 \
  --zips-dir /Volumes/Data/irs_zips \
  --output-dir ./ein_extracted \
  --index-file ./xml_index.json
```

#### Build XML to ZIP index
```bash
python irs990tools.py build-index \
  --zips-dir /Volumes/Data/irs_zips \
  --index-file ./xml_zip_index.json \
  --start-year 2017 \
  --end-year 2025
```

## Configuration

### Default Configuration
The module includes sensible defaults, but you can customize behavior with a configuration file:

```bash
# Generate default config
python -c "from config import save_default_config; save_default_config()"

# Use custom config
python irs990tools.py --config my_config.json run-all
```

### Configuration File Format
```json
{
  "directories": {
    "zips": "/Volumes/Data/irs_zips",
    "tsvs": "/Volumes/Data/tsvs",
    "analyzed": "/Volumes/Data/atsvs",
    "final": "/Volumes/Data/final",
    "browse": "/Volumes/Data/browse",
    "cache": "/Volumes/Data/cache"
  },
  "processing": {
    "start_year": 2017,
    "end_year": 2025,
    "minimum_d": 0,
    "worker_threads": 16,
    "batch_size": 500,
    "write_buffer_size": 10000,
    "writer_threads": 1
  },
  "filters": {
    "org_types": ["all"],
    "not_types": [],
    "filter_column": "denominator",
    "filter_value": 1000000
  }
}
```

## Pipeline Stages

1. **download** - Download IRS 990 ZIP files from the IRS website
2. **recompress** - Fix ZIP files with unsupported compression formats
3. **extract-charities** - Parse XML files and extract charity data to TSV
4. **analyze-charities** - Compute percentiles and generate analysis reports
5. **get-latest** - Select most recent filing for each charity EIN
6. **extract-addresses** - Extract address data for charity matching
7. **add-backfill** - Add backfill data for unknown EINs
8. **extract-grants** - Extract grant data from IRS filings
9. **filter-charities** - Filter charities by specified criteria
10. **check-grants** - Validate and filter grant data
11. **run-all** - Execute complete pipeline with dependency management

### New Development Commands

12. **run-from** - Start pipeline from any specific step (accelerates development/testing)
13. **extract-ein** - Extract all XML files for a specific EIN across all ZIP files
14. **build-index** - Build XML→ZIP index for fast EIN-based lookups

## Command Line Options

### Global Options
- `--verbose` - Enable verbose output
- `--quiet` - Suppress non-error output
- `--config FILE` - Path to configuration file
- `--help` - Show help message

### Common Arguments
- `--start-year YEAR` - Start year for processing
- `--end-year YEAR` - End year for processing
- `--input-dir DIR` - Input directory
- `--output-dir DIR` - Output directory
- `--worker-threads N` - Number of worker threads

## Output Files

The pipeline generates several output files:

- `charity_latest.tsv/csv` - Latest charity filings (now includes canonical_address column)
- `grants_latest.tsv/csv` - Grant data
- `backfill.tsv/csv` - Backfill data for unknown EINs
- `charity_latest_with_backfill.tsv/csv` - Charities with backfill data
- `grants_final.tsv/csv` - Final filtered grant data
- `contractors.tsv` - Contractors and consultants data (filer_ein, name, amount, ein, tax_year)
- `political_contributions.tsv` - Political contributions data (filer_ein, recipient, amount, tax_year)
- Various analysis reports and logs

## New Data Extraction Features

### Contractors and Consultants
Extracts data from Schedule L including:
- **EIN**: If available in the XML
- **Name**: Business name or person name
- **Amount**: Transaction/compensation amount
- **Relationship Type**: Business relationship, loans, business transactions

### Political Contributions
Extracts data from Schedule C including:
- **Recipient**: Who received the contribution
- **Amount**: Contribution amount
- **Purpose**: Political campaign activity details

### Canonical Addresses
- **Full Address**: Standardized address format for Google Street View
- **Address Components**: Parsed street, city, state, ZIP
- **Geocoding Ready**: Formatted for location services integration

### PAC Data Linking
All extracted data includes:
- **EIN**: For direct matching with PAC databases
- **Name/Address**: For fuzzy matching when EIN is unavailable
- **Tax Year**: For temporal analysis
- **Filer EIN**: Links back to the charity organization

## Development and Debugging Features

### Run Pipeline from Specific Step
The `run-from` command allows you to start the processing pipeline from any step, which is perfect for:
- **Accelerated Development**: Skip already completed steps
- **Targeted Testing**: Test specific pipeline stages
- **Error Recovery**: Resume from failed step without redoing everything

```bash
# Start from extraction (skip download/recompress)
python irs990tools.py run-from extract --start-year 2022 --end-year 2023

# Start from analysis (skip earlier steps)
python irs990tools.py run-from analyze --start-year 2022 --end-year 2023
```

### Extract EIN Files
The `extract-ein` command extracts all XML files for a specific EIN across all ZIP files:
- **Targeted Analysis**: Focus on specific organizations
- **Debugging**: Examine filings for particular charities
- **Performance**: Uses optional XML→ZIP index for fast lookups

```bash
# Extract all CHAI filings
python irs990tools.py extract-ein 271414646 --output-dir ./chai_filings

# Use existing index for faster extraction
python irs990tools.py extract-ein 271414646 --index-file ./xml_index.json
```

### Build XML Index
The `build-index` command creates a mapping of XML files to their containing ZIP files:
- **Performance Optimization**: Eliminates full ZIP scanning
- **Fast Lookups**: Enables quick EIN-based file location
- **Reusable**: Save index for multiple extractions

```bash
# Build comprehensive index
python irs990tools.py build-index --index-file ./xml_zip_index.json

# Build index for specific year range
python irs990tools.py build-index --start-year 2020 --end-year 2023 --index-file ./recent_index.json
```

## Dependencies

- Python 3.7+
- requests
- beautifulsoup4
- lxml
- pandas
- tqdm
- mako
- psutil

## Architecture

### Module Structure
```
990tools/
├── irs990tools.py      # Main CLI entry point
├── config.py           # Configuration management
├── xpaths.py           # Merged XPath definitions
├── extract_utils.py    # Shared utility functions
├── setup.py            # Package installation
└── [individual scripts] # Refactored processing modules
```

### Key Improvements

1. **Unified Interface**: Single command with subcommands vs. multiple scripts
2. **Consistent Arguments**: Standardized parameter naming and types
3. **Configuration System**: JSON-based config with environment-specific settings
4. **Error Handling**: Comprehensive error reporting and recovery
5. **Modularity**: Individual stages can be run independently for debugging
6. **Maintainability**: Consolidated common code, eliminated duplication

## Migration from Individual Scripts

### Before (Old Approach)
```bash
# Run 15+ separate commands
export ZIPS_DIR="/Volumes/Data/irs_zips"
export OUT_DIR="/Volumes/Data/tsvs"
export ANAL_DIR="/Volumes/Data/atsvs"
export FINAL_DIR="/Volumes/Data/final"

python download_IRS_990_zips.py 2017 2025 --dest $ZIPS_DIR
python extract_charities.py 2017 2025 --input-dir $ZIPS_DIR --output-dir $OUT_DIR
python analyze_charities.py --start-year 2017 --stop-year 2025 --input-dir $OUT_DIR --output-dir $ANAL_DIR
# ... 12 more commands
```

### After (New Approach)
```bash
# Single command with all enhancements
python irs990tools.py run-all \
  --start-year 2017 \
  --end-year 2025 \
  --zips-dir /Volumes/Data/irs_zips \
  --final-dir /Volumes/Data/final \
  --worker-threads 16
```

## Development

### Adding New Commands
1. Add subparser in `irs990tools.py`
2. Implement command handler function
3. Update `run-all` pipeline if needed
4. Add to configuration if new parameters needed

### Testing
```bash
# Test individual commands
python irs990tools.py download --help
python irs990tools.py extract-charities --start-year 2020 --end-year 2020 --input-dir ./test_zips --output-dir ./test_output

# Test pipeline
python irs990tools.py run-all --start-year 2020 --end-year 2020 [other options]
```

## Troubleshooting

### Common Issues

1. **Import Errors**: Ensure all dependencies are installed
2. **Path Issues**: Use absolute paths or ensure relative paths are correct
3. **Memory Issues**: Reduce worker threads or batch sizes
4. **Permission Errors**: Ensure write access to output directories

### Debug Mode
```bash
python irs990tools.py --verbose run-all [options]
```

## Contributing

1. Follow existing code patterns
2. Add comprehensive error handling
3. Update documentation
4. Test with sample data
5. Update configuration if new parameters added

## License

This project is part of the Data Republican toolkit for nonprofit data analysis.

---

For questions or issues, please refer to the individual script documentation or check the error logs for detailed information.
