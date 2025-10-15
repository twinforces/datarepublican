"""
Unit tests for ZIP and XML database management classes.
"""

import unittest
import tempfile
import os
import shutil
import zipfile
import json
from datetime import datetime
from zip_xml_db import ZipFileManager, XmlFileManager, IndexingManager, DatabaseManager
from models import ZipFile, XmlFile


class TestDatabaseManager(unittest.TestCase):
    """Test base database manager functionality."""

    def setUp(self):
        self.db_fd, self.db_path = tempfile.mkstemp()
        os.close(self.db_fd)  # Close the file descriptor
        self.manager = DatabaseManager(self.db_path)

    def tearDown(self):
        os.unlink(self.db_path)

    def test_database_creation(self):
        """Test that database and tables are created properly."""
        # _ensure_db_exists is called in __init__, so tables should exist
        import sqlite3
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = [row[0] for row in cursor.fetchall()]
            self.assertIn('ZipFiles', tables)
            self.assertIn('XmlFiles', tables)


class TestZipFileManager(unittest.TestCase):
    """Test ZIP file manager functionality."""

    def setUp(self):
        self.db_fd, self.db_path = tempfile.mkstemp()
        os.close(self.db_fd)
        self.manager = ZipFileManager(self.db_path)

    def tearDown(self):
        os.unlink(self.db_path)

    def test_add_and_get_zip_file(self):
        """Test adding and retrieving ZIP file."""
        zip_file = self.manager.add_zip_file(
            filename="test_2023.zip",
            file_path="/path/to/test_2023.zip",
            tax_year=2023,
            file_size=1024,
            checksum="abc123"
        )

        self.assertIsInstance(zip_file, ZipFile)
        self.assertEqual(zip_file.filename, "test_2023.zip")
        self.assertEqual(zip_file.tax_year, 2023)

        # Retrieve by ID
        retrieved = self.manager.get_zip_file(zip_file.zip_id)
        self.assertEqual(retrieved.filename, "test_2023.zip")

        # Retrieve by filename
        retrieved_by_name = self.manager.get_zip_file_by_filename("test_2023.zip")
        self.assertEqual(retrieved_by_name.filename, "test_2023.zip")

    def test_get_zip_files_by_year(self):
        """Test retrieving ZIP files by tax year."""
        # Add multiple files
        self.manager.add_zip_file("test_2022.zip", "/path/2022.zip", 2022)
        self.manager.add_zip_file("test_2023_1.zip", "/path/2023_1.zip", 2023)
        self.manager.add_zip_file("test_2023_2.zip", "/path/2023_2.zip", 2023)

        files_2023 = self.manager.get_zip_files_by_year(2023)
        self.assertEqual(len(files_2023), 2)
        self.assertTrue(all(f.tax_year == 2023 for f in files_2023))

    def test_update_zip_status(self):
        """Test updating ZIP file status."""
        zip_file = self.manager.add_zip_file("test.zip", "/path/test.zip", 2023)

        updated = self.manager.update_zip_status(zip_file.zip_id, "processed")
        self.assertEqual(updated.status, "processed")
        self.assertIsNotNone(updated.processed_date)

        # Test setting back to downloaded
        updated2 = self.manager.update_zip_status(zip_file.zip_id, "downloaded")
        self.assertEqual(updated2.status, "downloaded")
        self.assertIsNone(updated2.processed_date)

    def test_delete_zip_file(self):
        """Test deleting ZIP file."""
        zip_file = self.manager.add_zip_file("test.zip", "/path/test.zip", 2023)

        result = self.manager.delete_zip_file(zip_file.zip_id)
        self.assertTrue(result)

        # Verify deletion
        retrieved = self.manager.get_zip_file(zip_file.zip_id)
        self.assertIsNone(retrieved)


class TestXmlFileManager(unittest.TestCase):
    """Test XML file manager functionality."""

    def setUp(self):
        self.db_fd, self.db_path = tempfile.mkstemp()
        os.close(self.db_fd)
        self.zip_manager = ZipFileManager(self.db_path)
        self.xml_manager = XmlFileManager(self.db_path)

        # Create a test ZIP file
        self.test_zip = self.zip_manager.add_zip_file(
            "test_2023.zip", "/path/test_2023.zip", 2023
        )

    def tearDown(self):
        os.unlink(self.db_path)

    def test_add_and_get_xml_file(self):
        """Test adding and retrieving XML file."""
        xml_file = self.xml_manager.add_xml_file(
            zip_id=self.test_zip.zip_id,
            filename="test_123456789_2023.xml",
            internal_path="test_123456789_2023.xml",
            ein="123456789",
            tax_year=2023,
            form_type="990"
        )

        self.assertIsInstance(xml_file, XmlFile)
        self.assertEqual(xml_file.filename, "test_123456789_2023.xml")
        self.assertEqual(xml_file.ein, "123456789")

        # Retrieve by ID
        retrieved = self.xml_manager.get_xml_file(xml_file.xml_id)
        self.assertEqual(retrieved.filename, "test_123456789_2023.xml")

    def test_get_xml_files_by_zip(self):
        """Test retrieving XML files by ZIP ID."""
        # Add multiple XML files
        self.xml_manager.add_xml_file(self.test_zip.zip_id, "file1.xml", "file1.xml", "111111111")
        self.xml_manager.add_xml_file(self.test_zip.zip_id, "file2.xml", "file2.xml", "222222222")

        xml_files = self.xml_manager.get_xml_files_by_zip(self.test_zip.zip_id)
        self.assertEqual(len(xml_files), 2)
        self.assertTrue(all(f.zip_id == self.test_zip.zip_id for f in xml_files))

    def test_get_xml_files_by_ein(self):
        """Test retrieving XML files by EIN."""
        ein = "123456789"
        self.xml_manager.add_xml_file(self.test_zip.zip_id, "file1.xml", "file1.xml", ein, 2022)
        self.xml_manager.add_xml_file(self.test_zip.zip_id, "file2.xml", "file2.xml", ein, 2023)

        xml_files = self.xml_manager.get_xml_files_by_ein(ein)
        self.assertEqual(len(xml_files), 2)
        self.assertTrue(all(f.ein == ein for f in xml_files))
        # Should be ordered by tax_year DESC
        self.assertEqual(xml_files[0].tax_year, 2023)
        self.assertEqual(xml_files[1].tax_year, 2022)

    def test_update_xml_processed(self):
        """Test updating XML file processing status."""
        xml_file = self.xml_manager.add_xml_file(
            self.test_zip.zip_id, "test.xml", "test.xml"
        )

        # First update to processed with error
        updated = self.xml_manager.update_xml_processed(xml_file.xml_id, True, "Test error")
        self.assertTrue(updated.processed)
        self.assertEqual(updated.error_message, "Test error")

        # Test setting to False
        updated2 = self.xml_manager.update_xml_processed(xml_file.xml_id, False)
        self.assertFalse(updated2.processed)
        self.assertIsNone(updated2.error_message)


class TestIndexingManager(unittest.TestCase):
    """Test indexing manager functionality."""

    def setUp(self):
        self.db_fd, self.db_path = tempfile.mkstemp()
        os.close(self.db_fd)

        # Create temporary directory with test ZIP files
        self.temp_dir = tempfile.mkdtemp()
        self.create_test_zip_files()

    def tearDown(self):
        os.unlink(self.db_path)
        shutil.rmtree(self.temp_dir)

    def create_test_zip_files(self):
        """Create test ZIP files with XML content."""
        # Create test XML content
        xml_content = """<?xml version="1.0" encoding="UTF-8"?>
        <Return xmlns="urn:us:irs:990" xmlns:irs="urn:us:irs:990">
            <ReturnHeader>
                <TaxYr>2023</TaxYr>
                <FormType>990</FormType>
            </ReturnHeader>
            <Filer>
                <EIN>123456789</EIN>
                <Name>TEST CHARITY</Name>
            </Filer>
        </Return>"""

        # Create ZIP file for 2023
        zip_path_2023 = os.path.join(self.temp_dir, "test_2023.zip")
        with zipfile.ZipFile(zip_path_2023, 'w') as zip_file:
            zip_file.writestr("test_123456789_2023.xml", xml_content)

        # Create ZIP file for 2022
        zip_path_2022 = os.path.join(self.temp_dir, "test_2022.zip")
        with zipfile.ZipFile(zip_path_2022, 'w') as zip_file:
            zip_file.writestr("test_987654321_2022.xml", xml_content.replace("123456789", "987654321").replace("2023", "2022"))

    def test_build_index_from_zip(self):
        """Test building index from a single ZIP file."""
        manager = IndexingManager(self.db_path)
        zip_path = os.path.join(self.temp_dir, "test_2023.zip")

        zip_file, xml_files = manager.build_index_from_zip(zip_path)

        self.assertEqual(zip_file.filename, "test_2023.zip")
        self.assertEqual(zip_file.tax_year, 2023)
        self.assertEqual(len(xml_files), 1)
        self.assertEqual(xml_files[0].filename, "test_123456789_2023.xml")
        self.assertEqual(xml_files[0].ein, "123456789")

    def test_build_index_from_directory(self):
        """Test building index from directory."""
        manager = IndexingManager(self.db_path)

        result = manager.build_index_from_directory(self.temp_dir, 2022, 2023)

        self.assertEqual(result['zip_files_indexed'], 2)
        self.assertEqual(result['xml_files_indexed'], 2)
        self.assertEqual(len(result['errors']), 0)

    def test_get_file_path(self):
        """Test getting file path for XML file."""
        manager = IndexingManager(self.db_path)
        manager.build_index_from_directory(self.temp_dir, 2022, 2023)

        path = manager.get_file_path("test_123456789_2023.xml")
        expected_path = os.path.join(self.temp_dir, "test_2023.zip")
        self.assertEqual(path, expected_path)

    def test_get_zip_path_for_ein(self):
        """Test getting ZIP paths for EIN."""
        manager = IndexingManager(self.db_path)
        manager.build_index_from_directory(self.temp_dir, 2022, 2023)

        paths = manager.get_zip_path_for_ein("123456789")
        self.assertEqual(len(paths), 1)
        self.assertIn("test_2023.zip", paths[0])

    def test_check_file_status(self):
        """Test checking file processing status."""
        manager = IndexingManager(self.db_path)
        manager.build_index_from_directory(self.temp_dir, 2022, 2023)

        status = manager.check_file_status("test_123456789_2023.xml")
        self.assertEqual(status, "unprocessed")

        # Update status and check again
        xml_files = manager.xml_manager.get_xml_files_by_ein("123456789")
        manager.xml_manager.update_xml_processed(xml_files[0].xml_id, True)

        status = manager.check_file_status("test_123456789_2023.xml")
        self.assertEqual(status, "processed")

    def test_get_relationships(self):
        """Test getting file relationships."""
        manager = IndexingManager(self.db_path)
        manager.build_index_from_directory(self.temp_dir, 2022, 2023)

        relationships = manager.get_relationships("test_123456789_2023.xml")

        self.assertIsNotNone(relationships)
        self.assertEqual(relationships['ein'], "123456789")
        self.assertEqual(relationships['zip_year'], 2023)
        self.assertEqual(relationships['xml_year'], 2023)
        self.assertEqual(relationships['processed'], False)


class TestMigrationUtilities(unittest.TestCase):
    """Test migration utilities."""

    def setUp(self):
        self.db_fd, self.db_path = tempfile.mkstemp()
        os.close(self.db_fd)
        self.temp_dir = tempfile.mkdtemp()

        # Create mock JSON indexes
        self.xml_index = {
            "file1.xml": os.path.join(self.temp_dir, "test_2023.zip"),
            "file2.xml": os.path.join(self.temp_dir, "test_2023.zip")
        }
        self.ein_index = {
            "123456789": [
                {"xml_file": "file1.xml", "zip_path": os.path.join(self.temp_dir, "test_2023.zip")}
            ]
        }

        # Write JSON files
        with open(os.path.join(self.temp_dir, "xml_zip_index.json"), 'w') as f:
            json.dump(self.xml_index, f)
        with open(os.path.join(self.temp_dir, "ein_xml_index.json"), 'w') as f:
            json.dump(self.ein_index, f)

    def tearDown(self):
        os.unlink(self.db_path)
        shutil.rmtree(self.temp_dir)

    def test_migrate_json_indexes(self):
        """Test migrating JSON indexes to database."""
        from migrate_indexes import migrate_json_indexes

        # Create a fake ZIP file that exists
        fake_zip_path = os.path.join(self.temp_dir, "test_2023.zip")
        with open(fake_zip_path, 'w') as f:
            f.write("fake zip content")

        # Update the XML index to point to the real file
        self.xml_index["file1.xml"] = fake_zip_path
        self.xml_index["file2.xml"] = fake_zip_path

        # Rewrite the JSON file
        with open(os.path.join(self.temp_dir, "xml_zip_index.json"), 'w') as f:
            json.dump(self.xml_index, f)

        results = migrate_json_indexes(
            self.temp_dir, self.db_path,
            os.path.join(self.temp_dir, "xml_zip_index.json"),
            os.path.join(self.temp_dir, "ein_xml_index.json")
        )

        self.assertTrue(results['xml_index_migrated'])
        self.assertTrue(results['ein_index_migrated'])
        self.assertEqual(results['zip_files_created'], 1)  # One unique ZIP file
        self.assertEqual(results['xml_files_created'], 2)  # Two XML files
        self.assertEqual(len(results['errors']), 0)  # No errors expected


if __name__ == '__main__':
    unittest.main()