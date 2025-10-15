"""
Database managers for ZIP and XML file metadata storage and indexing.
Replaces JSON-based indexing system with SQLite database storage.
"""

import sqlite3
import os
import hashlib
import zipfile
import re
from datetime import datetime
from typing import List, Optional, Dict, Any, Tuple
from dataclasses import asdict
from models import ZipFile, XmlFile


class DatabaseManager:
    """Base class for database operations with connection management."""

    def __init__(self, db_path: str = "/Volumes/Data/final/pipeline_progress.db"):
        """Initialize database connection and create tables if needed."""
        self.db_path = db_path
        self._ensure_db_exists()

    def _ensure_db_exists(self):
        """Create database and tables if they don't exist."""
        with sqlite3.connect(self.db_path) as conn:
            # Enable foreign key constraints
            conn.execute('PRAGMA foreign_keys = ON')

            # Create ZipFiles table
            conn.execute('''
                CREATE TABLE IF NOT EXISTS ZipFiles (
                    zip_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    filename TEXT NOT NULL UNIQUE,
                    file_path TEXT NOT NULL,
                    tax_year INTEGER NOT NULL,
                    file_size INTEGER,
                    checksum TEXT,
                    download_date DATETIME,
                    processed_date DATETIME,
                    status TEXT DEFAULT 'downloaded' CHECK(status IN ('downloaded', 'processed', 'error')),
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            ''')

            # Create XmlFiles table
            conn.execute('''
                CREATE TABLE IF NOT EXISTS XmlFiles (
                    xml_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    zip_id INTEGER NOT NULL,
                    filename TEXT NOT NULL,
                    internal_path TEXT NOT NULL,
                    ein TEXT,
                    tax_year INTEGER,
                    form_type TEXT,
                    processed BOOLEAN DEFAULT FALSE,
                    error_message TEXT,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (zip_id) REFERENCES ZipFiles(zip_id) ON DELETE CASCADE,
                    UNIQUE(zip_id, filename)
                )
            ''')

            # Create indexes for performance
            conn.execute('CREATE INDEX IF NOT EXISTS idx_zipfiles_tax_year ON ZipFiles(tax_year)')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_zipfiles_status ON ZipFiles(status)')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_xmlfiles_zip_id ON XmlFiles(zip_id)')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_xmlfiles_ein ON XmlFiles(ein)')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_xmlfiles_tax_year ON XmlFiles(tax_year)')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_xmlfiles_processed ON XmlFiles(processed)')

            conn.commit()

    def _row_to_zipfile(self, row) -> ZipFile:
        """Convert database row to ZipFile object."""
        return ZipFile(
            zip_id=row[0],
            filename=row[1],
            file_path=row[2],
            tax_year=row[3],
            file_size=row[4],
            checksum=row[5],
            download_date=datetime.fromisoformat(row[6]) if row[6] else None,
            processed_date=datetime.fromisoformat(row[7]) if row[7] else None,
            status=row[8],
            created_at=datetime.fromisoformat(row[9]) if row[9] else None,
        )

    def _row_to_xmlfile(self, row) -> XmlFile:
        """Convert database row to XmlFile object."""
        return XmlFile(
            xml_id=row[0],
            zip_id=row[1],
            filename=row[2],
            internal_path=row[3],
            ein=row[4],
            tax_year=row[5],
            form_type=row[6],
            processed=bool(row[7]),
            error_message=row[8],
            created_at=datetime.fromisoformat(row[9]) if row[9] else None,
        )


class ZipFileManager(DatabaseManager):
    """Manager class for ZIP file operations and metadata."""

    def add_zip_file(self, filename: str, file_path: str, tax_year: int,
                    file_size: Optional[int] = None, checksum: Optional[str] = None,
                    download_date: Optional[datetime] = None) -> ZipFile:
        """Add a ZIP file to the database."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute('''
                INSERT INTO ZipFiles
                (filename, file_path, tax_year, file_size, checksum, download_date)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(filename) DO UPDATE SET
                    file_path = excluded.file_path,
                    tax_year = excluded.tax_year,
                    file_size = excluded.file_size,
                    checksum = excluded.checksum,
                    download_date = excluded.download_date
            ''', (filename, file_path, tax_year, file_size, checksum,
                  download_date.isoformat() if download_date else None))

            zip_id = cursor.lastrowid
            if not zip_id:
                # Get existing ID if conflict occurred
                cursor = conn.execute('SELECT zip_id FROM ZipFiles WHERE filename = ?', (filename,))
                zip_id = cursor.fetchone()[0]

            # Retrieve the full record
            cursor = conn.execute('''
                SELECT zip_id, filename, file_path, tax_year, file_size, checksum,
                       download_date, processed_date, status, created_at, updated_at
                FROM ZipFiles WHERE zip_id = ?
            ''', (zip_id,))

            row = cursor.fetchone()
            return self._row_to_zipfile(row)

    def get_zip_file(self, zip_id: int) -> Optional[ZipFile]:
        """Get ZIP file by ID."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute('''
                SELECT zip_id, filename, file_path, tax_year, file_size, checksum,
                       download_date, processed_date, status, created_at
                FROM ZipFiles WHERE zip_id = ?
            ''', (zip_id,))

            row = cursor.fetchone()
            return self._row_to_zipfile(row) if row else None

    def get_zip_file_by_filename(self, filename: str) -> Optional[ZipFile]:
        """Get ZIP file by filename."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute('''
                SELECT zip_id, filename, file_path, tax_year, file_size, checksum,
                       download_date, processed_date, status, created_at
                FROM ZipFiles WHERE filename = ?
            ''', (filename,))

            row = cursor.fetchone()
            return self._row_to_zipfile(row) if row else None

    def get_zip_files_by_year(self, tax_year: int) -> List[ZipFile]:
        """Get all ZIP files for a specific tax year."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute('''
                SELECT zip_id, filename, file_path, tax_year, file_size, checksum,
                       download_date, processed_date, status, created_at
                FROM ZipFiles WHERE tax_year = ? ORDER BY filename
            ''', (tax_year,))

            return [self._row_to_zipfile(row) for row in cursor.fetchall()]

    def update_zip_status(self, zip_id: int, status: str,
                         processed_date: Optional[datetime] = None) -> ZipFile:
        """Update ZIP file status."""
        now = datetime.now().isoformat()
        processed_iso = processed_date.isoformat() if processed_date else (now if status == 'processed' else None)

        with sqlite3.connect(self.db_path) as conn:
            conn.execute('''
                UPDATE ZipFiles
                SET status = ?, processed_date = ?, updated_at = ?
                WHERE zip_id = ?
            ''', (status, processed_iso, now, zip_id))

            # Return updated record
            return self.get_zip_file(zip_id)

    def delete_zip_file(self, zip_id: int) -> bool:
        """Delete ZIP file record (cascades to XML files)."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute('DELETE FROM ZipFiles WHERE zip_id = ?', (zip_id,))
            return cursor.rowcount > 0

    def get_all_zip_files(self) -> List[ZipFile]:
        """Get all ZIP files."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute('''
                SELECT zip_id, filename, file_path, tax_year, file_size, checksum,
                       download_date, processed_date, status, created_at
                FROM ZipFiles ORDER BY tax_year, filename
            ''')

            return [self._row_to_zipfile(row) for row in cursor.fetchall()]

    def get_zip_files_by_status(self, status: str) -> List[ZipFile]:
        """Get ZIP files by status."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute('''
                SELECT zip_id, filename, file_path, tax_year, file_size, checksum,
                       download_date, processed_date, status, created_at
                FROM ZipFiles WHERE status = ? ORDER BY tax_year, filename
            ''', (status,))

            return [self._row_to_zipfile(row) for row in cursor.fetchall()]


class XmlFileManager(DatabaseManager):
    """Manager class for XML file operations and metadata."""

    def add_xml_file(self, zip_id: int, filename: str, internal_path: str,
                    ein: Optional[str] = None, tax_year: Optional[int] = None,
                    form_type: Optional[str] = None) -> XmlFile:
        """Add an XML file to the database."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute('''
                INSERT INTO XmlFiles
                (zip_id, filename, internal_path, ein, tax_year, form_type)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(zip_id, filename) DO UPDATE SET
                    internal_path = excluded.internal_path,
                    ein = excluded.ein,
                    tax_year = excluded.tax_year,
                    form_type = excluded.form_type
            ''', (zip_id, filename, internal_path, ein, tax_year, form_type))

            xml_id = cursor.lastrowid
            if not xml_id:
                # Get existing ID if conflict occurred
                cursor = conn.execute('SELECT xml_id FROM XmlFiles WHERE zip_id = ? AND filename = ?',
                                    (zip_id, filename))
                xml_id = cursor.fetchone()[0]

            # Retrieve the full record
            cursor = conn.execute('''
                SELECT xml_id, zip_id, filename, internal_path, ein, tax_year,
                       form_type, processed, error_message, created_at, updated_at
                FROM XmlFiles WHERE xml_id = ?
            ''', (xml_id,))

            row = cursor.fetchone()
            return self._row_to_xmlfile(row)

    def get_xml_file(self, xml_id: int) -> Optional[XmlFile]:
        """Get XML file by ID."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute('''
                SELECT xml_id, zip_id, filename, internal_path, ein, tax_year,
                       form_type, processed, error_message, created_at
                FROM XmlFiles WHERE xml_id = ?
            ''', (xml_id,))

            row = cursor.fetchone()
            return self._row_to_xmlfile(row) if row else None

    def get_xml_files_by_zip(self, zip_id: int) -> List[XmlFile]:
        """Get all XML files in a ZIP file."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute('''
                SELECT xml_id, zip_id, filename, internal_path, ein, tax_year,
                       form_type, processed, error_message, created_at
                FROM XmlFiles WHERE zip_id = ? ORDER BY filename
            ''', (zip_id,))

            return [self._row_to_xmlfile(row) for row in cursor.fetchall()]

    def get_xml_files_by_ein(self, ein: str) -> List[XmlFile]:
        """Get all XML files for a specific EIN."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute('''
                SELECT xml_id, zip_id, filename, internal_path, ein, tax_year,
                       form_type, processed, error_message, created_at
                FROM XmlFiles WHERE ein = ? ORDER BY tax_year DESC, filename
            ''', (ein,))

            return [self._row_to_xmlfile(row) for row in cursor.fetchall()]

    def get_xml_files_by_year(self, tax_year: int) -> List[XmlFile]:
        """Get all XML files for a specific tax year."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute('''
                SELECT xml_id, zip_id, filename, internal_path, ein, tax_year,
                       form_type, processed, error_message, created_at
                FROM XmlFiles WHERE tax_year = ? ORDER BY filename
            ''', (tax_year,))

            return [self._row_to_xmlfile(row) for row in cursor.fetchall()]

    def get_xml_files_by_form_type(self, form_type: str) -> List[XmlFile]:
        """Get all XML files of a specific form type."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute('''
                SELECT xml_id, zip_id, filename, internal_path, ein, tax_year,
                       form_type, processed, error_message, created_at
                FROM XmlFiles WHERE form_type = ? ORDER BY tax_year, filename
            ''', (form_type,))

            return [self._row_to_xmlfile(row) for row in cursor.fetchall()]

    def update_xml_processed(self, xml_id: int, processed: bool,
                           error_message: Optional[str] = None) -> XmlFile:
        """Update XML file processing status."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute('''
                UPDATE XmlFiles
                SET processed = ?, error_message = ?, updated_at = ?
                WHERE xml_id = ?
            ''', (processed, error_message, datetime.now().isoformat(), xml_id))

            # Return updated record
            return self.get_xml_file(xml_id)

    def delete_xml_file(self, xml_id: int) -> bool:
        """Delete XML file record."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute('DELETE FROM XmlFiles WHERE xml_id = ?', (xml_id,))
            return cursor.rowcount > 0

    def get_all_xml_files(self) -> List[XmlFile]:
        """Get all XML files."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute('''
                SELECT xml_id, zip_id, filename, internal_path, ein, tax_year,
                       form_type, processed, error_message, created_at
                FROM XmlFiles ORDER BY tax_year, filename
            ''')

            return [self._row_to_xmlfile(row) for row in cursor.fetchall()]

    def get_unprocessed_xml_files(self) -> List[XmlFile]:
        """Get all unprocessed XML files."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute('''
                SELECT xml_id, zip_id, filename, internal_path, ein, tax_year,
                       form_type, processed, error_message, created_at
                FROM XmlFiles WHERE processed = FALSE ORDER BY tax_year, filename
            ''', ())

            return [self._row_to_xmlfile(row) for row in cursor.fetchall()]


class IndexingManager:
    """Manager for building and maintaining indexes from ZIP files."""

    def __init__(self, db_path: str = "/Volumes/Data/final/pipeline_progress.db"):
        self.zip_manager = ZipFileManager(db_path)
        self.xml_manager = XmlFileManager(db_path)
        self.db_path = db_path

    def build_index_from_zip(self, zip_path: str, extract_metadata: bool = True) -> Tuple[ZipFile, List[XmlFile]]:
        """Build index for a single ZIP file."""
        if not os.path.exists(zip_path):
            raise FileNotFoundError(f"ZIP file not found: {zip_path}")

        filename = os.path.basename(zip_path)
        file_size = os.path.getsize(zip_path)

        # Calculate checksum
        checksum = self._calculate_checksum(zip_path)

        # Extract tax year from filename
        tax_year = self._extract_tax_year(filename)
        if not tax_year:
            raise ValueError(f"Could not extract tax year from filename: {filename}")

        # Add ZIP file to database
        zip_file = self.zip_manager.add_zip_file(
            filename=filename,
            file_path=zip_path,
            tax_year=tax_year,
            file_size=file_size,
            checksum=checksum,
            download_date=datetime.now()
        )

        xml_files = []
        if extract_metadata:
            xml_files = self._extract_xml_metadata(zip_file)

        return zip_file, xml_files

    def build_index_from_directory(self, zips_dir: str, start_year: int = 2017,
                                 end_year: int = 2025, extract_metadata: bool = True) -> Dict[str, Any]:
        """Build index for all ZIP files in a directory."""
        if not os.path.exists(zips_dir):
            raise FileNotFoundError(f"Directory not found: {zips_dir}")

        zip_files = []
        xml_files = []
        errors = []

        # Find all ZIP files
        for filename in os.listdir(zips_dir):
            if not filename.endswith('.zip'):
                continue

            zip_path = os.path.join(zips_dir, filename)

            # Check if within year range
            tax_year = self._extract_tax_year(filename)
            if tax_year and (tax_year < start_year or tax_year > end_year):
                continue

            try:
                zip_file, xml_list = self.build_index_from_zip(zip_path, extract_metadata)
                zip_files.append(zip_file)
                xml_files.extend(xml_list)
            except Exception as e:
                errors.append(f"Error indexing {filename}: {str(e)}")

        return {
            'zip_files_indexed': len(zip_files),
            'xml_files_indexed': len(xml_files),
            'errors': errors,
            'zip_files': zip_files,
            'xml_files': xml_files
        }

    def _extract_xml_metadata(self, zip_file: ZipFile) -> List[XmlFile]:
        """Extract metadata from XML files in a ZIP."""
        xml_files = []

        try:
            with zipfile.ZipFile(zip_file.file_path, 'r') as zip_ref:
                xml_filenames = [f for f in zip_ref.namelist() if f.endswith('.xml')]

                for xml_filename in xml_filenames:
                    try:
                        # Extract metadata from XML content
                        with zip_ref.open(xml_filename) as xml_file_obj:
                            content = xml_file_obj.read().decode('utf-8', errors='ignore')

                            # Extract EIN
                            ein_match = re.search(r'<(?:irs:)?EIN>(\d{9})</(?:irs:)?EIN>', content)
                            ein = ein_match.group(1) if ein_match else None

                            # Extract tax year
                            tax_year_match = re.search(r'<TaxYr>(\d{4})</TaxYr>', content)
                            tax_year = int(tax_year_match.group(1)) if tax_year_match else None

                            # Extract form type
                            form_match = re.search(r'<FormType>([^<]+)</FormType>', content)
                            form_type = form_match.group(1) if form_match else None

                            # Add XML file to database
                            xml_file = self.xml_manager.add_xml_file(
                                zip_id=zip_file.zip_id,
                                filename=xml_filename,
                                internal_path=xml_filename,
                                ein=ein,
                                tax_year=tax_year,
                                form_type=form_type
                            )
                            xml_files.append(xml_file)

                    except Exception as e:
                        # Add XML file with error
                        xml_file = self.xml_manager.add_xml_file(
                            zip_id=zip_file.zip_id,
                            filename=xml_filename,
                            internal_path=xml_filename
                        )
                        self.xml_manager.update_xml_processed(xml_file.xml_id, False, str(e))
                        xml_files.append(xml_file)

        except Exception as e:
            # Mark ZIP file as error
            self.zip_manager.update_zip_status(zip_file.zip_id, 'error')

        return xml_files

    def _calculate_checksum(self, file_path: str) -> str:
        """Calculate SHA256 checksum of file."""
        hash_sha256 = hashlib.sha256()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                hash_sha256.update(chunk)
        return hash_sha256.hexdigest()

    def _extract_tax_year(self, filename: str) -> Optional[int]:
        """Extract tax year from ZIP filename."""
        match = re.search(r'(\d{4})', filename)
        return int(match.group(1)) if match else None

    def get_file_path(self, xml_filename: str) -> Optional[str]:
        """Get the full path to a ZIP file containing the specified XML file."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute('''
                SELECT z.file_path
                FROM ZipFiles z
                JOIN XmlFiles x ON z.zip_id = x.zip_id
                WHERE x.filename = ?
                LIMIT 1
            ''', (xml_filename,))

            row = cursor.fetchone()
            return row[0] if row else None

    def get_zip_path_for_ein(self, ein: str) -> List[str]:
        """Get all ZIP file paths containing XML files for a specific EIN."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute('''
                SELECT DISTINCT z.file_path
                FROM ZipFiles z
                JOIN XmlFiles x ON z.zip_id = x.zip_id
                WHERE x.ein = ?
                ORDER BY z.tax_year DESC
            ''', (ein,))

            return [row[0] for row in cursor.fetchall()]

    def check_file_status(self, xml_filename: str) -> Optional[str]:
        """Check processing status of an XML file."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute('''
                SELECT x.processed, x.error_message
                FROM XmlFiles x
                WHERE x.filename = ?
                LIMIT 1
            ''', (xml_filename,))

            row = cursor.fetchone()
            if row:
                return 'processed' if row[0] else ('error' if row[1] else 'unprocessed')
            return None

    def get_relationships(self, xml_filename: str) -> Optional[Dict[str, Any]]:
        """Get file relationships and metadata for an XML file."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute('''
                SELECT z.filename as zip_filename, z.file_path, z.tax_year as zip_year,
                       x.ein, x.tax_year as xml_year, x.form_type, x.processed, x.error_message
                FROM ZipFiles z
                JOIN XmlFiles x ON z.zip_id = x.zip_id
                WHERE x.filename = ?
                LIMIT 1
            ''', (xml_filename,))

            row = cursor.fetchone()
            if row:
                return {
                    'zip_filename': row[0],
                    'zip_path': row[1],
                    'zip_year': row[2],
                    'ein': row[3],
                    'xml_year': row[4],
                    'form_type': row[5],
                    'processed': bool(row[6]),
                    'error_message': row[7]
                }
            return None


# Convenience functions for backward compatibility
def build_zip_index(zips_dir: str, start_year: int = 2017, end_year: int = 2025) -> IndexingManager:
    """Build ZIP index using database manager."""
    manager = IndexingManager()
    result = manager.build_index_from_directory(zips_dir, start_year, end_year)
    return manager

def get_file_path(xml_filename: str, db_path: str = "/Volumes/Data/final/pipeline_progress.db") -> Optional[str]:
    """Get ZIP file path for an XML file."""
    manager = IndexingManager(db_path)
    return manager.get_file_path(xml_filename)

def check_file_status(xml_filename: str, db_path: str = "/Volumes/Data/final/pipeline_progress.db") -> Optional[str]:
    """Check processing status of an XML file."""
    manager = IndexingManager(db_path)
    return manager.check_file_status(xml_filename)