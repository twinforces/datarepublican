#!/usr/bin/env python3
"""
bulk_operations.py - Bulk database operations for IRS 990 data processing

This module handles bulk insert operations for performance optimization
in the IRS 990 processor.
"""

from typing import List
from database_operations import DatabaseOperations


class BulkOperations:
    """Handles bulk database operations for IRS 990 data"""

    def __init__(self, db_ops: DatabaseOperations):
        self.db_ops = db_ops

    def bulk_insert_xml_files(self, xml_files):
        """Bulk insert XML files"""
        if not xml_files:
            return

        xml_data = [(xf.zip_id, xf.filename, xf.internal_path, xf.ein, xf.tax_year, xf.form_type,
                    xf.processed, xf.processing_version, xf.error_message)
                   for xf in xml_files]
        self.db_ops.db_cursor.executemany("""
            INSERT OR IGNORE INTO XmlFiles (zip_id, filename, internal_path, ein, tax_year, form_type, processed, processing_version, error_message)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, xml_data)
        self.db_ops.db_conn.commit()

    def bulk_insert_charities(self, charities):
        """Bulk insert charities"""
        if not charities:
            return

        charity_data = [(c.ein, c.tax_year, c.filer_name, c.business_name_line1, c.business_name_line2,
                        c.receipt_amt, c.govt_amt, c.contrib_amt, c.org_type, c.total_exp,
                        c.prog_exp, c.travel_amt, c.conferences_amt, c.officer_comp, c.comp_pct,
                        c.comp_ptile, c.travel_pct, c.travel_ptile, c.conferences_pct,
                        c.conferences_ptile, c.grants_pct, c.grants_ptile, c.foreign_expenses_pct,
                        c.foreign_expenses_ptile, c.grift_ratio, c.total_assets, c.form_type,
                        c.denominator, c.foreign_office, c.foreign_expenses, c.grants_to_others,
                        c.domestic_misrep_flag, c.xml_name)
                       for c in charities]

        self.db_ops.db_cursor.executemany("""
            INSERT INTO Charities (ein, tax_year, filer_name, business_name_line1, business_name_line2,
                                  receipt_amt, govt_amt, contrib_amt, org_type, total_exp,
                                  prog_exp, travel_amt, conferences_amt, officer_comp, comp_pct,
                                  comp_ptile, travel_pct, travel_ptile, conferences_pct,
                                  conferences_ptile, grants_pct, grants_ptile, foreign_expenses_pct,
                                  foreign_expenses_ptile, grift_ratio, total_assets, form_type,
                                  denominator, foreign_office, foreign_expenses, grants_to_others,
                                  domestic_misrep_flag, xml_name)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, charity_data)
        self.db_ops.db_conn.commit()

    def bulk_insert_officers(self, officers):
        """Bulk insert officers"""
        if not officers:
            return

        officer_data = [(o.charity_id, o.first_name, o.last_name, o.compensation, o.tax_year)
                       for o in officers]
        self.db_ops.db_cursor.executemany("""
            INSERT INTO Officers (charity_id, first_name, last_name, compensation, tax_year)
            VALUES (?, ?, ?, ?, ?)
        """, officer_data)
        self.db_ops.db_conn.commit()

    def bulk_insert_grants(self, grants):
        """Bulk insert grants"""
        if not grants:
            return

        grant_data = [(g.filer_ein, g.filer_name, g.grant_ein, g.grant_amt, g.tax_year,
                      g.filer_colocator, g.grantee_colocator) for g in grants]
        self.db_ops.db_cursor.executemany("""
            INSERT INTO Grants (filer_ein, filer_name, grant_ein, grant_amt, tax_year,
                              filer_colocator, grantee_colocator)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, grant_data)
        self.db_ops.db_conn.commit()

    def bulk_insert_contractors(self, contractors):
        """Bulk insert contractors"""
        if not contractors:
            return

        contractor_data = [(c.filer_ein, c.name, c.amount, c.ein, c.address, c.zip_code,
                          c.po_box, c.tax_year) for c in contractors]
        self.db_ops.db_cursor.executemany("""
            INSERT INTO Contractors (filer_ein, name, amount, ein, address, zip_code,
                                    po_box, tax_year)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, contractor_data)
        self.db_ops.db_conn.commit()

    def bulk_insert_political_contributions(self, contributions):
        """Bulk insert political contributions"""
        if not contributions:
            return

        contribution_data = [(c.filer_ein, c.recipient, c.amount, c.recipient_address,
                            c.recipient_zip, c.recipient_po_box, c.tax_year, c.colocator) for c in contributions]
        self.db_ops.db_cursor.executemany("""
            INSERT INTO PoliticalContributions (filer_ein, recipient, amount,
                                              recipient_address, recipient_zip,
                                              recipient_po_box, tax_year, colocator)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, contribution_data)
        self.db_ops.db_conn.commit()