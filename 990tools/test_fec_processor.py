#!/usr/bin/env python3
"""FEC bulk zip naming and data-dir env (no network)."""

import os
import unittest
from pathlib import Path

import duckdb

from fec_processor import (
    FECProcessor,
    cycle_zip_names,
    default_data_dir,
    extract_is_stale,
    ingest_is_stale,
    ingest_marker_path,
    parse_cycles,
    should_reload_cycle,
    write_extract_marker,
    write_ingest_marker,
    zip_url,
)


class TestFecNaming(unittest.TestCase):
    def test_cycle_zip_names_2024(self):
        self.assertEqual(
            cycle_zip_names(2024),
            ["cm24.zip", "indiv24.zip", "oth24.zip", "pas224.zip", "oppexp24.zip"],
        )

    def test_cycle_zip_names_2026(self):
        self.assertEqual(
            cycle_zip_names(2026),
            ["cm26.zip", "indiv26.zip", "oth26.zip", "pas226.zip", "oppexp26.zip"],
        )

    def test_zip_url(self):
        self.assertEqual(
            zip_url(2024, "indiv24.zip"),
            "https://www.fec.gov/files/bulk-downloads/2024/indiv24.zip",
        )

    def test_parse_cycles(self):
        self.assertEqual(parse_cycles("2024,2026"), [2024, 2026])
        self.assertIsNone(parse_cycles(""))
        self.assertIsNone(parse_cycles("nope"))

    def test_reload_when_extract_stale_or_downloaded(self):
        self.assertTrue(should_reload_cycle(False, True))
        self.assertTrue(should_reload_cycle(True, False))
        self.assertFalse(should_reload_cycle(False, False))

    def test_extract_marker_tracks_zip_identity(self):
        import tempfile

        with tempfile.TemporaryDirectory() as raw:
            d = Path(raw)
            z = d / "cm24.zip"
            t = d / "cm.txt"
            z.write_bytes(b"zip")
            t.write_bytes(b"txt")
            self.assertTrue(extract_is_stale(z, t))
            write_extract_marker(z, t)
            self.assertFalse(extract_is_stale(z, t))
            z.write_bytes(b"zip2")
            self.assertTrue(extract_is_stale(z, t))

    def test_ingest_marker_stale_until_written(self):
        import tempfile

        with tempfile.TemporaryDirectory() as raw:
            d = Path(raw)
            z = d / "indiv24.zip"
            z.write_bytes(b"zip")
            marker = ingest_marker_path(d, "fec_raw_individuals", 2024)
            self.assertTrue(ingest_is_stale(z, marker))
            write_ingest_marker(z, marker)
            self.assertFalse(ingest_is_stale(z, marker))

    def test_batched_delete_clears_cycle(self):
        con = duckdb.connect(":memory:")
        con.execute("CREATE TABLE owners (id INTEGER, report_year INTEGER)")
        con.execute(
            "CREATE TABLE Addresses (address_id INTEGER, address_type VARCHAR, owner_id INTEGER)"
        )
        con.execute("INSERT INTO owners VALUES (1, 2024), (2, 2024), (3, 2022)")
        con.execute(
            "INSERT INTO Addresses VALUES (10, 'fec_individual_contribution', 1), "
            "(11, 'fec_individual_contribution', 2), (12, 'other', 1)"
        )
        proc = FECProcessor.__new__(FECProcessor)
        proc._batched_delete(
            con,
            table="Addresses",
            pk="address_id",
            id_select=(
                "SELECT a.address_id FROM Addresses a "
                "INNER JOIN owners o ON a.owner_id = o.id "
                "WHERE a.address_type = ? AND o.report_year = ?"
            ),
            params=("fec_individual_contribution", 2024),
            label="addr",
            batch=1,
        )
        proc._batched_delete(
            con,
            table="owners",
            pk="id",
            id_select="SELECT id FROM owners WHERE report_year = ?",
            params=(2024,),
            label="owners",
            batch=1,
        )
        years = [r[0] for r in con.execute("SELECT report_year FROM owners").fetchall()]
        self.assertEqual(years, [2022])
        left = con.execute("SELECT address_id FROM Addresses ORDER BY address_id").fetchall()
        self.assertEqual(left, [(12,)])

    def test_copy_empty_contributor_name(self):
        from database_operations import insert_tsv_via_copy, write_bulk_tsv

        con = duckdb.connect(":memory:")
        con.execute(
            """
            CREATE TABLE fec_individual_contributions (
                id VARCHAR,
                fec_sub_id VARCHAR NOT NULL,
                fec_cmte_id VARCHAR NOT NULL,
                contributor_name VARCHAR NOT NULL,
                contribution_amount DOUBLE NOT NULL,
                report_year INTEGER NOT NULL
            )
            """
        )
        cols = [
            "id",
            "fec_sub_id",
            "fec_cmte_id",
            "contributor_name",
            "contribution_amount",
            "report_year",
        ]
        types = ["VARCHAR", "VARCHAR", "VARCHAR", "VARCHAR", "DOUBLE", "INTEGER"]
        path = write_bulk_tsv(cols, [("u", "s1", "C001", "", 0.0, 2024)])
        insert_tsv_via_copy(con, "fec_individual_contributions", cols, types, path)
        name = con.execute(
            "SELECT contributor_name FROM fec_individual_contributions"
        ).fetchone()[0]
        self.assertEqual(name, "")

    def test_data_dir_env(self):
        prev = os.environ.get("FEC_DATA_DIR")
        os.environ["FEC_DATA_DIR"] = "/tmp/fec-test-dir"
        try:
            self.assertEqual(default_data_dir("/Volumes/Data/final"), Path("/tmp/fec-test-dir"))
        finally:
            if prev is None:
                del os.environ["FEC_DATA_DIR"]
            else:
                os.environ["FEC_DATA_DIR"] = prev


if __name__ == "__main__":
    unittest.main()
