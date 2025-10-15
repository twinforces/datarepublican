"""
Pipeline execution commands
"""

from pipeline_db import PipelineDatabaseManager


def bootstrap_pipeline_progress(args):
    """Bootstrap pipeline progress from existing completed steps."""
    print("Bootstrapping pipeline progress tracking...")

    db_manager = PipelineDatabaseManager()

    # Define the pipeline step order
    pipeline_steps = [
        'download', 'recompress', 'extract', 'analyze', 'latest',
        'addresses', 'geocoding', 'backfill', 'grants', 'check', 'percentiles', 'copy', 'report'
    ]

    # Check which steps have been completed by looking for output files
    completed_steps = []

    # Check for download completion (ZIP files exist)
    import os
    import glob
    zip_files = glob.glob(os.path.join(args.zips_dir, "*.zip"))
    if zip_files:
        completed_steps.append('download')
        completed_steps.append('recompress')  # Assume recompress is done if zips exist

    # Check for extract completion (charity files exist)
    charity_files = glob.glob(os.path.join(args.tsvs_dir, "charities_*.tsv"))
    if charity_files:
        completed_steps.append('extract')

    # Check for analyze completion
    analyzed_files = glob.glob(os.path.join(args.analyzed_dir, "*.tsv"))
    if analyzed_files:
        completed_steps.append('analyze')

    # Check for latest filings
    latest_files = glob.glob(os.path.join(args.analyzed_dir, "*latest*.tsv"))
    if latest_files:
        completed_steps.append('latest')

    # Check for addresses
    address_files = glob.glob(os.path.join(args.final_dir, "*addresses*.tsv"))
    if address_files:
        completed_steps.append('addresses')

    # Check for geocoding completion (check if geocoding has been run)
    # For now, assume geocoding is done if addresses exist and we have a database
    import sqlite3
    db_path = "/Volumes/Data/final/pipeline_progress.db"
    try:
        with sqlite3.connect(db_path) as conn:
            cursor = conn.execute("SELECT COUNT(*) FROM Geocoding WHERE geocoding_status = 'success'")
            geocoded_count = cursor.fetchone()[0]
            if geocoded_count > 0:
                completed_steps.append('geocoding')
    except:
        pass  # Database might not exist or have tables

    # Check for backfill
    backfill_files = glob.glob(os.path.join(args.final_dir, "*backfill*.tsv"))
    if backfill_files:
        completed_steps.append('backfill')

    # Check for grants
    grant_files = glob.glob(os.path.join(args.final_dir, "grants*.tsv"))
    if grant_files:
        completed_steps.append('grants')

    # Check for check completion (final grants file)
    final_grants = os.path.join(args.final_dir, "grants_final.tsv")
    if os.path.exists(final_grants):
        completed_steps.append('check')

    # Check for percentiles completion (check if percentiles have been calculated)
    # For now, assume percentiles are done if check is done and we have a database
    import sqlite3
    db_path = "/Volumes/Data/final/irs990.db"
    try:
        with sqlite3.connect(db_path) as conn:
            cursor = conn.execute("SELECT COUNT(*) FROM Charities WHERE comp_ptile IS NOT NULL LIMIT 1")
            percentile_count = cursor.fetchone()[0]
            if percentile_count > 0:
                completed_steps.append('percentiles')
    except:
        pass  # Database might not exist or have tables

    # Check for copy completion (files in final dir)
    contractors_file = os.path.join(args.final_dir, "contractors.tsv")
    if os.path.exists(contractors_file):
        completed_steps.append('copy')

    # Check for report completion
    report_file = os.path.join(args.final_dir, "final_report.md")
    if os.path.exists(report_file):
        completed_steps.append('report')

    # Bootstrap the database
    db_manager.bootstrap_from_existing(args.start_year, args.end_year, completed_steps)

    print(f"Bootstrapped progress for {len(completed_steps)} completed steps: {', '.join(completed_steps)}")
    return db_manager


def resume_pipeline(args):
    """Resume an interrupted pipeline from the last completed step."""
    print("Resuming pipeline from last completed step...")

    db_manager = PipelineDatabaseManager()

    # Get the resume point
    resume_step = db_manager.get_resume_point(args.start_year, args.end_year)

    if resume_step is None:
        print("Pipeline is already complete!")
        return

    print(f"Resuming from step: {resume_step}")

    # Map step names to function calls
    step_functions = {
        'download': lambda: run_from_step_with_args(args, 'download'),
        'recompress': lambda: run_from_step_with_args(args, 'recompress'),
        'extract': lambda: run_from_step_with_args(args, 'extract'),
        'analyze': lambda: run_from_step_with_args(args, 'analyze'),
        'latest': lambda: run_from_step_with_args(args, 'latest'),
        'addresses': lambda: run_from_step_with_args(args, 'addresses'),
        'geocoding': lambda: run_from_step_with_args(args, 'geocoding'),
        'backfill': lambda: run_from_step_with_args(args, 'backfill'),
        'grants': lambda: run_from_step_with_args(args, 'grants'),
        'check': lambda: run_from_step_with_args(args, 'check'),
        'percentiles': lambda: run_from_step_with_args(args, 'percentiles'),
        'copy': lambda: run_from_step_with_args(args, 'copy'),
        'report': lambda: run_from_step_with_args(args, 'report')
    }

    # Resume from the appropriate step
    if resume_step in step_functions:
        step_functions[resume_step]()
    else:
        print(f"Unknown resume step: {resume_step}")


def run_from_step_with_args(args, start_step):
    """Helper function to run from step with modified args."""
    # Create a copy of args with the start_step set
    args_copy = args
    args_copy.start_step = start_step
    run_from_step(args_copy)

def run_all_pipeline(args):
    """Run the complete processing pipeline."""
    print("Running complete IRS 990 processing pipeline...")
    print(f"Processing years {args.start_year} to {args.end_year}")
    print(f"Directories: zips={args.zips_dir}, tsvs={args.tsvs_dir}, analyzed={args.analyzed_dir}, final={args.final_dir}")

    # Initialize progress tracking
    db_manager = PipelineDatabaseManager()

    try:
        # Import optimization functions
        from irs990tools import should_skip_download, should_skip_recompress, check_index_status

        # Step 1: Download IRS ZIP files (always check website for new files)
        print("\n=== Step 1: Downloading IRS ZIP files ===")
        progress = db_manager.start_step('download', args.start_year, args.end_year)
        try:
            from commands.download import download_irs_zips
            download_irs_zips(args.start_year, args.end_year, args.zips_dir, verbose=args.verbose, quiet=args.quiet)
            db_manager.complete_step('download', args.start_year, args.end_year)
        except Exception as e:
            db_manager.fail_step('download', args.start_year, args.end_year, str(e))
            raise

        # Step 2: Recompress ZIP files (skip if not needed)
        print("\n=== Step 2: Recompressing ZIP files ===")
        progress = db_manager.start_step('recompress', args.start_year, args.end_year)
        try:
            skip_recompress = should_skip_recompress(args.zips_dir, args.start_year, args.end_year,
                                                    force=getattr(args, 'force', False),
                                                    verbose=args.verbose, quiet=args.quiet)

            if not skip_recompress:
                from commands.download import recompress_zips
                recompress_zips(zips_dir=args.zips_dir, verbose=args.verbose, quiet=args.quiet)
            else:
                print("Skipping recompress step - no recompression needed")
            db_manager.complete_step('recompress', args.start_year, args.end_year)
        except Exception as e:
            db_manager.fail_step('recompress', args.start_year, args.end_year, str(e))
            raise

        # Step 2.5: Check and build indexes if needed
        print("\n=== Step 2.5: Checking indexes ===")
        indexes_up_to_date, xml_exists, ein_exists = check_index_status(args.zips_dir, args.start_year, args.end_year,
                                                                       verbose=args.verbose, quiet=args.quiet)

        if not indexes_up_to_date or not xml_exists:
            print("Building/updating XML and EIN indexes...")
            from commands.utilities import build_xml_index
            build_xml_index(args.zips_dir, start_year=args.start_year, end_year=args.end_year,
                          verbose=args.verbose, quiet=args.quiet)
        else:
            print("Indexes are up-to-date, skipping rebuild")

        # Step 3: Extract charity data
        print("\n=== Step 3: Extracting charity data ===")
        progress = db_manager.start_step('extract', args.start_year, args.end_year)
        try:
            # Diagnostic: Check ZIP files before extraction
            import glob
            import os
            zip_files = glob.glob(os.path.join(args.zips_dir, "*.zip"))
            print(f"DEBUG: Found {len(zip_files)} ZIP files in {args.zips_dir}")
            if zip_files:
                print(f"DEBUG: Sample ZIP files: {zip_files[:3]}")
            else:
                print(f"DEBUG: No ZIP files found in {args.zips_dir}")
            from commands.extract import extract_charities
            extract_charities(
                start_year=args.start_year,
                end_year=args.end_year,
                input_dir=args.zips_dir,
                output_dir=args.tsvs_dir,
                verbose=args.verbose,
                quiet=args.quiet,
                worker_threads=args.worker_threads
            )
            db_manager.complete_step('extract', args.start_year, args.end_year)
        except Exception as e:
            db_manager.fail_step('extract', args.start_year, args.end_year, str(e))
            raise

        # Step 4: Analyze charities
        print("\n=== Step 4: Analyzing charity data ===")
        progress = db_manager.start_step('analyze', args.start_year, args.end_year)
        try:
            from commands.analyze import analyze_charities
            analyze_charities(args)
            db_manager.complete_step('analyze', args.start_year, args.end_year)
        except Exception as e:
            db_manager.fail_step('analyze', args.start_year, args.end_year, str(e))
            raise

        # Step 5: Get latest filings
        print("\n=== Step 5: Getting latest filings ===")
        progress = db_manager.start_step('latest', args.start_year, args.end_year)
        try:
            from commands.analyze import get_latest_filings
            get_latest_filings(args)
            db_manager.complete_step('latest', args.start_year, args.end_year)
        except Exception as e:
            db_manager.fail_step('latest', args.start_year, args.end_year, str(e))
            raise

        # Step 6: Extract addresses
        print("\n=== Step 6: Extracting addresses ===")
        progress = db_manager.start_step('addresses', args.start_year, args.end_year)
        try:
            from commands.extract import extract_addresses
            extract_addresses(args)
            db_manager.complete_step('addresses', args.start_year, args.end_year)
        except Exception as e:
            db_manager.fail_step('addresses', args.start_year, args.end_year, str(e))
            raise

        # Step 6.5: Process geocoding for addresses
        print("\n=== Step 6.5: Processing geocoding ===")
        progress = db_manager.start_step('geocoding', args.start_year, args.end_year)
        try:
            from geocoding_db import process_database_geocoding_threaded
            stats = process_database_geocoding_threaded(num_threads=4, batch_size=5000)
            print(f"Geocoding completed: {stats}")
            db_manager.complete_step('geocoding', args.start_year, args.end_year)
        except Exception as e:
            db_manager.fail_step('geocoding', args.start_year, args.end_year, str(e))
            raise

        # Step 7: Add backfill
        print("\n=== Step 7: Adding backfill data ===")
        progress = db_manager.start_step('backfill', args.start_year, args.end_year)
        try:
            from commands.extract import add_backfill
            import os
            from utils.args import find_file_path

            # Find the charity_latest.tsv file
            possible_dirs = [args.final_dir, args.analyzed_dir, args.tsvs_dir, args.data_root]
            charity_file = find_file_path(possible_dirs, "charity_latest.tsv", "charity file")
            backfill_file = find_file_path(possible_dirs, "backfill.tsv", "backfill file")

            print(f"Looking for charity_latest.tsv, found: {charity_file}")
            print(f"Looking for backfill.tsv, found: {backfill_file}")

            add_backfill(
                charity_tsv=charity_file,
                backfill_tsv=backfill_file,
                output_dir=args.final_dir,
                verbose=args.verbose,
                quiet=args.quiet
            )
            db_manager.complete_step('backfill', args.start_year, args.end_year)
        except Exception as e:
            db_manager.fail_step('backfill', args.start_year, args.end_year, str(e))
            raise

        # Step 8: Extract grants
        print("\n=== Step 8: Extracting grants ===")
        progress = db_manager.start_step('grants', args.start_year, args.end_year)
        try:
            from commands.extract import extract_grants
            extract_grants(args)
            db_manager.complete_step('grants', args.start_year, args.end_year)
        except Exception as e:
            db_manager.fail_step('grants', args.start_year, args.end_year, str(e))
            raise

        # Step 9: Check grants
        print("\n=== Step 9: Checking grants ===")
        progress = db_manager.start_step('check', args.start_year, args.end_year)
        try:
            from commands.analyze import check_grants
            from utils.args import find_file_path
            import os

            # Find the required files
            possible_dirs = [args.final_dir, args.analyzed_dir, args.tsvs_dir, args.data_root]
            index_file = find_file_path(possible_dirs, "charity_latest_with_backfill.tsv", "charity index file")
            input_file = find_file_path(possible_dirs, "grants_latest.tsv", "grants input file")

            print(f"Looking for charity_latest_with_backfill.tsv, found: {index_file}")
            print(f"Looking for grants_latest.tsv, found: {input_file}")

            check_grants(
                index_file=index_file,
                input_file=input_file,
                output_file=f"{args.final_dir}/grants_final.tsv",
                report_file=f"{args.final_dir}/filter_501.md",
                verbose=args.verbose,
                quiet=args.quiet
            )
            db_manager.complete_step('check', args.start_year, args.end_year)
        except Exception as e:
            db_manager.fail_step('check', args.start_year, args.end_year, str(e))
            raise

        # Step 9.5: Calculate percentiles
        print("\n=== Step 9.5: Calculating percentiles ===")
        progress = db_manager.start_step('percentiles', args.start_year, args.end_year)
        try:
            from commands.analyze import calculate_percentiles
            calculate_percentiles(args)
            db_manager.complete_step('percentiles', args.start_year, args.end_year)
        except Exception as e:
            db_manager.fail_step('percentiles', args.start_year, args.end_year, str(e))
            raise

        # Step 10: Calculate percentiles
        print("\n=== Step 10: Calculating percentiles ===")
        progress = db_manager.start_step('percentiles', args.start_year, args.end_year)
        try:
            from commands.analyze import calculate_percentiles
            calculate_percentiles(args)
            db_manager.complete_step('percentiles', args.start_year, args.end_year)
        except Exception as e:
            db_manager.fail_step('percentiles', args.start_year, args.end_year, str(e))
            raise

        # Step 11: Generate final TSV files from database
        print("\n=== Step 11: Generating final TSV files from database ===")
        progress = db_manager.start_step('copy', args.start_year, args.end_year)
        try:
            from generate_final_tsvs import TSVGenerator

            # Generate all final TSV files from database queries
            generator = TSVGenerator(db_path="/Volumes/Data/final/pipeline_progress.db")
            results = generator.generate_all_tsvs(args.final_dir)

            print("Generated final TSV files:")
            for filename, count in results.items():
                print(f"  {filename}: {count} rows")

            db_manager.complete_step('copy', args.start_year, args.end_year)
        except Exception as e:
            db_manager.fail_step('copy', args.start_year, args.end_year, str(e))
            raise

        # Step 12: Generate reports
        print("\n=== Step 12: Generating reports ===")
        progress = db_manager.start_step('report', args.start_year, args.end_year)
        try:
            from commands.analyze import generate_grant_report
            from utils.args import find_file_path

            # Find the grants_final.tsv file
            possible_dirs = [args.final_dir, args.analyzed_dir, args.tsvs_dir, args.data_root]
            input_file = find_file_path(possible_dirs, "grants_final.tsv", "grants final file")

            print(f"Looking for grants_final.tsv, found: {input_file}")

            generate_grant_report(
                input_file=input_file,
                report_file=f"{args.final_dir}/final_report.md",
                verbose=args.verbose,
                quiet=args.quiet
            )
            db_manager.complete_step('report', args.start_year, args.end_year)
        except Exception as e:
            db_manager.fail_step('report', args.start_year, args.end_year, str(e))
            raise

        # Step 12: Copy files to browse directory
        print("\n=== Step 12: Copying files to browse directory ===")
        import shutil
        import os

        # Copy grants_final.tsv to browse directory
        grants_src = os.path.join(args.final_dir, "grants_final.tsv")
        grants_dst = os.path.join(args.browse_dir, "grants.tsv")
        if os.path.exists(grants_src):
            shutil.copy2(grants_src, grants_dst)
            print(f"Copied grants_final.tsv to {args.browse_dir}/grants.tsv")
        else:
            print("grants_final.tsv not found, skipping...")

        # Copy grants_pf.tsv to browse directory
        grants_pf_src = os.path.join(args.final_dir, "grants_pf.tsv")
        grants_pf_dst = os.path.join(args.browse_dir, "grants.pf.tsv")
        if os.path.exists(grants_pf_src):
            shutil.copy2(grants_pf_src, grants_pf_dst)
            print(f"Copied grants_pf.tsv to {args.browse_dir}/grants.pf.tsv")
        else:
            print("grants_pf.tsv not found, skipping...")

        # Copy charity_latest.tsv to browse directory
        charity_src = os.path.join(args.final_dir, "charity_latest.tsv")
        charity_dst = os.path.join(args.browse_dir, "charities.tsv")
        if os.path.exists(charity_src):
            shutil.copy2(charity_src, charity_dst)
            print(f"Copied charity_latest.tsv to {args.browse_dir}/charities.tsv")
        else:
            print("charity_latest.tsv not found, skipping...")

        # Copy contractors.tsv to browse directory if it exists
        contractors_src = os.path.join(args.final_dir, "contractors.tsv")
        contractors_dst = os.path.join(args.browse_dir, "contractors.tsv")
        if os.path.exists(contractors_src):
            shutil.copy2(contractors_src, contractors_dst)
            print(f"Copied contractors.tsv to {args.browse_dir}")
        else:
            print("contractors.tsv not found, skipping...")

        # Copy political_contributions.tsv to browse directory if it exists
        political_src = os.path.join(args.final_dir, "political_contributions.tsv")
        political_dst = os.path.join(args.browse_dir, "political_contributions.tsv")
        if os.path.exists(political_src):
            shutil.copy2(political_src, political_dst)
            print(f"Copied political_contributions.tsv to {args.browse_dir}")
        else:
            print("political_contributions.tsv not found, skipping...")

        print("\n=== Pipeline Complete ===")
        print("All steps completed successfully!")
        print(f"Output files are in: {args.final_dir}")
        print(f"Browse files are in: {args.browse_dir}")

        # Print progress summary
        summary = db_manager.get_progress_summary(args.start_year, args.end_year)
        print(f"\nProgress Summary: {summary['completed_steps']}/{summary['total_steps']} steps completed")

    except Exception as e:
        print(f"Error during pipeline execution: {e}")
        if args.verbose:
            import traceback
            traceback.print_exc()
        raise

def run_from_step(args):
    """Run the processing pipeline starting from a specific step."""
    print(f"Running IRS 990 processing pipeline starting from step: {args.start_step}")
    print(f"Processing years {args.start_year} to {args.end_year}")

    # Initialize progress tracking
    db_manager = PipelineDatabaseManager()

    steps = {
        'download': 1,
        'recompress': 2,
        'extract': 3,
        'analyze': 4,
        'latest': 5,
        'addresses': 6,
        'geocoding': 7,
        'backfill': 8,
        'grants': 9,
        'check': 10,
        'percentiles': 11,
        'copy': 12,
        'report': 13
    }

    start_step_num = steps.get(args.start_step, 1)

    try:
        # Import optimization functions
        from irs990tools import should_skip_download, should_skip_recompress, check_index_status

        # Step 1: Download IRS ZIP files (if starting from here, always check website)
        if start_step_num <= 1:
            print("\n=== Step 1: Downloading IRS ZIP files ===")
            progress = db_manager.start_step('download', args.start_year, args.end_year)
            try:
                from commands.download import download_irs_zips
                download_irs_zips(args.start_year, args.end_year, args.zips_dir, verbose=args.verbose, quiet=args.quiet)
                db_manager.complete_step('download', args.start_year, args.end_year)
            except Exception as e:
                db_manager.fail_step('download', args.start_year, args.end_year, str(e))
                raise

        # Step 2: Recompress ZIP files (if starting from here)
        if start_step_num <= 2:
            print("\n=== Step 2: Recompressing ZIP files ===")
            progress = db_manager.start_step('recompress', args.start_year, args.end_year)
            try:
                skip_recompress = should_skip_recompress(args.zips_dir, args.start_year, args.end_year,
                                                        force=getattr(args, 'force', False),
                                                        verbose=args.verbose, quiet=args.quiet)

                if not skip_recompress:
                    from commands.download import recompress_zips
                    recompress_zips(zips_dir=args.zips_dir, verbose=args.verbose, quiet=args.quiet)
                else:
                    print("Skipping recompress step - no recompression needed")
                db_manager.complete_step('recompress', args.start_year, args.end_year)
            except Exception as e:
                db_manager.fail_step('recompress', args.start_year, args.end_year, str(e))
                raise

        # Step 2.5: Check and build indexes if needed (if starting from extract or earlier)
        if start_step_num <= 3:
            print("\n=== Step 2.5: Checking indexes ===")
            indexes_up_to_date, xml_exists, ein_exists = check_index_status(args.zips_dir, args.start_year, args.end_year,
                                                                           verbose=args.verbose, quiet=args.quiet)

            if not indexes_up_to_date or not xml_exists:
                print("Building/updating XML and EIN indexes...")
                from commands.utilities import build_xml_index
                build_xml_index(args.zips_dir, start_year=args.start_year, end_year=args.end_year,
                              verbose=args.verbose, quiet=args.quiet)
            else:
                print("Indexes are up-to-date, skipping rebuild")

        # Step 3: Extract charity data (if starting from here)
        if start_step_num <= 3:
            print("\n=== Step 3: Extracting charity data ===")
            progress = db_manager.start_step('extract', args.start_year, args.end_year)
            try:
                from commands.extract import extract_charities
                extract_charities(
                    start_year=args.start_year,
                    end_year=args.end_year,
                    input_dir=args.zips_dir,
                    output_dir=args.tsvs_dir,
                    verbose=args.verbose,
                    quiet=args.quiet,
                    worker_threads=args.worker_threads
                )
                db_manager.complete_step('extract', args.start_year, args.end_year)
            except Exception as e:
                db_manager.fail_step('extract', args.start_year, args.end_year, str(e))
                raise

        # Step 4: Analyze charities (if starting from here)
        if start_step_num <= 4:
            print("\n=== Step 4: Analyzing charity data ===")
            progress = db_manager.start_step('analyze', args.start_year, args.end_year)
            try:
                from commands.analyze import analyze_charities
                analyze_charities(args)
                db_manager.complete_step('analyze', args.start_year, args.end_year)
            except Exception as e:
                db_manager.fail_step('analyze', args.start_year, args.end_year, str(e))
                print(f"Error in analyze step: {e}")
                if args.verbose:
                    import traceback
                    traceback.print_exc()
                raise

        # Step 5: Get latest filings (if starting from here)
        if start_step_num <= 5:
            print("\n=== Step 5: Getting latest filings ===")
            progress = db_manager.start_step('latest', args.start_year, args.end_year)
            try:
                from commands.analyze import get_latest_filings
                get_latest_filings(args)
                db_manager.complete_step('latest', args.start_year, args.end_year)
            except Exception as e:
                db_manager.fail_step('latest', args.start_year, args.end_year, str(e))
                print(f"Error in get-latest step: {e}")
                if args.verbose:
                    import traceback
                    traceback.print_exc()
                raise

        # Step 6: Extract addresses (if starting from here)
        if start_step_num <= 6:
            print("\n=== Step 6: Extracting addresses ===")
            progress = db_manager.start_step('addresses', args.start_year, args.end_year)
            try:
                from commands.extract import extract_addresses
                extract_addresses(args)
                db_manager.complete_step('addresses', args.start_year, args.end_year)
            except Exception as e:
                db_manager.fail_step('addresses', args.start_year, args.end_year, str(e))
                print(f"Error in extract-addresses step: {e}")
                if args.verbose:
                    import traceback
                    traceback.print_exc()
                raise

        # Step 7: Process geocoding (if starting from here)
        if start_step_num <= 7:
            print("\n=== Step 7: Processing geocoding ===")
            progress = db_manager.start_step('geocoding', args.start_year, args.end_year)
            try:
                from geocoding_db import process_database_geocoding_threaded
                stats = process_database_geocoding_threaded(num_threads=4, batch_size=5000)
                print(f"Geocoding completed: {stats}")
                db_manager.complete_step('geocoding', args.start_year, args.end_year)
            except Exception as e:
                db_manager.fail_step('geocoding', args.start_year, args.end_year, str(e))
                print(f"Error in geocoding step: {e}")
                if args.verbose:
                    import traceback
                    traceback.print_exc()
                raise

        # Step 8: Add backfill (if starting from here)
        if start_step_num <= 8:
            print("\n=== Step 8: Adding backfill data ===")
            progress = db_manager.start_step('backfill', args.start_year, args.end_year)
            try:
                from commands.extract import add_backfill
                import os
                from utils.args import find_file_path

                # Find the charity_latest.tsv file
                possible_dirs = [args.final_dir, args.analyzed_dir, args.tsvs_dir, args.data_root]
                charity_file = find_file_path(possible_dirs, "charity_latest.tsv", "charity file")
                backfill_file = find_file_path(possible_dirs, "backfill.tsv", "backfill file")

                print(f"Looking for charity_latest.tsv, found: {charity_file}")
                print(f"Looking for backfill.tsv, found: {backfill_file}")

                add_backfill(
                    charity_tsv=charity_file,
                    backfill_tsv=backfill_file,
                    output_dir=args.final_dir,
                    verbose=args.verbose,
                    quiet=args.quiet
                )
                db_manager.complete_step('backfill', args.start_year, args.end_year)
            except Exception as e:
                db_manager.fail_step('backfill', args.start_year, args.end_year, str(e))
                print(f"Error in add-backfill step: {e}")
                if args.verbose:
                    import traceback
                    traceback.print_exc()
                raise

        # Step 9: Extract grants (if starting from here)
        if start_step_num <= 9:
            print("\n=== Step 8: Extracting grants ===")
            progress = db_manager.start_step('grants', args.start_year, args.end_year)
            try:
                from commands.extract import extract_grants
                extract_grants(args)
                db_manager.complete_step('grants', args.start_year, args.end_year)
            except Exception as e:
                db_manager.fail_step('grants', args.start_year, args.end_year, str(e))
                print(f"Error in extract-grants step: {e}")
                if args.verbose:
                    import traceback
                    traceback.print_exc()
                raise

        # Step 9: Check grants (if starting from here)
        if start_step_num <= 9:
            print("\n=== Step 9: Checking grants ===")
            progress = db_manager.start_step('check', args.start_year, args.end_year)
            try:
                from commands.analyze import check_grants
                from utils.args import find_file_path
                import os

                # Find the required files
                possible_dirs = [args.final_dir, args.analyzed_dir, args.tsvs_dir, args.data_root]
                index_file = find_file_path(possible_dirs, "charity_latest_with_backfill.tsv", "charity index file")
                input_file = find_file_path(possible_dirs, "grants_latest.tsv", "grants input file")

                print(f"Looking for charity_latest_with_backfill.tsv, found: {index_file}")
                print(f"Looking for grants_latest.tsv, found: {input_file}")

                check_grants(
                    index_file=index_file,
                    input_file=input_file,
                    output_file=f"{args.final_dir}/grants_final.tsv",
                    report_file=f"{args.final_dir}/filter_501.md",
                    verbose=args.verbose,
                    quiet=args.quiet
                )
                db_manager.complete_step('check', args.start_year, args.end_year)
            except Exception as e:
                db_manager.fail_step('check', args.start_year, args.end_year, str(e))
                raise

        # Step 10: Calculate percentiles (if starting from here)
        if start_step_num <= 10:
            print("\n=== Step 10: Calculating percentiles ===")
            progress = db_manager.start_step('percentiles', args.start_year, args.end_year)
            try:
                from commands.analyze import calculate_percentiles
                calculate_percentiles(args)
                db_manager.complete_step('percentiles', args.start_year, args.end_year)
            except Exception as e:
                db_manager.fail_step('percentiles', args.start_year, args.end_year, str(e))
                print(f"Error in percentiles step: {e}")
                if args.verbose:
                    import traceback
                    traceback.print_exc()
                raise

        # Step 11: Generate final TSV files from database (if starting from here)
        if start_step_num <= 11:
            print("\n=== Step 11: Generating final TSV files from database ===")
            progress = db_manager.start_step('copy', args.start_year, args.end_year)
            try:
                from generate_final_tsvs import TSVGenerator

                # Generate all final TSV files from database queries
                generator = TSVGenerator(db_path="/Volumes/Data/final/pipeline_progress.db")
                results = generator.generate_all_tsvs(args.final_dir)

                print("Generated final TSV files:")
                for filename, count in results.items():
                    print(f"  {filename}: {count} rows")

                db_manager.complete_step('copy', args.start_year, args.end_year)
            except Exception as e:
                db_manager.fail_step('copy', args.start_year, args.end_year, str(e))
                raise

        # Step 12: Generate reports (if starting from here)
        if start_step_num <= 12:
            print("\n=== Step 12: Generating reports ===")
            progress = db_manager.start_step('report', args.start_year, args.end_year)
            try:
                from commands.analyze import generate_grant_report
                from utils.args import find_file_path

                # Find the grants_final.tsv file
                possible_dirs = [args.final_dir, args.analyzed_dir, args.tsvs_dir, args.data_root]
                input_file = find_file_path(possible_dirs, "grants_final.tsv", "grants final file")

                print(f"Looking for grants_final.tsv, found: {input_file}")

                generate_grant_report(
                    input_file=input_file,
                    report_file=f"{args.final_dir}/final_report.md",
                    verbose=args.verbose,
                    quiet=args.quiet
                )
                db_manager.complete_step('report', args.start_year, args.end_year)
            except Exception as e:
                db_manager.fail_step('report', args.start_year, args.end_year, str(e))
                raise

        # Step 12: Copy files to browse directory (if starting from report step)
        if start_step_num <= 11:
            print("\n=== Step 12: Copying files to browse directory ===")
            import shutil
            import os

            # Copy grants_final.tsv to browse directory
            grants_src = os.path.join(args.final_dir, "grants_final.tsv")
            grants_dst = os.path.join(args.browse_dir, "grants.tsv")
            if os.path.exists(grants_src):
                shutil.copy2(grants_src, grants_dst)
                print(f"Copied grants_final.tsv to {args.browse_dir}/grants.tsv")
            else:
                print("grants_final.tsv not found, skipping...")

            # Copy grants_pf.tsv to browse directory
            grants_pf_src = os.path.join(args.final_dir, "grants_pf.tsv")
            grants_pf_dst = os.path.join(args.browse_dir, "grants.pf.tsv")
            if os.path.exists(grants_pf_src):
                shutil.copy2(grants_pf_src, grants_pf_dst)
                print(f"Copied grants_pf.tsv to {args.browse_dir}/grants.pf.tsv")
            else:
                print("grants_pf.tsv not found, skipping...")

            # Copy charity_latest.tsv to browse directory
            charity_src = os.path.join(args.final_dir, "charity_latest.tsv")
            charity_dst = os.path.join(args.browse_dir, "charities.tsv")
            if os.path.exists(charity_src):
                shutil.copy2(charity_src, charity_dst)
                print(f"Copied charity_latest.tsv to {args.browse_dir}/charities.tsv")
            else:
                print("charity_latest.tsv not found, skipping...")

            # Copy contractors.tsv to browse directory if it exists
            contractors_src = os.path.join(args.final_dir, "contractors.tsv")
            contractors_dst = os.path.join(args.browse_dir, "contractors.tsv")
            if os.path.exists(contractors_src):
                shutil.copy2(contractors_src, contractors_dst)
                print(f"Copied contractors.tsv to {args.browse_dir}")
            else:
                print("contractors.tsv not found, skipping...")

            # Copy political_contributions.tsv to browse directory if it exists
            political_src = os.path.join(args.final_dir, "political_contributions.tsv")
            political_dst = os.path.join(args.browse_dir, "political_contributions.tsv")
            if os.path.exists(political_src):
                shutil.copy2(political_src, political_dst)
                print(f"Copied political_contributions.tsv to {args.browse_dir}")
            else:
                print("political_contributions.tsv not found, skipping...")

        print("\n=== Pipeline Complete ===")
        print("All requested steps completed successfully!")
        print(f"Output files are in: {args.final_dir}")
        print(f"Browse files are in: {args.browse_dir}")

        # Print progress summary
        summary = db_manager.get_progress_summary(args.start_year, args.end_year)
        print(f"\nProgress Summary: {summary['completed_steps']}/{summary['total_steps']} steps completed")

    except Exception as e:
        print(f"Error during pipeline execution: {e}")
        if args.verbose:
            import traceback
            traceback.print_exc()
        raise