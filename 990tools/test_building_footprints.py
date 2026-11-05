#!/usr/bin/env python3
import unittest

from building_footprints_processor import (
    STATE_SLUGS,
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


if __name__ == "__main__":
    unittest.main()
