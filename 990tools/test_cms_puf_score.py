#!/usr/bin/env python3
import unittest

import duckdb

from cms_puf_score_processor import build_score


def _schema(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(
        """
        CREATE TABLE cms_puf_service_hours (
            npi BIGINT, hcpcs VARCHAR, place VARCHAR,
            hours DOUBLE, work_rvu_total DOUBLE
        )
        """
    )
    con.execute(
        """
        CREATE TABLE cms_puf_npi_hours (
            npi BIGINT, name VARCHAR, first_name VARCHAR, provider_type VARCHAR,
            entity VARCHAR, city VARCHAR, state VARCHAR, zip VARCHAR, street VARCHAR,
            colocator VARCHAR, bene_avg_age DOUBLE, leie BOOLEAN,
            max_area_near DOUBLE, n_buildings_near INTEGER, n_cells_near INTEGER,
            hours_timed DOUBLE
        )
        """
    )


class TestPufScore(unittest.TestCase):
    def test_facility_rounder_not_solo_watch(self):
        con = duckdb.connect(":memory:")
        _schema(con)
        # SNF rounder: huge facility hours, tiny office, small house pin.
        con.execute(
            "INSERT INTO cms_puf_npi_hours VALUES "
            "(1,'Paley','H','Otolaryngology','I','Encino','CA','91436','1 Main',"
            "'LL:34.1:-118.4',76,FALSE,3000,1,1,80000)"
        )
        con.execute(
            "INSERT INTO cms_puf_service_hours VALUES "
            "(1,'99309','F',80000,100),(1,'99213','O',200,10)"
        )
        # Peer so p90 is small.
        con.execute(
            "INSERT INTO cms_puf_npi_hours VALUES "
            "(9,'Peer','A','Otolaryngology','I','LA','CA','90001','2 Main',"
            "'LL:34.2:-118.3',70,FALSE,5000,1,1,400)"
        )
        con.execute("INSERT INTO cms_puf_service_hours VALUES (9,'99213','O',400,20)")
        build_score(con)
        row = con.execute(
            "SELECT office_heavy, watch_solo, watch_mill, watch FROM cms_puf_npi_score WHERE npi=1"
        ).fetchone()
        self.assertFalse(row[0])
        self.assertFalse(row[1])
        self.assertFalse(row[3])

    def test_mill_stack_in_small_building_is_watch(self):
        con = duckdb.connect(":memory:")
        _schema(con)
        cell = "LL:34.0309:-81.0361"
        for i in range(5):
            npi = 100 + i
            con.execute(
                "INSERT INTO cms_puf_npi_hours VALUES "
                f"({npi},'House','N{i}','Internal Medicine','I','Columbia','SC','29203',"
                f"'111 Doctor Cir','{cell}',75,FALSE,2743,1,1,5000)"
            )
            con.execute(
                f"INSERT INTO cms_puf_service_hours VALUES ({npi},'99213','O',5000,100)"
            )
        build_score(con)
        n_watch = con.execute(
            "SELECT COUNT(*) FROM cms_puf_npi_score WHERE watch_mill AND watch"
        ).fetchone()[0]
        self.assertEqual(n_watch, 5)
        self.assertEqual(
            con.execute(
                "SELECT npi_high_at_cell FROM cms_puf_npi_score WHERE npi=100"
            ).fetchone()[0],
            5,
        )
        self.assertGreater(
            con.execute(
                "SELECT priority FROM cms_puf_npi_score WHERE npi=100"
            ).fetchone()[0],
            0,
        )
        self.assertEqual(
            con.execute("SELECT COUNT(*) FROM cms_puf_watch_cells").fetchone()[0],
            1,
        )

    def test_office_outlier_in_small_site_is_solo_watch(self):
        con = duckdb.connect(":memory:")
        _schema(con)
        con.execute(
            "INSERT INTO cms_puf_npi_hours VALUES "
            "(2,'Solo','S','Family Practice','I','Town','TN','37877','10 Oak',"
            "'LL:36.2:-83.3',70,FALSE,900,1,1,5000)"
        )
        con.execute("INSERT INTO cms_puf_service_hours VALUES (2,'99213','O',5000,200)")
        for i in range(10):
            npi = 80 + i
            con.execute(
                "INSERT INTO cms_puf_npi_hours VALUES "
                f"({npi},'Peer','P{i}','Family Practice','I','Town','TN','37877','11 Oak',"
                f"'LL:36.2:-83.4',70,FALSE,5000,1,1,400)"
            )
            con.execute(
                f"INSERT INTO cms_puf_service_hours VALUES ({npi},'99213','O',400,20)"
            )
        build_score(con)
        row = con.execute(
            "SELECT watch_solo, watch_mill, watch, office_heavy, extreme_office "
            "FROM cms_puf_npi_score WHERE npi=2"
        ).fetchone()
        self.assertTrue(row[0])
        self.assertFalse(row[1])
        self.assertTrue(row[2])
        self.assertTrue(row[3])
        self.assertTrue(row[4])

    def test_facility_stack_is_not_mill(self):
        con = duckdb.connect(":memory:")
        _schema(con)
        cell = "LL:40.9535:-74.2128"
        for i in range(4):
            npi = 300 + i
            con.execute(
                "INSERT INTO cms_puf_npi_hours VALUES "
                f"({npi},'Round','R{i}','Internal Medicine','I','Wayne','NJ','07470',"
                f"'510 Hamburg Tpke','{cell}',75,FALSE,106,1,1,5000)"
            )
            con.execute(
                f"INSERT INTO cms_puf_service_hours VALUES ({npi},'99309','F',5000,100)"
            )
        con.execute(
            "INSERT INTO cms_puf_npi_hours VALUES "
            "(305,'Office','S','Podiatry','I','Wayne','NJ','07470',"
            f"'510 Hamburg Tpke','{cell}',75,FALSE,106,1,1,1064)"
        )
        con.execute("INSERT INTO cms_puf_service_hours VALUES (305,'99213','O',1064,40)")
        build_score(con)
        self.assertEqual(
            con.execute(
                "SELECT COUNT(*) FROM cms_puf_npi_score WHERE watch_mill"
            ).fetchone()[0],
            0,
        )

    def test_hospital_sized_stack_not_mill(self):
        con = duckdb.connect(":memory:")
        _schema(con)
        cell = "LL:34.0736:-118.3799"
        for i in range(5):
            npi = 200 + i
            con.execute(
                "INSERT INTO cms_puf_npi_hours VALUES "
                f"({npi},'Campus','C{i}','Cardiology','I','Los Angeles','CA','90048',"
                f"'8635 W 3rd St','{cell}',75,FALSE,3918,1,1,5000)"
            )
            con.execute(
                f"INSERT INTO cms_puf_service_hours VALUES ({npi},'99213','O',5000,100)"
            )
        build_score(con)
        self.assertEqual(
            con.execute(
                "SELECT COUNT(*) FROM cms_puf_npi_score WHERE watch_mill"
            ).fetchone()[0],
            0,
        )

    def test_optometry_not_solo_watch(self):
        con = duckdb.connect(":memory:")
        _schema(con)
        con.execute(
            "INSERT INTO cms_puf_npi_hours VALUES "
            "(3,'Kvitle','K','Optometry','I','Town','MN','55000','1 Eye',"
            "'LL:45.0:-93.0',70,FALSE,900,1,1,12000)"
        )
        con.execute("INSERT INTO cms_puf_service_hours VALUES (3,'66984','O',12000,400)")
        for i in range(10):
            npi = 70 + i
            con.execute(
                "INSERT INTO cms_puf_npi_hours VALUES "
                f"({npi},'Peer','O{i}','Optometry','I','Town','MN','55001','2 Eye',"
                f"'LL:45.1:-93.1',70,FALSE,5000,1,1,400)"
            )
            con.execute(
                f"INSERT INTO cms_puf_service_hours VALUES ({npi},'66984','O',400,20)"
            )
        build_score(con)
        row = con.execute(
            "SELECT optometry_type, watch_solo, watch FROM cms_puf_npi_score WHERE npi=3"
        ).fetchone()
        self.assertTrue(row[0])
        self.assertFalse(row[1])
        self.assertFalse(row[2])


if __name__ == "__main__":
    unittest.main()
