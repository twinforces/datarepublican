#!/usr/bin/env python3
"""
Unit tests for the database-backed geocoding system.
Tests AddressManager, GeocodingManager, and integration functions.
"""

import os
import sys
import tempfile
import unittest
from datetime import datetime
from unittest.mock import patch, MagicMock

# Add the 990tools directory to the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__)))

from geocoding_db import AddressManager, GeocodingManager, DatabaseManager
from geocoding_db import batch_geocode_with_cache, get_geocoded_address
from models import Address, Geocoding


class TestDatabaseManager(unittest.TestCase):
    """Test the base DatabaseManager class."""

    def setUp(self):
        """Set up test database."""
        self.test_db = tempfile.NamedTemporaryFile(delete=False, suffix='.db')
        self.test_db.close()
        self.db_path = self.test_db.name

    def tearDown(self):
        """Clean up test database."""
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def test_database_creation(self):
        """Test that database and tables are created properly."""
        db_manager = DatabaseManager(self.db_path)

        # Check that tables exist
        with db_manager._get_connection() as conn:
            cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = [row[0] for row in cursor.fetchall()]

            self.assertIn('Addresses', tables)
            self.assertIn('Geocoding', tables)

            # Check indexes
            cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='index'")
            indexes = [row[0] for row in cursor.fetchall()]

            expected_indexes = [
                'idx_addresses_ein', 'idx_addresses_zip_code', 'idx_addresses_type',
                'idx_addresses_geocoding', 'idx_geocoding_address_hash', 'idx_geocoding_status'
            ]

            for idx in expected_indexes:
                self.assertIn(idx, indexes)


class TestAddressManager(unittest.TestCase):
    """Test the AddressManager class."""

    def setUp(self):
        """Set up test database and manager."""
        self.test_db = tempfile.NamedTemporaryFile(delete=False, suffix='.db')
        self.test_db.close()
        self.db_path = self.test_db.name
        self.address_manager = AddressManager(self.db_path)

    def tearDown(self):
        """Clean up test database."""
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def test_insert_and_get_address(self):
        """Test inserting and retrieving an address."""
        address = Address(
            ein="123456789",
            name="Test Organization",
            canonical_address="123 Main St, Anytown, CA 12345",
            zip_code="12345",
            address_type="filer"
        )

        # Insert address
        address_id = self.address_manager.insert_address(address)
        self.assertIsInstance(address_id, int)
        self.assertGreater(address_id, 0)

        # Retrieve address
        retrieved = self.address_manager.get_address_by_id(address_id)
        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved.ein, "123456789")
        self.assertEqual(retrieved.name, "Test Organization")
        self.assertEqual(retrieved.canonical_address, "123 Main St, Anytown, CA 12345")

    def test_get_addresses_by_ein(self):
        """Test getting all addresses for an EIN."""
        ein = "987654321"

        # Insert multiple addresses for same EIN
        addresses = [
            Address(ein=ein, name="Org 1", canonical_address="Addr 1", address_type="filer"),
            Address(ein=ein, name="Org 1", canonical_address="Addr 2", address_type="grantee"),
        ]

        for addr in addresses:
            self.address_manager.insert_address(addr)

        # Retrieve addresses
        retrieved = self.address_manager.get_addresses_by_ein(ein)
        self.assertEqual(len(retrieved), 2)

        address_types = {addr.address_type for addr in retrieved}
        self.assertEqual(address_types, {"filer", "grantee"})

    def test_update_address(self):
        """Test updating an address."""
        address = Address(
            ein="111111111",
            name="Original Name",
            canonical_address="Original Address",
            address_type="filer"
        )

        address_id = self.address_manager.insert_address(address)
        address.address_id = address_id
        address.name = "Updated Name"

        # Update address
        success = self.address_manager.update_address(address)
        self.assertTrue(success)

        # Verify update
        retrieved = self.address_manager.get_address_by_id(address_id)
        self.assertEqual(retrieved.name, "Updated Name")

    def test_delete_address(self):
        """Test deleting an address."""
        address = Address(ein="222222222", name="To Delete", canonical_address="Delete Me", address_type="filer")
        address_id = self.address_manager.insert_address(address)

        # Delete address
        success = self.address_manager.delete_address(address_id)
        self.assertTrue(success)

        # Verify deletion
        retrieved = self.address_manager.get_address_by_id(address_id)
        self.assertIsNone(retrieved)


class TestGeocodingManager(unittest.TestCase):
    """Test the GeocodingManager class."""

    def setUp(self):
        """Set up test database and manager."""
        self.test_db = tempfile.NamedTemporaryFile(delete=False, suffix='.db')
        self.test_db.close()
        self.db_path = self.test_db.name
        self.geocoding_manager = GeocodingManager(self.db_path)

    def tearDown(self):
        """Clean up test database."""
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def test_insert_and_get_geocoding(self):
        """Test inserting and retrieving a geocoding result."""
        geocoding = Geocoding(
            address_hash="testhash123",
            normalized_address="123 Test St, Test City, CA 12345",
            latitude=37.7749,
            longitude=-122.4194,
            geocoding_status="success",
            attempt_count=1
        )

        # Insert geocoding
        geocoding_id = self.geocoding_manager.insert_geocoding(geocoding)
        self.assertIsInstance(geocoding_id, int)
        self.assertGreater(geocoding_id, 0)

        # Retrieve by ID
        retrieved = self.geocoding_manager.get_geocoding_by_id(geocoding_id)
        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved.latitude, 37.7749)
        self.assertEqual(retrieved.longitude, -122.4194)
        self.assertEqual(retrieved.geocoding_status, "success")

        # Retrieve by hash
        retrieved_by_hash = self.geocoding_manager.get_geocoding_by_hash("testhash123")
        self.assertIsNotNone(retrieved_by_hash)
        self.assertEqual(retrieved_by_hash.geocoding_id, geocoding_id)

    def test_create_geocoding_from_address(self):
        """Test creating a geocoding record from an address."""
        normalized_address = "456 Sample Ave, Sample City, NY 10001"
        geocoding = self.geocoding_manager.create_geocoding_from_address(normalized_address)

        self.assertIsNotNone(geocoding.geocoding_id)
        self.assertEqual(geocoding.normalized_address, normalized_address)
        self.assertEqual(geocoding.geocoding_status, "pending")
        self.assertEqual(geocoding.attempt_count, 0)

    def test_mark_geocoding_success(self):
        """Test marking a geocoding as successful."""
        # Create a pending geocoding
        geocoding = self.geocoding_manager.create_geocoding_from_address("789 Success St")

        # Mark as successful
        success = self.geocoding_manager.mark_geocoding_success(geocoding.geocoding_id, 40.7128, -74.0060)
        self.assertTrue(success)

        # Verify update
        retrieved = self.geocoding_manager.get_geocoding_by_id(geocoding.geocoding_id)
        self.assertEqual(retrieved.geocoding_status, "success")
        self.assertEqual(retrieved.latitude, 40.7128)
        self.assertEqual(retrieved.longitude, -74.0060)
        self.assertEqual(retrieved.attempt_count, 1)

    def test_mark_geocoding_failed(self):
        """Test marking a geocoding as failed."""
        # Create a pending geocoding
        geocoding = self.geocoding_manager.create_geocoding_from_address("999 Failure Ave")

        # Mark as failed
        success = self.geocoding_manager.mark_geocoding_failed(geocoding.geocoding_id)
        self.assertTrue(success)

        # Verify update
        retrieved = self.geocoding_manager.get_geocoding_by_id(geocoding.geocoding_id)
        self.assertEqual(retrieved.geocoding_status, "failed")
        self.assertEqual(retrieved.attempt_count, 1)
        self.assertIsNone(retrieved.latitude)
        self.assertIsNone(retrieved.longitude)

    def test_get_pending_geocodings(self):
        """Test getting pending geocoding records."""
        # Create multiple geocoding records
        addresses = ["Addr 1", "Addr 2", "Addr 3"]
        for addr in addresses:
            self.geocoding_manager.create_geocoding_from_address(addr)

        # Mark one as successful
        geocoding = self.geocoding_manager.create_geocoding_from_address("Success Addr")
        self.geocoding_manager.mark_geocoding_success(geocoding.geocoding_id, 0, 0)

        # Get pending geocodings
        pending = self.geocoding_manager.get_pending_geocodings(limit=10)
        self.assertEqual(len(pending), 3)  # Should not include the successful one

    def test_get_successful_geocodings(self):
        """Test getting successful geocoding records."""
        # Create and mark geocodings
        geocoding1 = self.geocoding_manager.create_geocoding_from_address("Success 1")
        geocoding2 = self.geocoding_manager.create_geocoding_from_address("Success 2")
        geocoding3 = self.geocoding_manager.create_geocoding_from_address("Pending")

        self.geocoding_manager.mark_geocoding_success(geocoding1.geocoding_id, 1, 1)
        self.geocoding_manager.mark_geocoding_success(geocoding2.geocoding_id, 2, 2)

        successful = self.geocoding_manager.get_successful_geocodings()
        self.assertEqual(len(successful), 2)

        latitudes = {g.latitude for g in successful}
        self.assertEqual(latitudes, {1.0, 2.0})


class TestIntegrationFunctions(unittest.TestCase):
    """Test integration functions."""

    def setUp(self):
        """Set up test database."""
        self.test_db = tempfile.NamedTemporaryFile(delete=False, suffix='.db')
        self.test_db.close()
        self.db_path = self.test_db.name

    def tearDown(self):
        """Clean up test database."""
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    @patch('geocoding.geocode_batch')
    def test_batch_geocode_with_cache(self, mock_geocode_batch):
        """Test batch geocoding with database caching."""
        from geocoding_db import batch_geocode_with_cache

        # Mock the geocoding response
        mock_geocode_batch.return_value = [
            {'address': '123 Main St', 'lat': 37.7749, 'lon': -122.4194},
            {'address': '456 Oak Ave', 'lat': None, 'lon': None}
        ]

        geocoding_manager = GeocodingManager(self.db_path)
        addresses = ['123 Main St', '456 Oak Ave']

        results = batch_geocode_with_cache(addresses, geocoding_manager)

        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]['lat'], 37.7749)
        self.assertEqual(results[0]['lon'], -122.4194)
        self.assertIsNone(results[1]['lat'])
        self.assertIsNone(results[1]['lon'])

        # Verify database was updated
        geocodings = geocoding_manager.get_successful_geocodings()
        self.assertEqual(len(geocodings), 1)

    def test_get_geocoded_address(self):
        """Test getting geocoded address information."""
        from geocoding_db import get_geocoded_address

        # Set up test data using the same database path as the function will use
        address_manager = AddressManager()  # Uses default db path
        geocoding_manager = GeocodingManager()  # Uses default db path

        # Create address and geocoding
        geocoding = geocoding_manager.create_geocoding_from_address("Test Address")
        geocoding_manager.mark_geocoding_success(geocoding.geocoding_id, 35.0, -80.0)

        address = Address(
            ein="123456789",
            name="Test Org",
            canonical_address="Test Address",
            address_type="filer",
            geocoding_id=geocoding.geocoding_id
        )
        address_id = address_manager.insert_address(address)
        address.address_id = address_id

        # Test retrieval - use the correct function that creates its own managers
        result = get_geocoded_address("123456789")
        self.assertIsNotNone(result)
        self.assertEqual(result['address'].ein, "123456789")
        self.assertEqual(result['geocoding'].latitude, 35.0)
        self.assertEqual(result['geocoding'].longitude, -80.0)


if __name__ == '__main__':
    unittest.main()