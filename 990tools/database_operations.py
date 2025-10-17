#!/usr/bin/env python3
"""
database_operations.py - Database operations for IRS 990 data processing

This module contains all database-related operations for the IRS 990 processor,
including CRUD operations for all data models.
"""

import sqlite3
from typing import Optional, List, Tuple
from datetime import datetime
from pathlib import Path

import sys
import os
sys.path.append(os.path.dirname(__file__))
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any, Tuple
from datetime import datetime

# Define dataclasses locally to avoid import issues
@dataclass
class ZipFile:
    filename: str
    file_path: str
    tax_year: int
    file_size: Optional[int] = None
    checksum: Optional[str] = None
    download_date: Optional[datetime] = None
    processed_date: Optional[datetime] = None
    status: str = "pending"
    zip_id: int = 0

@dataclass
class XMLFile:
    zip_id: int
    filename: str
    internal_path: str
    ein: Optional[str] = None
    tax_year: int = 0
    form_type: str = "Unknown"
    processed: bool = False
    processing_version: int = 1
    error_message: Optional[str] = None
    xml_id: int = 0

@dataclass
class Address:
    ein: str
    name: str
    street: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    zip_code: Optional[str] = None
    po_box: Optional[str] = None
    canonical_address: str
    address_type: str = "charity"
    geocoding_id: Optional[int] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    colocator: Optional[str] = None
    address_id: int = 0

@dataclass
class Charity:
    ein: str
    tax_year: int
    filer_name: str
    receipt_amt: Optional[float] = None
    govt_amt: Optional[float] = None
    contrib_amt: Optional[float] = None
    org_type: Optional[str] = None
    total_exp: Optional[float] = None
    prog_exp: Optional[float] = None
    travel_amt: Optional[float] = None
    conferences_amt: Optional[float] = None
    officer_comp: Optional[float] = None
    comp_pct: Optional[float] = None
    comp_ptile: Optional[float] = None
    travel_pct: Optional[float] = None
    travel_ptile: Optional[float] = None
    conferences_pct: Optional[float] = None
    conferences_ptile: Optional[float] = None
    grants_pct: Optional[float] = None
    grants_ptile: Optional[float] = None
    foreign_expenses_pct: Optional[float] = None
    foreign_expenses_ptile: Optional[float] = None
    grift_ratio: Optional[float] = None
    total_assets: Optional[float] = None
    form_type: str = "Unknown"
    denominator: Optional[float] = None
    foreign_office: Optional[bool] = None
    foreign_expenses: Optional[float] = None
    grants_to_others: Optional[float] = None
    domestic_misrep_flag: Optional[bool] = None
    xml_name: Optional[str] = None
    charity_id: int = 0

@dataclass
class Grant:
    filer_ein: str
    filer_name: str
    grant_ein: Optional[str] = None
    grant_amt: float = 0
    tax_year: int = 0
    filer_colocator: Optional[str] = None
    grantee_colocator: Optional[str] = None
    grant_id: int = 0

@dataclass
class Officer:
    first_name: str
    last_name: str
    compensation: float
    tax_year: int
    charity_id: int = 0
    officer_id: int = 0

@dataclass
class Contractor:
    filer_ein: str
    name: str
    amount: float
    ein: Optional[str] = None
    address: Optional[str] = None
    zip_code: Optional[str] = None
    po_box: Optional[str] = None
    tax_year: int = 0
    colocator: Optional[str] = None
    contractor_id: int = 0

@dataclass
class PoliticalContribution:
    filer_ein: str
    recipient: str
    amount: float
    recipient_address: Optional[str] = None
    recipient_zip: Optional[str] = None
    recipient_po_box: Optional[str] = None
    tax_year: int = 0
    colocator: Optional[str] = None
    political_id: int = 0


class DatabaseOperations:
    """Handles all database operations for IRS 990 data processing"""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self.db_conn: sqlite3.Connection
        self.db_cursor: sqlite3.Cursor
        self._init_connection()

    def _init_connection(self):
        """Initialize database connection"""
        self.db_conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.db_cursor = self.db_conn.cursor()
        # Enable foreign keys
        self.db_cursor.execute("PRAGMA foreign_keys = ON")

    def __del__(self):
        """Cleanup database connection"""
        if hasattr(self, 'db_conn') and self.db_conn:
            self.db_conn.close()

    # ZipFile operations
    def insert_zip_file(self, zip_file: ZipFile) -> int:
        """Insert ZipFile into database, handling duplicates"""
        # Check if ZIP file already exists
        self.db_cursor.execute("SELECT zip_id FROM ZipFiles WHERE filename = ?", (zip_file.filename,))
        existing = self.db_cursor.fetchone()
        if existing:
            zip_file.zip_id = existing[0]
            return existing[0]

        self.db_cursor.execute("""
            INSERT INTO ZipFiles (filename, file_path, tax_year, file_size, checksum,
                                download_date, processed_date, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (zip_file.filename, zip_file.file_path, zip_file.tax_year,
              zip_file.file_size, zip_file.checksum, zip_file.download_date.isoformat() if zip_file.download_date else None,
              zip_file.processed_date.isoformat() if zip_file.processed_date else None, zip_file.status))
        zip_file.zip_id = self.db_cursor.lastrowid or 0
        self.db_conn.commit()
        return zip_file.zip_id

    def update_zip_status(self, zip_id: int, status: str):
        """Update ZIP file processing status"""
        self.db_cursor.execute("""
            UPDATE ZipFiles SET status = ?, processed_date = ?
            WHERE zip_id = ?
        """, (status, datetime.now().isoformat(), zip_id))
        self.db_conn.commit()

    # XMLFile operations
    def insert_xml_file(self, xml_file: XMLFile) -> int:
        """Insert XMLFile into database, handling duplicates"""
        # Check if XML file already exists
        self.db_cursor.execute("SELECT xml_id FROM XmlFiles WHERE zip_id = ? AND filename = ?",
                              (xml_file.zip_id, xml_file.filename))
        existing = self.db_cursor.fetchone()
        if existing:
            xml_file.xml_id = existing[0]
            return existing[0]

        self.db_cursor.execute("""
            INSERT INTO XmlFiles (zip_id, filename, internal_path, ein, tax_year,
                                form_type, processed, processing_version, error_message)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (xml_file.zip_id, xml_file.filename, xml_file.internal_path,
              xml_file.ein, xml_file.tax_year, xml_file.form_type,
              xml_file.processed, xml_file.processing_version, xml_file.error_message))
        xml_file.xml_id = self.db_cursor.lastrowid or 0
        self.db_conn.commit()
        return xml_file.xml_id

    def get_unprocessed_xml_files(self, processing_version: int, max_files: Optional[int] = None) -> List[Tuple]:
        """Get unprocessed XML files"""
        query = """
            SELECT xml_id, zip_id, filename, internal_path
            FROM XmlFiles
            WHERE processed = FALSE OR processing_version < ?
            ORDER BY zip_id, filename
        """
        params = (processing_version,)

        if max_files:
            query += " LIMIT ?"
            params = (processing_version, max_files)

        self.db_cursor.execute(query, params)
        return self.db_cursor.fetchall()

    def mark_xml_processed(self, xml_id: int, processing_version: int):
        """Mark XML file as processed"""
        self.db_cursor.execute("""
            UPDATE XmlFiles SET processed = TRUE, processing_version = ?
            WHERE xml_id = ?
        """, (processing_version, xml_id))
        self.db_conn.commit()

    def mark_xml_error(self, xml_id: int, processing_version: int, error_msg: str):
        """Mark XML file as having an error"""
        self.db_cursor.execute("""
            UPDATE XmlFiles SET processed = TRUE, processing_version = ?, error_message = ?
            WHERE xml_id = ?
        """, (processing_version, error_msg, xml_id))
        self.db_conn.commit()

    def update_xml_ein(self, xml_id: int, ein: str):
        """Update XML file with EIN after parsing"""
        self.db_cursor.execute("UPDATE XmlFiles SET ein = ? WHERE xml_id = ?", (ein, xml_id))
        self.db_conn.commit()

    # Address operations
    def insert_address(self, address: Address) -> int:
        """Insert Address into database, avoiding duplicates"""
        # Check for existing address
        self.db_cursor.execute("""
            SELECT address_id FROM Addresses
            WHERE ein = ? AND canonical_address = ?
        """, (address.ein, address.canonical_address))

        existing = self.db_cursor.fetchone()
        if existing:
            address.address_id = existing[0]
            return existing[0]

        self.db_cursor.execute("""
            INSERT INTO Addresses (ein, name, street, city, state, zip_code, po_box,
                                  canonical_address, address_type, geocoding_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (address.ein, address.name, address.street, address.city, address.state,
              address.zip_code, address.po_box, address.canonical_address,
              address.address_type, address.geocoding_id))
        address.address_id = self.db_cursor.lastrowid or 0
        self.db_conn.commit()
        return address.address_id

    def get_addresses_for_geocoding(self) -> List[Tuple]:
        """Get addresses that need geocoding"""
        self.db_cursor.execute("""
            SELECT address_id, canonical_address, po_box, zip_code
            FROM Addresses
            WHERE geocoding_id IS NULL
            AND (po_box IS NULL OR po_box = '')  -- Skip PO boxes
            ORDER BY address_id
        """)
        return self.db_cursor.fetchall()

    def update_address_geocoding(self, address_id: int, geocoding_id: int, latitude: float = None,
                                longitude: float = None, colocator: str = None):
        """Update address with geocoding information"""
        self.db_cursor.execute("""
            UPDATE Addresses SET geocoding_id = ?, latitude = ?, longitude = ?, colocator = ?
            WHERE address_id = ?
        """, (geocoding_id, latitude, longitude, colocator, address_id))
        self.db_conn.commit()

    # Charity operations
    def insert_charity(self, charity: Charity) -> int:
        """Insert Charity into database, handling duplicates by EIN and tax_year"""
        # Check if charity already exists for this EIN and tax_year
        self.db_cursor.execute("""
            SELECT charity_id FROM Charities
            WHERE ein = ? AND tax_year = ?
        """, (charity.ein, charity.tax_year))

        existing = self.db_cursor.fetchone()
        if existing:
            # Update existing record instead of inserting duplicate
            charity.charity_id = existing[0]
            self.db_cursor.execute("""
                UPDATE Charities SET
                    filer_name = ?, receipt_amt = ?, govt_amt = ?, contrib_amt = ?,
                    org_type = ?, total_exp = ?, prog_exp = ?, travel_amt = ?,
                    conferences_amt = ?, officer_comp = ?, comp_pct = ?, comp_ptile = ?,
                    travel_pct = ?, travel_ptile = ?, conferences_pct = ?, conferences_ptile = ?,
                    grants_pct = ?, grants_ptile = ?, foreign_expenses_pct = ?,
                    foreign_expenses_ptile = ?, grift_ratio = ?, total_assets = ?,
                    form_type = ?, denominator = ?, foreign_office = ?, foreign_expenses = ?,
                    grants_to_others = ?, domestic_misrep_flag = ?, xml_name = ?
                WHERE charity_id = ?
            """, (charity.filer_name, charity.receipt_amt, charity.govt_amt,
                  charity.contrib_amt, charity.org_type, charity.total_exp,
                  charity.prog_exp, charity.travel_amt, charity.conferences_amt,
                  charity.officer_comp, charity.comp_pct, charity.comp_ptile,
                  charity.travel_pct, charity.travel_ptile, charity.conferences_pct,
                  charity.conferences_ptile, charity.grants_pct, charity.grants_ptile,
                  charity.foreign_expenses_pct, charity.foreign_expenses_ptile,
                  charity.grift_ratio, charity.total_assets, charity.form_type,
                  charity.denominator, charity.foreign_office, charity.foreign_expenses,
                  charity.grants_to_others, charity.domestic_misrep_flag, charity.xml_name,
                  charity.charity_id))
            self.db_conn.commit()
            return charity.charity_id

        # Insert new charity record
        self.db_cursor.execute("""
            INSERT INTO Charities (ein, tax_year, filer_name, receipt_amt, govt_amt,
                                  contrib_amt, org_type, total_exp, prog_exp, travel_amt,
                                  conferences_amt, officer_comp, comp_pct, comp_ptile,
                                  travel_pct, travel_ptile, conferences_pct, conferences_ptile,
                                  grants_pct, grants_ptile, foreign_expenses_pct,
                                  foreign_expenses_ptile, grift_ratio, total_assets,
                                  form_type, denominator, foreign_office, foreign_expenses,
                                  grants_to_others, domestic_misrep_flag, xml_name)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (charity.ein, charity.tax_year, charity.filer_name, charity.receipt_amt,
              charity.govt_amt, charity.contrib_amt, charity.org_type, charity.total_exp,
              charity.prog_exp, charity.travel_amt, charity.conferences_amt,
              charity.officer_comp, charity.comp_pct, charity.comp_ptile,
              charity.travel_pct, charity.travel_ptile, charity.conferences_pct,
              charity.conferences_ptile, charity.grants_pct, charity.grants_ptile,
              charity.foreign_expenses_pct, charity.foreign_expenses_ptile,
              charity.grift_ratio, charity.total_assets, charity.form_type,
              charity.denominator, charity.foreign_office, charity.foreign_expenses,
              charity.grants_to_others, charity.domestic_misrep_flag, charity.xml_name))
        charity.charity_id = self.db_cursor.lastrowid or 0
        self.db_conn.commit()
        return charity.charity_id

    def update_charity_percentiles(self, ein: str, tax_year: int, comp_ptile: float = None,
                                  travel_ptile: float = None, conferences_ptile: float = None,
                                  grants_ptile: float = None, foreign_ptile: float = None):
        """Update charity percentile rankings"""
        self.db_cursor.execute("""
            UPDATE Charities SET
                comp_ptile_value = ?,
                travel_ptile_value = ?,
                conferences_ptile_value = ?,
                grants_ptile_value = ?,
                foreign_expenses_ptile_value = ?
            WHERE ein = ? AND tax_year = ?
        """, (comp_ptile, travel_ptile, conferences_ptile, grants_ptile, foreign_ptile, ein, tax_year))
        self.db_conn.commit()

    def get_charities_for_percentiles(self) -> List[Tuple]:
        """Get charities for percentile calculation"""
        self.db_cursor.execute("""
            SELECT org_type, tax_year, ein, comp_pct, travel_pct, conferences_pct, grants_pct, foreign_expenses_pct
            FROM Charities
            WHERE denominator > 0
            ORDER BY org_type, tax_year
        """)
        return self.db_cursor.fetchall()

    def create_latest_charities_table(self):
        """Create LatestCharities table with most recent filings"""
        self.db_cursor.execute("""
            CREATE TABLE IF NOT EXISTS LatestCharities AS
            SELECT c.*
            FROM Charities c
            INNER JOIN (
                SELECT ein, MAX(tax_year) as max_year
                FROM Charities
                GROUP BY ein
            ) latest ON c.ein = latest.ein AND c.tax_year = latest.max_year
        """)
        self.db_conn.commit()

    # Grant operations
    def insert_grant(self, grant: Grant) -> int:
        """Insert Grant into database"""
        self.db_cursor.execute("""
            INSERT INTO Grants (filer_ein, filer_name, grant_ein, grant_amt, tax_year,
                              filer_colocator, grantee_colocator)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (grant.filer_ein, grant.filer_name, grant.grant_ein, grant.grant_amt,
              grant.tax_year, grant.filer_colocator, grant.grantee_colocator))
        grant.grant_id = self.db_cursor.lastrowid or 0
        self.db_conn.commit()
        return grant.grant_id

    def update_grant_ein(self, grant_id: int, grant_ein: str):
        """Update grant with matched EIN"""
        self.db_cursor.execute("""
            UPDATE Grants SET grant_ein = ? WHERE grant_id = ?
        """, (grant_ein, grant_id))
        self.db_conn.commit()

    def get_grants_without_ein(self) -> List[Tuple]:
        """Get grants with unknown EINs for matching"""
        self.db_cursor.execute("""
            SELECT grant_id, filer_ein, grant_amt, tax_year
            FROM Grants
            WHERE grant_ein IS NULL OR grant_ein = ''
        """)
        return self.db_cursor.fetchall()

    # Officer operations
    def insert_officer(self, officer: Officer) -> int:
        """Insert Officer into database"""
        # Debug: Validate charity_id before insertion
        if officer.charity_id is None or officer.charity_id <= 0:
            raise ValueError(f"Invalid charity_id {officer.charity_id} for officer {officer.first_name} {officer.last_name}")

        # Debug: Check if charity_id exists in Charities table
        self.db_cursor.execute("SELECT 1 FROM Charities WHERE charity_id = ?", (officer.charity_id,))
        if not self.db_cursor.fetchone():
            raise ValueError(f"charity_id {officer.charity_id} does not exist in Charities table for officer {officer.first_name} {officer.last_name}")

        self.db_cursor.execute("""
            INSERT INTO Officers (charity_id, first_name, last_name, compensation, tax_year)
            VALUES (?, ?, ?, ?, ?)
        """, (officer.charity_id, officer.first_name, officer.last_name,
              officer.compensation, officer.tax_year))
        officer.officer_id = self.db_cursor.lastrowid or 0
        self.db_conn.commit()
        return officer.officer_id

    # Contractor operations
    def insert_contractor(self, contractor: Contractor) -> int:
        """Insert Contractor into database"""
        self.db_cursor.execute("""
            INSERT INTO Contractors (filer_ein, name, amount, ein, address, zip_code,
                                    po_box, tax_year, colocator)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (contractor.filer_ein, contractor.name, contractor.amount, contractor.ein,
              contractor.address, contractor.zip_code, contractor.po_box, contractor.tax_year, contractor.colocator))
        contractor.contractor_id = self.db_cursor.lastrowid or 0
        self.db_conn.commit()
        return contractor.contractor_id

    # Political Contribution operations
    def insert_political_contribution(self, contribution: PoliticalContribution) -> int:
        """Insert PoliticalContribution into database"""
        self.db_cursor.execute("""
            INSERT INTO PoliticalContributions (filer_ein, recipient, amount,
                                              recipient_address, recipient_zip,
                                              recipient_po_box, tax_year, colocator)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (contribution.filer_ein, contribution.recipient, contribution.amount,
              contribution.recipient_address, contribution.recipient_zip,
              contribution.recipient_po_box, contribution.tax_year, contribution.colocator))
        contribution.political_id = self.db_cursor.lastrowid or 0
        self.db_conn.commit()
        return contribution.political_id

    # Geocoding operations
    def insert_geocoding_record(self, address_hash: int, normalized_address: str,
                               latitude: float = None, longitude: float = None,
                               status: str = 'success') -> int:
        """Insert geocoding record"""
        self.db_cursor.execute("""
            INSERT INTO Geocoding (address_hash, normalized_address, latitude, longitude, geocoding_status)
            VALUES (?, ?, ?, ?, ?)
        """, (address_hash, normalized_address, latitude, longitude, status))
        geocoding_id = self.db_cursor.lastrowid or 0
        self.db_conn.commit()
        return geocoding_id

    # Bulk operations for performance
    def bulk_insert_xml_files(self, xml_files: List[XMLFile]):
        """Bulk insert XML files"""
        if not xml_files:
            return

        xml_data = [(xf.zip_id, xf.filename, xf.internal_path, xf.ein, xf.tax_year, xf.form_type,
                    xf.processed, xf.processing_version, xf.error_message)
                   for xf in xml_files]
        self.db_cursor.executemany("""
            INSERT OR IGNORE INTO XmlFiles (zip_id, filename, internal_path, ein, tax_year, form_type, processed, processing_version, error_message)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, xml_data)
        self.db_conn.commit()

    def bulk_insert_charities(self, charities: List[Charity]):
        """Bulk insert charities"""
        if not charities:
            return

        charity_data = [(c.ein, c.tax_year, c.filer_name, c.receipt_amt, c.govt_amt,
                        c.contrib_amt, c.org_type, c.total_exp, c.prog_exp, c.travel_amt,
                        c.conferences_amt, c.officer_comp, c.comp_pct, c.comp_ptile,
                        c.travel_pct, c.travel_ptile, c.conferences_pct, c.conferences_ptile,
                        c.grants_pct, c.grants_ptile, c.foreign_expenses_pct,
                        c.foreign_expenses_ptile, c.grift_ratio, c.total_assets,
                        c.form_type, c.denominator, c.foreign_office, c.foreign_expenses,
                        c.grants_to_others, c.domestic_misrep_flag, c.xml_name)
                       for c in charities]
        self.db_cursor.executemany("""
            INSERT INTO Charities (ein, tax_year, filer_name, receipt_amt, govt_amt,
                                  contrib_amt, org_type, total_exp, prog_exp, travel_amt,
                                  conferences_amt, officer_comp, comp_pct, comp_ptile,
                                  travel_pct, travel_ptile, conferences_pct, conferences_ptile,
                                  grants_pct, grants_ptile, foreign_expenses_pct,
                                  foreign_expenses_ptile, grift_ratio, total_assets,
                                  form_type, denominator, foreign_office, foreign_expenses,
                                  grants_to_others, domestic_misrep_flag, xml_name)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, charity_data)
        self.db_conn.commit()

    def bulk_insert_officers(self, officers: List[Officer]):
        """Bulk insert officers"""
        if not officers:
            return

        officer_data = [(o.charity_id, o.first_name, o.last_name, o.compensation, o.tax_year)
                       for o in officers]
        self.db_cursor.executemany("""
            INSERT INTO Officers (charity_id, first_name, last_name, compensation, tax_year)
            VALUES (?, ?, ?, ?, ?)
        """, officer_data)
        self.db_conn.commit()

    def bulk_insert_grants(self, grants: List[Grant]):
        """Bulk insert grants"""
        if not grants:
            return

        grant_data = [(g.filer_ein, g.filer_name, g.grant_ein, g.grant_amt, g.tax_year,
                      g.filer_colocator, g.grantee_colocator) for g in grants]
        self.db_cursor.executemany("""
            INSERT INTO Grants (filer_ein, filer_name, grant_ein, grant_amt, tax_year,
                              filer_colocator, grantee_colocator)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, grant_data)
        self.db_conn.commit()

    def bulk_insert_contractors(self, contractors: List[Contractor]):
        """Bulk insert contractors"""
        if not contractors:
            return

        contractor_data = [(c.filer_ein, c.name, c.amount, c.ein, c.address, c.zip_code,
                          c.po_box, c.tax_year) for c in contractors]
        self.db_cursor.executemany("""
            INSERT INTO Contractors (filer_ein, name, amount, ein, address, zip_code,
                                    po_box, tax_year)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, contractor_data)
        self.db_conn.commit()

    def bulk_insert_political_contributions(self, contributions: List[PoliticalContribution]):
        """Bulk insert political contributions"""
        if not contributions:
            return

        contribution_data = [(c.filer_ein, c.recipient, c.amount, c.recipient_address,
                            c.recipient_zip, c.recipient_po_box, c.tax_year, c.colocator) for c in contributions]
        self.db_cursor.executemany("""
            INSERT INTO PoliticalContributions (filer_ein, recipient, amount,
                                              recipient_address, recipient_zip,
                                              recipient_po_box, tax_year, colocator)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, contribution_data)
        self.db_conn.commit()

    # Export operations
    def get_latest_charities_for_export(self) -> List[Tuple]:
        """Get latest charities for TSV export"""
        self.db_cursor.execute("""
            SELECT
                tax_year, ein, filer_name, receipt_amt, govt_amt, contrib_amt,
                org_type, total_exp, prog_exp, travel_amt, conferences_amt,
                officer_comp, comp_pct, comp_ptile_value, travel_pct, travel_ptile_value,
                conferences_pct, conferences_ptile_value, grants_pct, grants_ptile_value,
                foreign_expenses_pct, foreign_expenses_ptile_value, grift_ratio,
                total_assets, form_type, denominator, foreign_office, foreign_expenses,
                grants_to_others, domestic_misrep_flag, xml_name
            FROM LatestCharities
            ORDER BY ein
        """)
        return self.db_cursor.fetchall()

    def get_grants_for_export(self) -> List[Tuple]:
        """Get grants for TSV export"""
        self.db_cursor.execute("""
            SELECT
                g.filer_ein, g.filer_name, g.grant_ein, g.grant_amt, g.tax_year,
                g.filer_colocator, g.grantee_colocator
            FROM Grants g
            JOIN LatestCharities lc ON g.filer_ein = lc.ein
            ORDER BY g.filer_ein, g.tax_year
        """)
        return self.db_cursor.fetchall()

    def get_contractors_for_export(self) -> List[Tuple]:
        """Get contractors for TSV export"""
        self.db_cursor.execute("""
            SELECT
                c.filer_ein, c.name, c.amount, c.ein, c.address, c.zip_code,
                c.po_box, c.tax_year, c.colocator
            FROM Contractors c
            JOIN LatestCharities lc ON c.filer_ein = lc.ein
            ORDER BY c.filer_ein, c.tax_year
        """)
        return self.db_cursor.fetchall()

    def get_political_contributions_for_export(self) -> List[Tuple]:
        """Get political contributions for TSV export"""
        self.db_cursor.execute("""
            SELECT
                pc.filer_ein, pc.recipient, pc.amount, pc.recipient_address,
                pc.recipient_zip, pc.recipient_po_box, pc.tax_year, pc.colocator
            FROM PoliticalContributions pc
            JOIN LatestCharities lc ON pc.filer_ein = lc.ein
            ORDER BY pc.filer_ein, pc.tax_year
        """)
        return self.db_cursor.fetchall()