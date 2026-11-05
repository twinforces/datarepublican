#!/usr/bin/env python3
import math
import unittest

from building_footprints_processor import (
    INGEST_VERSION,
    STATE_SLUGS,
    area_sql,
    colocator_sql,
    parse_states,
    zip_url,
)


class TestBuildingFootprints(unittest.TestCase):
    def test_tennessee_first(self):
        self.assertEqual(STATE_SLUGS[0], "Tennessee")
        self.assertEqual(len(STATE_SLUGS), 51)

    def test_parse_states_alias(self):
        self.assertEqual(parse_states("district of columbia,Tennessee"), ["DistrictofColumbia", "Tennessee"])

    def test_zip_url(self):
        url = zip_url("Tennessee")
        self.assertIn("Tennessee.geojson.zip", url)
        self.assertIn("usbuildings-v2", url)

    def test_colocator_sql_matches_address_round4(self):
        sql = colocator_sql("lat", "lon")
        self.assertIn("ROUND(lat, 4)", sql)
        self.assertIn("printf('LL:%.4f:%.4f'", sql)

    def test_area_sql_flips_coordinates(self):
        self.assertEqual(INGEST_VERSION, 2)
        self.assertEqual(
            area_sql("geom"),
            "ST_Area_Spheroid(ST_FlipCoordinates(geom))",
        )

    def test_flip_makes_western_area_finite(self):
        try:
            import duckdb
        except ImportError:
            self.skipTest("duckdb missing")
        con = duckdb.connect(":memory:")
        con.execute("INSTALL spatial")
        con.execute("LOAD spatial")
        east = '{"type":"Polygon","coordinates":[[[-75.050029,38.456823],[-75.050029,38.456926],[-75.050274,38.456926],[-75.050274,38.456823],[-75.050029,38.456823]]]}'
        west = '{"type":"Polygon","coordinates":[[[-104.0478,41.9991],[-104.0478,41.9993],[-104.0475,41.9993],[-104.0475,41.9991],[-104.0478,41.9991]]]}'
        east_raw, east_flip, west_raw, west_flip = con.execute(
            """
            SELECT
              ST_Area_Spheroid(ST_GeomFromGeoJSON(?)),
              ST_Area_Spheroid(ST_FlipCoordinates(ST_GeomFromGeoJSON(?))),
              ST_Area_Spheroid(ST_GeomFromGeoJSON(?)),
              ST_Area_Spheroid(ST_FlipCoordinates(ST_GeomFromGeoJSON(?)))
            """,
            [east, east, west, west],
        ).fetchone()
        self.assertTrue(math.isnan(west_raw))
        self.assertFalse(math.isnan(west_flip))
        self.assertGreater(west_flip, 0)
        self.assertGreater(east_flip, east_raw)
        self.assertGreater(east_flip / east_raw, 2.5)


if __name__ == "__main__":
    unittest.main()
