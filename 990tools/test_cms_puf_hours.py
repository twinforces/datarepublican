#!/usr/bin/env python3
import unittest
import uuid

import duckdb

from cms_puf_hours_processor import PUF_YEAR, build_hours


class TestPufHours(unittest.TestCase):
    def test_99213_is_half_hour_and_npi_rolls_up(self):
        con = duckdb.connect(":memory:")
        con.execute(
            """
            CREATE TABLE cms_puf_service (
                Rndrng_NPI BIGINT, HCPCS_Cd VARCHAR, Place_Of_Srvc VARCHAR,
                HCPCS_Drug_Ind VARCHAR, Tot_Benes INTEGER, Tot_Srvcs DOUBLE
            )
            """
        )
        con.execute(
            """
            INSERT INTO cms_puf_service VALUES
            (123, '99213', 'O', 'N', 20, 40),
            (123, '80053', 'O', 'N', 15, 15)
            """
        )
        con.execute(
            """
            CREATE TABLE cms_pfs_work_time (
                cpt_code VARCHAR, adj_total_min DOUBLE, adj_intra_min DOUBLE
            )
            """
        )
        con.execute("INSERT INTO cms_pfs_work_time VALUES ('99213', 30, 20)")
        con.execute(
            """
            CREATE TABLE cms_pfs_rvu (
                hcpcs VARCHAR, mod VARCHAR, work_rvu DOUBLE
            )
            """
        )
        con.execute("INSERT INTO cms_pfs_rvu VALUES ('99213', NULL, 1.3)")
        con.execute(
            """
            CREATE TABLE cms_puf_provider (
                Rndrng_NPI BIGINT, Rndrng_Prvdr_Last_Org_Name VARCHAR,
                Rndrng_Prvdr_First_Name VARCHAR, Rndrng_Prvdr_Type VARCHAR,
                Rndrng_Prvdr_Ent_Cd VARCHAR, Rndrng_Prvdr_St1 VARCHAR,
                Rndrng_Prvdr_City VARCHAR, Rndrng_Prvdr_State_Abrvtn VARCHAR,
                Rndrng_Prvdr_Zip5 VARCHAR, Bene_Avg_Age DOUBLE,
                Tot_Benes INTEGER, Tot_Srvcs DOUBLE, Tot_HCPCS_Cds INTEGER
            )
            """
        )
        con.execute(
            """
            INSERT INTO cms_puf_provider VALUES
            (123, 'SMITH', 'JANE', 'Internal Medicine', 'I',
             '1 MAIN ST', 'KNOXVILLE', 'TN', '37919', 72, 40, 80, 3)
            """
        )
        mid = uuid.uuid4()
        con.execute(
            "CREATE TABLE medicare_providers (id UUID, npi VARCHAR)"
        )
        con.execute("INSERT INTO medicare_providers VALUES (?, '123')", [mid])
        con.execute(
            "CREATE TABLE Addresses (owner_id UUID, address_type VARCHAR, colocator VARCHAR)"
        )
        con.execute(
            "INSERT INTO Addresses VALUES (?, 'nppes_practice', 'LL:35.9606:-83.9207')",
            [mid],
        )
        con.execute("CREATE TABLE cms_leie (NPI BIGINT)")
        stats = build_hours(con)
        self.assertEqual(stats["cms_puf_service_hours"], 2)
        row = con.execute(
            "SELECT hcpcs, hours, work_rvu_total FROM cms_puf_service_hours WHERE hcpcs='99213'"
        ).fetchone()
        self.assertAlmostEqual(row[1], 20.0)  # 40 × 30 min / 60
        self.assertAlmostEqual(row[2], 52.0)  # 40 × 1.3
        lab = con.execute(
            "SELECT hours FROM cms_puf_service_hours WHERE hcpcs='80053'"
        ).fetchone()
        self.assertIsNone(lab[0])
        npi = con.execute(
            """
            SELECT hours_timed, srvcs_untimed, suppressed_srvcs, colocator, leie, puf_year
            FROM cms_puf_npi_hours
            """
        ).fetchone()
        self.assertAlmostEqual(npi[0], 20.0)
        self.assertAlmostEqual(npi[1], 15.0)
        self.assertAlmostEqual(npi[2], 25.0)  # provider 80 − published 55
        self.assertEqual(npi[3], "LL:35.9606:-83.9207")
        self.assertFalse(npi[4])
        self.assertEqual(npi[5], PUF_YEAR)

    def test_attach_building_sqft(self):
        import tempfile
        from pathlib import Path

        from cms_puf_hours_processor import attach_building_sqft

        con = duckdb.connect(":memory:")
        con.execute(
            """
            CREATE TABLE cms_puf_npi_hours (
                npi BIGINT, colocator VARCHAR, hours_timed DOUBLE
            )
            """
        )
        con.execute(
            "INSERT INTO cms_puf_npi_hours VALUES "
            "(1, 'LL:36.2128:-83.3029', 10), "
            "(2, 'LL:36.2128:-83.3032', 10), "
            "(3, 'LL:0:0', 1)"
        )
        with tempfile.TemporaryDirectory() as td:
            bdb = str(Path(td) / "b.duckdb")
            b = duckdb.connect(bdb)
            b.execute(
                """
                CREATE TABLE building_colocator (
                    colocator VARCHAR, n_buildings INTEGER,
                    min_area_sqft DOUBLE, max_area_sqft DOUBLE, sum_area_sqft DOUBLE
                )
                """
            )
            b.execute(
                "INSERT INTO building_colocator VALUES ('LL:36.2128:-83.3029', 1, 6500, 6500, 6500)"
            )
            b.close()
            stats = attach_building_sqft(con, bdb)
        self.assertEqual(stats["cms_puf_npi_hours_sqft"], 1)
        self.assertEqual(stats["cms_puf_npi_hours_sqft_near"], 2)
        got = {
            r[0]: r
            for r in con.execute(
                "SELECT npi, n_buildings, max_area_sqft, n_buildings_near, max_area_near "
                "FROM cms_puf_npi_hours"
            ).fetchall()
        }
        self.assertEqual(got[1][1], 1)
        self.assertAlmostEqual(got[1][2], 6500)
        self.assertIsNone(got[2][1])
        self.assertEqual(got[2][3], 1)
        self.assertAlmostEqual(got[2][4], 6500)
        self.assertIsNone(got[3][1])
        self.assertIsNone(got[3][3])


if __name__ == "__main__":
    unittest.main()
