"""
Pipeline execution commands
"""

def run_all_pipeline(args):
    """Run the complete processing pipeline."""
    print("Running complete IRS 990 processing pipeline...")
    print(f"Processing years {args.start_year} to {args.end_year}")
    print(f"Directories: zips={args.zips_dir}, tsvs={args.tsvs_dir}, analyzed={args.analyzed_dir}, final={args.final_dir}")

    try:
        # Import optimization functions
        from irs990tools import should_skip_download, should_skip_recompress, check_index_status

        # Step 1: Download IRS ZIP files (always check website for new files)
        print("\n=== Step 1: Downloading IRS ZIP files ===")
        from commands.download import download_irs_zips
        download_irs_zips(args.start_year, args.end_year, args.zips_dir, verbose=args.verbose, quiet=args.quiet)

        # Step 2: Recompress ZIP files (skip if not needed)
        print("\n=== Step 2: Recompressing ZIP files ===")
        skip_recompress = should_skip_recompress(args.zips_dir, args.start_year, args.end_year,
                                                force=getattr(args, 'force', False),
                                                verbose=args.verbose, quiet=args.quiet)

        if not skip_recompress:
            from commands.download import recompress_zips
            recompress_zips(zips_dir=args.zips_dir, verbose=args.verbose, quiet=args.quiet)
        else:
            print("Skipping recompress step - no recompression needed")

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

        # Step 4: Analyze charities
        print("\n=== Step 4: Analyzing charity data ===")
        from commands.analyze import analyze_charities
        analyze_charities(args)

        # Step 5: Get latest filings
        print("\n=== Step 5: Getting latest filings ===")
        from commands.analyze import get_latest_filings
        get_latest_filings(args)

        # Step 6: Extract addresses
        print("\n=== Step 6: Extracting addresses ===")
        from commands.extract import extract_addresses
        extract_addresses(args)

        # Step 7: Add backfill
        print("\n=== Step 7: Adding backfill data ===")
        from commands.extract import add_backfill
        add_backfill(
            charity_tsv=f"{args.final_dir}/charity_latest.tsv",
            backfill_tsv=f"{args.final_dir}/backfill.tsv",
            output_dir=args.final_dir,
            verbose=args.verbose,
            quiet=args.quiet
        )

        # Step 8: Extract grants
        print("\n=== Step 8: Extracting grants ===")
        from commands.extract import extract_grants
        extract_grants(args)

        # Step 9: Check grants
        print("\n=== Step 9: Checking grants ===")
        from commands.analyze import check_grants
        check_grants(
            index_file=f"{args.final_dir}/charity_latest_with_backfill.tsv",
            input_file=f"{args.final_dir}/grants_latest.tsv",
            output_file=f"{args.final_dir}/grants_final.tsv",
            report_file=f"{args.final_dir}/filter_501.md",
            verbose=args.verbose,
            quiet=args.quiet
        )

        # Step 10: Copy additional files to final directory
        print("\n=== Step 10: Copying additional files ===")
        import shutil
        import os

        # Copy contractor and political contribution files if they exist
        contractors_src = os.path.join(args.tsvs_dir, "contractors.tsv")
        contractors_dst = os.path.join(args.final_dir, "contractors.tsv")
        if os.path.exists(contractors_src):
            shutil.copy2(contractors_src, contractors_dst)
            print(f"Copied contractors.tsv to {args.final_dir}")
        else:
            print("contractors.tsv not found, skipping...")

        political_src = os.path.join(args.tsvs_dir, "political_contributions.tsv")
        political_dst = os.path.join(args.final_dir, "political_contributions.tsv")
        if os.path.exists(political_src):
            shutil.copy2(political_src, political_dst)
            print(f"Copied political_contributions.tsv to {args.final_dir}")
        else:
            print("political_contributions.tsv not found, skipping...")

        # Step 11: Generate reports
        print("\n=== Step 11: Generating reports ===")
        from commands.analyze import generate_grant_report
        generate_grant_report(
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

def run_from_step(args):
    """Run the processing pipeline starting from a specific step."""
    print(f"Running IRS 990 processing pipeline starting from step: {args.start_step}")
    print(f"Processing years {args.start_year} to {args.end_year}")

    steps = {
        'download': 1,
        'recompress': 2,
        'extract': 3,
        'analyze': 4,
        'latest': 5,
        'addresses': 6,
        'backfill': 7,
        'grants': 8,
        'check': 9,
        'copy': 10,
        'report': 11
    }

    start_step_num = steps.get(args.start_step, 1)

    try:
        # Import optimization functions
        from irs990tools import should_skip_download, should_skip_recompress, check_index_status

        # Step 1: Download IRS ZIP files (if starting from here, always check website)
        if start_step_num <= 1:
            print("\n=== Step 1: Downloading IRS ZIP files ===")
            from commands.download import download_irs_zips
            download_irs_zips(args.start_year, args.end_year, args.zips_dir, verbose=args.verbose, quiet=args.quiet)

        # Step 2: Recompress ZIP files (if starting from here)
        if start_step_num <= 2:
            print("\n=== Step 2: Recompressing ZIP files ===")
            skip_recompress = should_skip_recompress(args.zips_dir, args.start_year, args.end_year,
                                                    force=getattr(args, 'force', False),
                                                    verbose=args.verbose, quiet=args.quiet)

            if not skip_recompress:
                from commands.download import recompress_zips
                recompress_zips(zips_dir=args.zips_dir, verbose=args.verbose, quiet=args.quiet)
            else:
                print("Skipping recompress step - no recompression needed")

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

        # Step 4: Analyze charities (if starting from here)
        if start_step_num <= 4:
            print("\n=== Step 4: Analyzing charity data ===")
            try:
                from commands.analyze import analyze_charities
                analyze_charities(args)
            except Exception as e:
                print(f"Error in analyze step: {e}")
                if args.verbose:
                    import traceback
                    traceback.print_exc()

        # Step 5: Get latest filings (if starting from here)
        if start_step_num <= 5:
            print("\n=== Step 5: Getting latest filings ===")
            try:
                from commands.analyze import get_latest_filings
                get_latest_filings(args)
            except Exception as e:
                print(f"Error in get-latest step: {e}")
                if args.verbose:
                    import traceback
                    traceback.print_exc()

        # Step 6: Extract addresses (if starting from here)
        if start_step_num <= 6:
            print("\n=== Step 6: Extracting addresses ===")
            try:
                from commands.extract import extract_addresses
                extract_addresses(args)
            except Exception as e:
                print(f"Error in extract-addresses step: {e}")
                if args.verbose:
                    import traceback
                    traceback.print_exc()

        # Step 7: Add backfill (if starting from here)
        if start_step_num <= 7:
            print("\n=== Step 7: Adding backfill data ===")
            try:
                from commands.extract import add_backfill
                add_backfill(
                    charity_tsv=f"{args.final_dir}/charity_latest.tsv",
                    backfill_tsv=f"{args.final_dir}/backfill.tsv",
                    output_dir=args.final_dir,
                    verbose=args.verbose,
                    quiet=args.quiet
                )
            except Exception as e:
                print(f"Error in add-backfill step: {e}")
                if args.verbose:
                    import traceback
                    traceback.print_exc()

        # Step 8: Extract grants (if starting from here)
        if start_step_num <= 8:
            print("\n=== Step 8: Extracting grants ===")
            try:
                from commands.extract import extract_grants
                extract_grants(args)
            except Exception as e:
                print(f"Error in extract-grants step: {e}")
                if args.verbose:
                    import traceback
                    traceback.print_exc()

        # Step 9: Check grants (if starting from here)
        if start_step_num <= 9:
            print("\n=== Step 9: Checking grants ===")
            from commands.analyze import check_grants
            check_grants(
                index_file=f"{args.final_dir}/charity_latest_with_backfill.tsv",
                input_file=f"{args.final_dir}/grants_latest.tsv",
                output_file=f"{args.final_dir}/grants_final.tsv",
                report_file=f"{args.final_dir}/filter_501.md",
                verbose=args.verbose,
                quiet=args.quiet
            )

        # Step 10: Copy additional files (if starting from here)
        if start_step_num <= 10:
            print("\n=== Step 10: Copying additional files ===")
            import shutil
            import os

            # Copy contractor and political contribution files if they exist
            contractors_src = os.path.join(args.tsvs_dir, "contractors.tsv")
            contractors_dst = os.path.join(args.final_dir, "contractors.tsv")
            if os.path.exists(contractors_src):
                shutil.copy2(contractors_src, contractors_dst)
                print(f"Copied contractors.tsv to {args.final_dir}")
            else:
                print("contractors.tsv not found, skipping...")

            political_src = os.path.join(args.tsvs_dir, "political_contributions.tsv")
            political_dst = os.path.join(args.final_dir, "political_contributions.tsv")
            if os.path.exists(political_src):
                shutil.copy2(political_src, political_dst)
                print(f"Copied political_contributions.tsv to {args.final_dir}")
            else:
                print("political_contributions.tsv not found, skipping...")

        # Step 11: Generate reports (if starting from here)
        if start_step_num <= 11:
            print("\n=== Step 11: Generating reports ===")
            from commands.analyze import generate_grant_report
            generate_grant_report(
                input_file=f"{args.final_dir}/grants_final.tsv",
                report_file=f"{args.final_dir}/final_report.md",
                verbose=args.verbose,
                quiet=args.quiet
            )

        print("\n=== Pipeline Complete ===")
        print("All requested steps completed successfully!")
        print(f"Output files are in: {args.final_dir}")

    except Exception as e:
        print(f"Error during pipeline execution: {e}")
        if args.verbose:
            import traceback
            traceback.print_exc()
        raise