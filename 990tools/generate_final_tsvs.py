#!/usr/bin/env python3
"""
Generate final TSV files directly from database queries.

This script replaces the intermediate TSV file copying with direct database queries
to generate the final output files with identical format and content.
"""

import sqlite3
import csv
import argparse
import logging
import sys
from pathlib import Path
from typing import Dict, List, Optional


class TSVGenerator:
    """Handles generation of final TSV files from database queries."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self.logger = logging.getLogger(__name__)

    def connect_db(self):
        """Get database connection."""
        return sqlite3.connect(self.db_path)

    def generate_charity_latest(self, output_file: str) -> int:
        """Generate charity_latest.tsv from Charities table."""
        self.logger.info("Generating charity_latest.tsv")

        query = """
        SELECT
            tax_year,
            ein as filer_ein,
            filer_name,
            receipt_amt,
            govt_amt,
            contrib_amt,
            org_type,
            total_exp,
            prog_exp,
            travel_amt,
            conferences_amt,
            officer_comp,
            comp_pct,
            comp_ptile,
            travel_pct,
            travel_ptile,
            conferences_pct,
            conferences_ptile,
            grants_pct,
            grants_ptile,
            foreign_expenses_pct,
            foreign_expenses_ptile,
            grift_ratio,
            total_assets,
            form_type,
            denominator,
            foreign_office,
            foreign_expenses,
            grants_to_others,
            domestic_misrep_flag,
            xml_name,
            '' as canonical_address,  -- Will be populated from addresses if needed
            '' as mailing_zip,        -- Will be populated from addresses if needed
            '' as colocator           -- Will be populated from addresses if needed
        FROM Charities
        ORDER BY ein, tax_year DESC
        """

        with self.connect_db() as conn:
            cursor = conn.cursor()
            cursor.execute(query)
            rows = cursor.fetchall()

            # Get column names from cursor description
            columns = [desc[0] for desc in cursor.description]

            with open(output_file, 'w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f, delimiter='\t')
                writer.writerow(columns)
                writer.writerows(rows)

        self.logger.info(f"Generated {len(rows)} rows in charity_latest.tsv")
        return len(rows)

    def generate_charity_latest_with_backfill(self, output_file: str) -> int:
        """Generate charity_latest_with_backfill.tsv with backfill data joined."""
        self.logger.info("Generating charity_latest_with_backfill.tsv")

        query = """
        SELECT
            c.tax_year,
            c.ein as filer_ein,
            COALESCE(b.name, c.filer_name) as filer_name,
            c.receipt_amt,
            c.govt_amt,
            c.contrib_amt,
            c.org_type,
            c.total_exp,
            c.prog_exp,
            c.travel_amt,
            c.conferences_amt,
            c.officer_comp,
            c.comp_pct,
            c.comp_ptile,
            c.travel_pct,
            c.travel_ptile,
            c.conferences_pct,
            c.conferences_ptile,
            c.grants_pct,
            c.grants_ptile,
            c.foreign_expenses_pct,
            c.foreign_expenses_ptile,
            c.grift_ratio,
            c.total_assets,
            c.form_type,
            c.denominator,
            c.foreign_office,
            c.foreign_expenses,
            c.grants_to_others,
            c.domestic_misrep_flag,
            c.xml_name,
            COALESCE(b.canonical_address, '') as canonical_address,
            COALESCE(b.zip_code, '') as mailing_zip,
            COALESCE(b.po_box, '') as colocator
        FROM Charities c
        LEFT JOIN Backfill b ON c.ein = b.grant_ein
        ORDER BY c.ein, c.tax_year DESC
        """

        with self.connect_db() as conn:
            cursor = conn.cursor()
            cursor.execute(query)
            rows = cursor.fetchall()

            # Get column names from cursor description
            columns = [desc[0] for desc in cursor.description]

            with open(output_file, 'w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f, delimiter='\t')
                writer.writerow(columns)
                writer.writerows(rows)

        self.logger.info(f"Generated {len(rows)} rows in charity_latest_with_backfill.tsv")
        return len(rows)

    def generate_grants_latest(self, output_file: str) -> int:
        """Generate grants_latest.tsv from Grants table."""
        self.logger.info("Generating grants_latest.tsv")

        query = """
        SELECT
            filer_ein,
            filer_name,
            grant_ein,
            grant_amt,
            tax_year,
            filer_colocator,
            grantee_colocator
        FROM Grants
        ORDER BY filer_ein, tax_year DESC, grant_amt DESC
        """

        with self.connect_db() as conn:
            cursor = conn.cursor()
            cursor.execute(query)
            rows = cursor.fetchall()

            # Column headers
            columns = ['filer_ein', 'filer_name', 'grant_ein', 'grant_amt', 'tax_year', 'filer_colocator', 'grantee_colocator']

            with open(output_file, 'w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f, delimiter='\t')
                writer.writerow(columns)
                writer.writerows(rows)

        self.logger.info(f"Generated {len(rows)} rows in grants_latest.tsv")
        return len(rows)

    def generate_grants_final(self, output_file: str) -> int:
        """Generate grants_final.tsv with EIN resolution and backfill matching."""
        self.logger.info("Generating grants_final.tsv")

        query = """
        SELECT
            g.filer_ein,
            g.filer_name,
            COALESCE(g.grant_ein, b.grant_ein) as grant_ein,
            g.grant_amt,
            g.tax_year,
            g.filer_colocator,
            COALESCE(b.canonical_address, g.grantee_colocator) as grantee_colocator
        FROM Grants g
        LEFT JOIN Backfill b ON g.grant_ein = b.grant_ein OR (g.grant_ein IS NULL AND b.name = g.filer_name)
        ORDER BY g.filer_ein, g.tax_year DESC, g.grant_amt DESC
        """

        with self.connect_db() as conn:
            cursor = conn.cursor()
            cursor.execute(query)
            rows = cursor.fetchall()

            # Column headers
            columns = ['filer_ein', 'filer_name', 'grant_ein', 'grant_amt', 'tax_year', 'filer_colocator', 'grantee_colocator']

            with open(output_file, 'w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f, delimiter='\t')
                writer.writerow(columns)
                writer.writerows(rows)

        self.logger.info(f"Generated {len(rows)} rows in grants_final.tsv")
        return len(rows)

    def generate_grants_pf(self, output_file: str) -> int:
        """Generate grants_pf.tsv for private foundation grants."""
        self.logger.info("Generating grants_pf.tsv")

        # Private foundations are typically 501(c)(3) with specific form types
        query = """
        SELECT
            g.filer_ein,
            g.filer_name,
            COALESCE(g.grant_ein, b.grant_ein) as grant_ein,
            g.grant_amt,
            g.tax_year,
            g.filer_colocator,
            COALESCE(b.canonical_address, g.grantee_colocator) as grantee_colocator
        FROM Grants g
        INNER JOIN Charities c ON g.filer_ein = c.ein AND g.tax_year = c.tax_year
        LEFT JOIN Backfill b ON g.grant_ein = b.grant_ein OR (g.grant_ein IS NULL AND b.name = g.filer_name)
        WHERE c.form_type = '990PF'
        ORDER BY g.filer_ein, g.tax_year DESC, g.grant_amt DESC
        """

        with self.connect_db() as conn:
            cursor = conn.cursor()
            cursor.execute(query)
            rows = cursor.fetchall()

            # Column headers
            columns = ['filer_ein', 'filer_name', 'grant_ein', 'grant_amt', 'tax_year', 'filer_colocator', 'grantee_colocator']

            with open(output_file, 'w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f, delimiter='\t')
                writer.writerow(columns)
                writer.writerows(rows)

        self.logger.info(f"Generated {len(rows)} rows in grants_pf.tsv")
        return len(rows)

    def generate_political_contributions(self, output_file: str) -> int:
        """Generate political_contributions.tsv from PoliticalContributions table."""
        self.logger.info("Generating political_contributions.tsv")

        query = """
        SELECT
            filer_ein,
            recipient,
            amount,
            recipient_address,
            recipient_zip,
            recipient_po_box,
            tax_year
        FROM PoliticalContributions
        ORDER BY filer_ein, tax_year DESC, amount DESC
        """

        with self.connect_db() as conn:
            cursor = conn.cursor()
            cursor.execute(query)
            rows = cursor.fetchall()

            # Column headers
            columns = ['filer_ein', 'recipient', 'amount', 'recipient_address', 'recipient_zip', 'recipient_po_box', 'tax_year']

            with open(output_file, 'w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f, delimiter='\t')
                writer.writerow(columns)
                writer.writerows(rows)

        self.logger.info(f"Generated {len(rows)} rows in political_contributions.tsv")
        return len(rows)

    def generate_contractors(self, output_file: str) -> int:
        """Generate contractors.tsv from Contractors table."""
        self.logger.info("Generating contractors.tsv")

        query = """
        SELECT
            filer_ein,
            name,
            amount,
            ein,
            address,
            zip_code,
            po_box,
            tax_year
        FROM Contractors
        ORDER BY filer_ein, tax_year DESC, amount DESC
        """

        with self.connect_db() as conn:
            cursor = conn.cursor()
            cursor.execute(query)
            rows = cursor.fetchall()

            # Column headers
            columns = ['filer_ein', 'name', 'amount', 'ein', 'address', 'zip_code', 'po_box', 'tax_year']

            with open(output_file, 'w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f, delimiter='\t')
                writer.writerow(columns)
                writer.writerows(rows)

        self.logger.info(f"Generated {len(rows)} rows in contractors.tsv")
        return len(rows)

    def generate_all_tsvs(self, output_dir: str) -> Dict[str, int]:
        """Generate all final TSV files."""
        self.logger.info(f"Generating all TSV files in {output_dir}")

        # Ensure output directory exists
        Path(output_dir).mkdir(parents=True, exist_ok=True)

        results = {}

        # Generate each TSV file
        results['charity_latest.tsv'] = self.generate_charity_latest(
            f"{output_dir}/charity_latest.tsv"
        )

        results['charity_latest_with_backfill.tsv'] = self.generate_charity_latest_with_backfill(
            f"{output_dir}/charity_latest_with_backfill.tsv"
        )

        results['grants_latest.tsv'] = self.generate_grants_latest(
            f"{output_dir}/grants_latest.tsv"
        )

        results['grants_final.tsv'] = self.generate_grants_final(
            f"{output_dir}/grants_final.tsv"
        )

        results['grants_pf.tsv'] = self.generate_grants_pf(
            f"{output_dir}/grants_pf.tsv"
        )

        results['political_contributions.tsv'] = self.generate_political_contributions(
            f"{output_dir}/political_contributions.tsv"
        )

        results['contractors.tsv'] = self.generate_contractors(
            f"{output_dir}/contractors.tsv"
        )

        self.logger.info("All TSV files generated successfully")
        return results


def main():
    parser = argparse.ArgumentParser(description="Generate final TSV files from database queries")
    parser.add_argument("--db-path", default="/Volumes/Data/final/pipeline_progress.db",
                       help="Path to SQLite database")
    parser.add_argument("--output-dir", default="/Volumes/Data/final",
                       help="Output directory for TSV files")
    parser.add_argument("--verbose", action="store_true", help="Enable verbose logging")
    parser.add_argument("--quiet", action="store_true", help="Suppress non-error output")

    args = parser.parse_args()

    # Setup logging
    log_level = logging.ERROR if args.quiet else (logging.DEBUG if args.verbose else logging.INFO)
    logging.basicConfig(level=log_level, format='%(asctime)s - %(levelname)s - %(message)s')

    # Generate TSVs
    generator = TSVGenerator(args.db_path)
    results = generator.generate_all_tsvs(args.output_dir)

    # Print summary
    if not args.quiet:
        print("\nTSV Generation Summary:")
        for filename, count in results.items():
            print(f"  {filename}: {count} rows")


if __name__ == "__main__":
    main()