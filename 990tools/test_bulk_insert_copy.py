#!/usr/bin/env python3
"""TSV COPY insert used by DatabaseOperations.bulk_insert."""

import os
import tempfile
import unittest
import uuid
from datetime import datetime

import duckdb

from database_operations import (
    insert_tsv_via_copy,
    is_duckdb_oom,
    tsv_cell,
    write_bulk_tsv,
)
from models.base import BaseModel


class UuidPrepTests(unittest.TestCase):
    def test_set_id_if_needed_uuid7(self):
        class Row(BaseModel):
            def __init__(self):
                self.address_id = None

            @classmethod
            def get_db_field_names(cls):
                return ["address_id", "name"]

        row = Row()
        row.set_id_if_needed()
        self.assertIsNotNone(row.address_id)
        self.assertEqual(len(str(row.address_id).replace("-", "")), 32)


class CopyInsertTests(unittest.TestCase):
    def test_heap_table_inserts_without_pk(self):
        """Production Addresses is a heap. INSERT OR IGNORE would fail here."""
        conn = duckdb.connect(":memory:")
        conn.execute(
            "CREATE TABLE Addresses_test (address_id UUID, name VARCHAR)"
        )
        gid = uuid.uuid4()
        cols = ["address_id", "name"]
        types = ["UUID", "VARCHAR"]
        path = write_bulk_tsv(cols, [(gid, "Acme")])
        try:
            insert_tsv_via_copy(conn, "Addresses_test", cols, types, path)
            n = conn.execute("SELECT COUNT(*) FROM Addresses_test").fetchone()[0]
            self.assertEqual(n, 1)
            got = conn.execute("SELECT address_id FROM Addresses_test").fetchone()[0]
            self.assertEqual(str(got), str(gid))
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass

    def test_none_cell(self):
        self.assertEqual(tsv_cell(None), "")
        self.assertEqual(tsv_cell(datetime(2024, 1, 2, 3, 4, 5))[:10], "2024-01-02")

    def test_oom_detector(self):
        self.assertTrue(is_duckdb_oom(RuntimeError("failed to pin block")))
        self.assertFalse(is_duckdb_oom(RuntimeError("duplicate key")))


if __name__ == "__main__":
    unittest.main()
