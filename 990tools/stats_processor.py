#!/usr/bin/env python3
"""
stats_processor.py - Statistics generation and reporting for IRS 990 data processing

This module handles all statistics generation, analysis, and reporting functionality
for the IRS 990 processor.
"""

import os
from typing import Dict, List, Tuple, Any
from datetime import datetime
from mako.template import Template


class StatsProcessor:
    """Handles statistics generation and reporting for IRS 990 data"""

    def __init__(self, db_ops):
        """
        Initialize StatsProcessor with database operations instance

        Args:
            db_ops: DatabaseOperations instance for database access
        """
        self.db_ops = db_ops

    def generate_stats_report(self, step_name: str, notes: str = "") -> str:
        """Generate a statistics report for the current database state"""
        from datetime import datetime
        from mako.template import Template
        import os

        # Get table counts and summaries
        table_counts = self.get_table_counts()
        table_summaries = self.get_table_summaries()
        total_records = sum(table_counts.values())

        # Get XmlFiles specific statistics
        xml_group_counts = self.get_xml_files_group_counts()
        xml_histogram = self.get_xml_files_histogram()

        # Get comprehensive analysis for each major table
        charities_analysis = self.get_charities_analysis()
        officers_analysis = self.get_officers_analysis()
        grants_analysis = self.get_grants_analysis()
        contractors_analysis = self.get_contractors_analysis()
        political_contributions_analysis = self.get_political_contributions_analysis()
        addresses_analysis = self.get_addresses_analysis()

        # Prepare template data
        template_data = {
            'step_name': step_name,
            'timestamp': datetime.now().isoformat(),
            'db_path': self.db_ops.db_path,
            'table_counts': table_counts,
            'table_summaries': table_summaries,
            'total_records': total_records,
            'xml_group_counts': xml_group_counts,
            'xml_histogram': xml_histogram,
            'charities_analysis': charities_analysis,
            'officers_analysis': officers_analysis,
            'grants_analysis': grants_analysis,
            'contractors_analysis': contractors_analysis,
            'political_contributions_analysis': political_contributions_analysis,
            'addresses_analysis': addresses_analysis,
            'notes': notes or "No additional notes."
        }

        # Load and render template
        template_path = os.path.join(os.path.dirname(__file__), 'stats_template.mako')
        with open(template_path, 'r') as f:
            template_content = f.read()

        template = Template(template_content)
        report_content = template.render(**template_data)

        # Write report to file
        report_filename = f"stats_{step_name}.md"
        with open(report_filename, 'w') as f:
            f.write(report_content)

        return report_filename  # type: ignore

    def get_table_counts(self) -> dict:
        """Get row counts for all tables"""
        tables = [
            'ZipFiles', 'XmlFiles', 'Charities', 'Officers', 'Grants',
            'Contractors', 'PoliticalContributions', 'Addresses', 'Geocoding'
        ]

        counts = {}
        for table in tables:
            try:
                result = self.db_ops.execute_query(f"SELECT COUNT(*) FROM {table}").fetchone()
                counts[table] = result[0] if result else 0
            except Exception:
                counts[table] = 0  # Table might not exist yet

        return counts

    def get_table_summaries(self) -> dict:
        """Get SUMMARIZE data for all tables"""
        tables = [
            'ZipFiles', 'XmlFiles', 'Charities', 'Officers', 'Grants',
            'Contractors', 'PoliticalContributions', 'Addresses', 'Geocoding'
        ]

        summaries = {}
        for table in tables:
            try:
                result = self.db_ops.execute_query(f"SUMMARIZE {table}")
                summaries[table] = result.fetchall()
            except Exception:
                summaries[table] = []  # Table might not exist yet or SUMMARIZE might fail

        return summaries

    def get_xml_files_group_counts(self) -> Dict[str, List[Tuple]]:
        """Get group by counts for XmlFiles columns"""
        group_counts = {}

        # Group by tax_year
        result = self.db_ops.execute_query("""
            SELECT tax_year, COUNT(*) as count
            FROM XmlFiles
            GROUP BY tax_year
            ORDER BY tax_year
        """).fetchall()
        group_counts['tax_year'] = result

        # Group by form_type
        result = self.db_ops.execute_query("""
            SELECT form_type, COUNT(*) as count
            FROM XmlFiles
            GROUP BY form_type
            ORDER BY form_type
        """).fetchall()
        group_counts['form_type'] = result

        # Group by processed
        result = self.db_ops.execute_query("""
            SELECT processed, COUNT(*) as count
            FROM XmlFiles
            GROUP BY processed
            ORDER BY processed
        """).fetchall()
        group_counts['processed'] = result

        # Group by processing_version
        result = self.db_ops.execute_query("""
            SELECT processing_version, COUNT(*) as count
            FROM XmlFiles
            GROUP BY processing_version
            ORDER BY processing_version
        """).fetchall()
        group_counts['processing_version'] = result

        # Group by error_message prefix (first 50 characters)
        result = self.db_ops.execute_query("""
            SELECT
                CASE
                    WHEN error_message IS NULL THEN 'NULL'
                    WHEN LENGTH(error_message) <= 50 THEN error_message
                    ELSE SUBSTRING(error_message, 1, 50) || '...'
                END as error_prefix,
                COUNT(*) as count
            FROM XmlFiles
            GROUP BY error_prefix
            ORDER BY count DESC
        """).fetchall()
        group_counts['error_message_prefix'] = result

        return group_counts

    def get_xml_files_histogram(self) -> List[Dict]:
        """Get histogram data for XmlFiles.file_size using DuckDB histogram with 10 buckets"""
        try:
            # Check if min != max before creating histogram
            result = self.db_ops.execute_query("""
                SELECT MIN(file_size) as min_val, MAX(file_size) as max_val
                FROM XmlFiles
                WHERE file_size IS NOT NULL
            """).fetchone()
            if result and result[0] != result[1]:
                # Create histogram with equi_width_bins and get total count
                hist_result = self.db_ops.execute_query("""
                    SELECT histogram(file_size, equi_width_bins(?, ?, 10, true)) as hist,
                           (SELECT COUNT(*) FROM XmlFiles WHERE file_size IS NOT NULL) as total
                    FROM XmlFiles WHERE file_size IS NOT NULL
                """, (result[0], result[1])).fetchone()
                if hist_result and hist_result[0]:
                    hist_map = hist_result[0]
                    total = hist_result[1]
                    return [{'bin_upper': k, 'count': v, 'pct': (v / total * 100) if total > 0 else 0} for k, v in hist_map.items()]
            return []
        except Exception:
            # Fallback if histogram fails
            return []

    def get_charities_analysis(self) -> Dict[str, Any]:
        """Get comprehensive analysis for Charities table"""
        analysis = {}

        # Tax year group by
        result = self.db_ops.execute_query("""
            SELECT tax_year, COUNT(*) as count
            FROM Charities
            GROUP BY tax_year
            ORDER BY tax_year
        """).fetchall()
        analysis['tax_year_counts'] = result

        # Org type counts
        result = self.db_ops.execute_query("""
            SELECT org_type, COUNT(*) as count
            FROM Charities
            GROUP BY org_type
            ORDER BY count DESC
        """).fetchall()
        analysis['org_type_counts'] = result

        # Form type counts
        result = self.db_ops.execute_query("""
            SELECT form_type, COUNT(*) as count
            FROM Charities
            GROUP BY form_type
            ORDER BY count DESC
        """).fetchall()
        analysis['form_type_counts'] = result

        # Histograms for Double columns (only when min != max)
        double_columns = ['receipt_amt', 'govt_amt', 'contrib_amt', 'total_exp', 'prog_exp',
                          'travel_amt', 'conferences_amt', 'officer_comp', 'comp_pct', 'comp_ptile',
                          'comp_ptile_value', 'travel_pct', 'travel_ptile', 'travel_ptile_value',
                          'conferences_pct', 'conferences_ptile', 'conferences_ptile_value',
                          'grants_pct', 'grants_ptile', 'grants_ptile_value', 'foreign_expenses_pct',
                          'foreign_expenses_ptile', 'foreign_expenses_ptile_value', 'grift_ratio',
                          'total_assets', 'denominator', 'foreign_expenses', 'grants_to_others', 'grift']
        analysis['histograms'] = {}

        for col in double_columns:
            # Check if min != max
            result = self.db_ops.execute_query(f"""
                SELECT MIN({col}) as min_val, MAX({col}) as max_val
                FROM Charities
                WHERE {col} IS NOT NULL
            """).fetchone()
            if result and result[0] != result[1]:
                # Create histogram with equi_width_bins and get total count
                hist_result = self.db_ops.execute_query(f"""
                    SELECT histogram({col}, equi_width_bins(?, ?, 10, true)) as hist,
                           (SELECT COUNT(*) FROM Charities WHERE {col} IS NOT NULL) as total
                    FROM Charities WHERE {col} IS NOT NULL
                """, (result[0], result[1])).fetchone()
                if hist_result and hist_result[0]:
                    hist_map = hist_result[0]
                    total = hist_result[1]
                    analysis['histograms'][col] = [{'bin_upper': k, 'count': v, 'pct': (v / total * 100) if total > 0 else 0} for k, v in hist_map.items()]

        return analysis

    def get_officers_analysis(self) -> Dict[str, Any]:
        """Get analysis for Officers table"""
        analysis = {}

        # Top 10 last names
        result = self.db_ops.execute_query("""
            SELECT last_name, COUNT(*) as count
            FROM Officers
            GROUP BY last_name
            ORDER BY count DESC
            LIMIT 10
        """).fetchall()
        analysis['top_last_names'] = result

        return analysis

    def get_grants_analysis(self) -> Dict[str, Any]:
        """Get analysis for Grants table"""
        analysis = {}

        # Grant amount histogram (only if min != max)
        result = self.db_ops.execute_query("""
            SELECT MIN(grant_amt) as min_val, MAX(grant_amt) as max_val
            FROM Grants
            WHERE grant_amt IS NOT NULL
        """).fetchone()
        if result and result[0] != result[1]:
            hist_result = self.db_ops.execute_query("""
                SELECT histogram(grant_amt, equi_width_bins(?, ?, 10, true)) as hist,
                       (SELECT COUNT(*) FROM Grants WHERE grant_amt IS NOT NULL) as total
                FROM Grants WHERE grant_amt IS NOT NULL
            """, (result[0], result[1])).fetchone()
            if hist_result and hist_result[0]:
                hist_map = hist_result[0]
                total = hist_result[1]
                analysis['grant_amt_histogram'] = [{'bin_upper': k, 'count': v, 'pct': (v / total * 100) if total > 0 else 0} for k, v in hist_map.items()]

        return analysis

    def get_contractors_analysis(self) -> Dict[str, Any]:
        """Get analysis for Contractors table"""
        analysis = {}

        # Amount histogram (only if min != max)
        result = self.db_ops.execute_query("""
            SELECT MIN(amount) as min_val, MAX(amount) as max_val
            FROM Contractors
            WHERE amount IS NOT NULL
        """).fetchone()
        if result and result[0] != result[1]:
            hist_result = self.db_ops.execute_query("""
                SELECT histogram(amount, equi_width_bins(?, ?, 10, true)) as hist,
                       (SELECT COUNT(*) FROM Contractors WHERE amount IS NOT NULL) as total
                FROM Contractors WHERE amount IS NOT NULL
            """, (result[0], result[1])).fetchone()
            if hist_result and hist_result[0]:
                hist_map = hist_result[0]
                total = hist_result[1]
                analysis['amount_histogram'] = [{'bin_upper': k, 'count': v, 'pct': (v / total * 100) if total > 0 else 0} for k, v in hist_map.items()]

        return analysis

    def get_political_contributions_analysis(self) -> Dict[str, Any]:
        """Get analysis for PoliticalContributions table"""
        analysis = {}

        # Amount histogram (only if min != max)
        result = self.db_ops.execute_query("""
            SELECT MIN(amount) as min_val, MAX(amount) as max_val
            FROM PoliticalContributions
            WHERE amount IS NOT NULL
        """).fetchone()
        if result and result[0] != result[1]:
            hist_result = self.db_ops.execute_query("""
                SELECT histogram(amount, equi_width_bins(?, ?, 10, true)) as hist,
                       (SELECT COUNT(*) FROM PoliticalContributions WHERE amount IS NOT NULL) as total
                FROM PoliticalContributions WHERE amount IS NOT NULL
            """, (result[0], result[1])).fetchone()
            if hist_result and hist_result[0]:
                hist_map = hist_result[0]
                total = hist_result[1]
                analysis['amount_histogram'] = [{'bin_upper': k, 'count': v, 'pct': (v / total * 100) if total > 0 else 0} for k, v in hist_map.items()]

        return analysis

    def get_addresses_analysis(self) -> Dict[str, Any]:
        """Get analysis for Addresses table"""
        analysis = {}

        # Top 10 states
        result = self.db_ops.execute_query("""
            SELECT state, COUNT(*) as count
            FROM Addresses
            WHERE state IS NOT NULL AND state != ''
            GROUP BY state
            ORDER BY count DESC
            LIMIT 10
        """).fetchall()
        analysis['top_states'] = result

        # Top 10 zip codes
        result = self.db_ops.execute_query("""
            SELECT zip_code, COUNT(*) as count
            FROM Addresses
            WHERE zip_code IS NOT NULL AND zip_code != ''
            GROUP BY zip_code
            ORDER BY count DESC
            LIMIT 10
        """).fetchall()
        analysis['top_zip_codes'] = result

        return analysis

    # Analytics methods for DuckDB
    def get_charity_summary_stats(self, tax_year: int = None) -> Dict[str, Any]:
        """Get summary statistics for charities"""
        where_clause = f"WHERE tax_year = {tax_year}" if tax_year else ""

        query = f"""
            SELECT
                COUNT(*) as total_charities,
                AVG(total_exp) as avg_expenses,
                SUM(total_exp) as total_expenses,
                AVG(officer_comp) as avg_officer_comp,
                COUNT(CASE WHEN foreign_office = 'Y' THEN 1 END) as foreign_offices
            FROM Charities
            {where_clause}
        """

        result = self.db_ops.execute_query(query).fetchone()
        if result:
            return {
                'total_charities': result[0],
                'avg_expenses': result[1],
                'total_expenses': result[2],
                'avg_officer_comp': result[3],
                'foreign_offices': result[4]
            }
        return {}

    def get_top_grant_recipients(self, limit: int = 10) -> List[Tuple]:
        """Get top grant recipients by total amount"""
        result = self.db_ops.execute_query("""
            SELECT grant_ein, SUM(grant_amt) as total_grants, COUNT(*) as grant_count
            FROM Grants
            WHERE grant_ein IS NOT NULL AND grant_ein != ''
            GROUP BY grant_ein
            ORDER BY total_grants DESC
            LIMIT ?
        """, (limit,))
        return result.fetchall()

    def get_geocoding_stats(self) -> Dict[str, int]:
        """Get geocoding completion statistics"""
        result = self.db_ops.execute_query("""
            SELECT
                COUNT(*) as total_addresses,
                COUNT(CASE WHEN geocoding_id IS NOT NULL THEN 1 END) as geocoded,
                COUNT(CASE WHEN geocoding_id IS NULL THEN 1 END) as pending
            FROM Addresses
        """).fetchone()
        if result:
            return {
                'total_addresses': result[0],
                'geocoded': result[1],
                'pending': result[2]
            }
        return {}

    def get_performance_stats(self) -> Dict[str, Any]:
        """Get database performance statistics"""
        stats = {}

        # Get table sizes
        table_sizes = self.db_ops.execute_query("""
            SELECT table_name,
                    estimated_size as size_bytes,
                    ROUND(estimated_size / 1024.0 / 1024.0, 2) as size_mb
            FROM duckdb_tables()
            WHERE table_name IN ('Charities', 'Grants', 'Addresses', 'Officers', 'Geocoding')
        """).fetchall()

        stats['table_sizes'] = {row[0]: {'bytes': row[1], 'mb': row[2]} for row in table_sizes}

        # Get basic stats
        stats['memory_usage'] = self.db_ops.memory_limit
        stats['threads'] = self.db_ops.threads or 'auto'

        return stats

    def get_latest_charities_for_export(self) -> List[Tuple]:
        """Get latest charities for export"""
        # Ensure LatestCharities table exists
        self.db_ops.create_latest_charities_table()

        result = self.db_ops.execute_query("""
            SELECT tax_year, ein, filer_name, receipt_amt, govt_amt, contrib_amt,
                   org_type, total_exp, prog_exp, travel_amt, conferences_amt,
                   officer_comp, comp_pct, comp_ptile, travel_pct, travel_ptile,
                   conferences_pct, conferences_ptile, grants_pct, grants_ptile,
                   foreign_expenses_pct, foreign_expenses_ptile, grift_ratio,
                   total_assets, form_type, denominator, foreign_office, foreign_expenses,
                   grants_to_others, domestic_misrep_flag, xml_name
            FROM LatestCharities
            ORDER BY ein, tax_year
        """)
        return result.fetchall()

    def get_grants_for_export(self) -> List[Tuple]:
        """Get grants for export"""
        result = self.db_ops.execute_query("""
            SELECT filer_ein, filer_name, grant_ein, grant_amt, tax_year,
                   colocator, colocator
            FROM Grants
            ORDER BY filer_ein, tax_year
        """)
        return result.fetchall()

    def get_contractors_for_export(self) -> List[Tuple]:
        """Get contractors for export"""
        result = self.db_ops.execute_query("""
            SELECT filer_ein, name, amount, ein, address, zip_code,
                   po_box, tax_year, colocator
            FROM Contractors
            ORDER BY filer_ein, tax_year
        """)
        return result.fetchall()

    def get_political_contributions_for_export(self) -> List[Tuple]:
        """Get political contributions for export"""
        result = self.db_ops.execute_query("""
            SELECT filer_ein, recipient, amount, recipient_address,
                   recipient_zip, recipient_po_box, tax_year, colocator
            FROM PoliticalContributions
            ORDER BY filer_ein, tax_year
        """)
        return result.fetchall()