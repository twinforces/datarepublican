"""
Pipeline execution commands
"""

def run_all_pipeline(args):
    """Run the complete processing pipeline."""
    print("Running complete IRS 990 processing pipeline...")
    print(f"Processing years {args.start_year} to {args.end_year}")
    print(f"Directories: zips={args.zips_dir}, tsvs={args.tsvs_dir}, analyzed={args.analyzed_dir}, final={args.final_dir}")

    try:
        # Step 1: Download IRS ZIP files
        print("\n=== Step 1: Downloading IRS ZIP files ===")
        from download_irs_990_zips import main as download_main
        download_main(args.start_year, args.end_year, args.zips_dir, verbose=args.verbose, quiet=args.quiet)

        # Step 2: Recompress ZIP files
        print("\n=== Step 2: Recompressing ZIP files ===")
        from recompress_irs_zips import main as recompress_main
        recompress_main(zips_dir=args.zips_dir, verbose=args.verbose, quiet=args.quiet)

        # Step 3: Extract charity data
        print("\n=== Step 3: Extracting charity data ===")
        from extract_charities import main as extract_main
        extract_main(
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
        from analyze_charities import main as analyze_main
        import sys
        # Set up arguments for analyze_charities
        sys.argv = ['analyze_charities.py',
                   '--input-dir', args.tsvs_dir,
                   '--output-dir', args.analyzed_dir,
                   '--start-year', str(args.start_year),
                   '--stop-year', str(args.end_year)]
        analyze_main()

        # Step 5: Get latest filings
        print("\n=== Step 5: Getting latest filings ===")
        from get_latest import main as latest_main
        # Set up arguments for get_latest
        sys.argv = ['get_latest.py', str(args.start_year), str(args.end_year),
                   '--source-dir', args.analyzed_dir,
                   '--zip-dir', args.zips_dir,
                   '--output-dir', args.final_dir,
                   '--minimumD', str(args.minimum_d),
                   '--orgTypes', 'all',
                   '--NOTTypes', '',
                   '--worker-threads', str(args.worker_threads)]
        if args.verbose:
            sys.argv.append('--verbose')
        if args.quiet:
            sys.argv.append('--quiet')
        latest_main()

        # Step 6: Extract addresses
        print("\n=== Step 6: Extracting addresses ===")
        from extract_addresses import main as addresses_main
        # Set up arguments for extract_addresses
        sys.argv = ['extract_addresses.py', str(args.start_year), str(args.end_year),
                   '--zip-dir', args.zips_dir,
                   '--cache-dir', args.cache_dir,
                   '--output-dir', args.final_dir]
        if args.verbose:
            sys.argv.append('--verbose')
        if args.quiet:
            sys.argv.append('--quiet')
        addresses_main()

        # Step 7: Add backfill
        print("\n=== Step 7: Adding backfill data ===")
        from add_backfill import main as backfill_main
        backfill_main(
            charity_tsv=f"{args.final_dir}/charity_latest.tsv",
            backfill_tsv=f"{args.final_dir}/backfill.tsv",
            output_dir=args.final_dir,
            verbose=args.verbose,
            quiet=args.quiet
        )

        # Step 8: Extract grants
        print("\n=== Step 8: Extracting grants ===")
        from extract_grants import main as grants_main
        # Set up arguments for extract_grants
        sys.argv = ['extract_grants.py', str(args.start_year), str(args.end_year),
                   '--source-dir', args.analyzed_dir,
                   '--zip-dir', args.zips_dir,
                   '--output-dir', args.final_dir,
                   '--minimumD', str(args.minimum_d),
                   '--orgTypes', 'all',
                   '--NOTTypes', '',
                   '--worker-threads', str(args.worker_threads)]
        if args.verbose:
            sys.argv.append('--verbose')
        if args.quiet:
            sys.argv.append('--quiet')
        grants_main()

        # Step 9: Check grants
        print("\n=== Step 9: Checking grants ===")
        from grant_check import main as check_main
        check_main(
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
        from grant_report import main as report_main
        report_main(
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
        # Step 1: Download IRS ZIP files (if starting from here)
        if start_step_num <= 1:
            print("\n=== Step 1: Downloading IRS ZIP files ===")
            from download_irs_990_zips import main as download_main
            download_main(args.start_year, args.end_year, args.zips_dir, verbose=args.verbose, quiet=args.quiet)

        # Step 2: Recompress ZIP files (if starting from here)
        if start_step_num <= 2:
            print("\n=== Step 2: Recompressing ZIP files ===")
            from recompress_irs_zips import main as recompress_main
            recompress_main(zips_dir=args.zips_dir, verbose=args.verbose, quiet=args.quiet)

        # Step 3: Extract charity data (if starting from here)
        if start_step_num <= 3:
            print("\n=== Step 3: Extracting charity data ===")
            from extract_charities import main as extract_main
            extract_main(
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
                from analyze_charities import main as analyze_main
                import sys
                sys.argv = ['analyze_charities.py',
                           '--input-dir', args.tsvs_dir,
                           '--output-dir', args.analyzed_dir,
                           '--start-year', str(args.start_year),
                           '--stop-year', str(args.end_year)]
                if args.verbose:
                    sys.argv.append('--verbose')
                if args.quiet:
                    sys.argv.append('--quiet')
                analyze_main()
            except Exception as e:
                print(f"Error in analyze step: {e}")
                if args.verbose:
                    import traceback
                    traceback.print_exc()

        # Step 5: Get latest filings (if starting from here)
        if start_step_num <= 5:
            print("\n=== Step 5: Getting latest filings ===")
            try:
                from get_latest import main as latest_main
                import sys
                sys.argv = ['get_latest.py', str(args.start_year), str(args.end_year),
                           '--source-dir', args.analyzed_dir,
                           '--zip-dir', args.zips_dir,
                           '--output-dir', args.final_dir,
                           '--minimumD', str(args.minimum_d),
                           '--orgTypes', 'all',
                           '--NOTTypes', '',
                           '--worker-threads', str(args.worker_threads)]
                if args.verbose:
                    sys.argv.append('--verbose')
                if args.quiet:
                    sys.argv.append('--quiet')
                latest_main()
            except Exception as e:
                print(f"Error in get-latest step: {e}")
                if args.verbose:
                    import traceback
                    traceback.print_exc()

        # Step 6: Extract addresses (if starting from here)
        if start_step_num <= 6:
            print("\n=== Step 6: Extracting addresses ===")
            try:
                from extract_addresses import main as addresses_main
                import sys
                sys.argv = ['extract_addresses.py', str(args.start_year), str(args.end_year),
                           '--zip-dir', args.zips_dir,
                           '--cache-dir', args.cache_dir,
                           '--output-dir', args.final_dir]
                if args.verbose:
                    sys.argv.append('--verbose')
                if args.quiet:
                    sys.argv.append('--quiet')
                addresses_main()
            except Exception as e:
                print(f"Error in extract-addresses step: {e}")
                if args.verbose:
                    import traceback
                    traceback.print_exc()

        # Step 7: Add backfill (if starting from here)
        if start_step_num <= 7:
            print("\n=== Step 7: Adding backfill data ===")
            try:
                from add_backfill import main as backfill_main
                backfill_main(
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
                from extract_grants import main as grants_main
                import sys
                sys.argv = ['extract_grants.py', str(args.start_year), str(args.end_year),
                           '--source-dir', args.analyzed_dir,
                           '--zip-dir', args.zips_dir,
                           '--output-dir', args.final_dir,
                           '--minimumD', str(args.minimum_d),
                           '--orgTypes', 'all',
                           '--NOTTypes', '',
                           '--worker-threads', str(args.worker_threads)]
                if args.verbose:
                    sys.argv.append('--verbose')
                if args.quiet:
                    sys.argv.append('--quiet')
                grants_main()
            except Exception as e:
                print(f"Error in extract-grants step: {e}")
                if args.verbose:
                    import traceback
                    traceback.print_exc()

        # Step 9: Check grants (if starting from here)
        if start_step_num <= 9:
            print("\n=== Step 9: Checking grants ===")
            from grant_check import main as check_main
            check_main(
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
            from grant_report import main as report_main
            report_main(
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