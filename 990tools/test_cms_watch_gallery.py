#!/usr/bin/env python3
import unittest

from cms_reporting.campus_flag import classify_campus, parse_height_m
from cms_reporting.generate_watch_gallery import (
    DISCLAIMER,
    TERMS,
    mill_filename,
    mill_payload,
    npi_payload,
    nppes_url,
    parse_ll,
    person_name,
    term,
)


class WatchGalleryHelpers(unittest.TestCase):
    def test_parse_ll(self):
        lat, lon = parse_ll("LL:34.0309:-81.0361")
        self.assertAlmostEqual(lat, 34.0309)
        self.assertAlmostEqual(lon, -81.0361)

    def test_mill_filename_is_stable(self):
        self.assertEqual(
            mill_filename("LL:34.0309:-81.0361"),
            "mill-ll-34-0309--81-0361.html",
        )

    def test_nppes_url(self):
        self.assertIn("1235424128", nppes_url(1235424128))

    def test_person_name(self):
        self.assertEqual(
            person_name({"name": "Paley", "first_name": "Harvey"}),
            "Paley, Harvey",
        )

    def test_disclaimer_is_not_an_accusation(self):
        self.assertIn("not a fraud determination", DISCLAIMER.lower())

    def test_term_hover_uses_title(self):
        html = term("Office-year stack", "stack")
        self.assertIn('class="term"', html)
        self.assertIn('title="', html)
        self.assertIn("2,080", html)
        self.assertIn(TERMS["stack"], html)

    def test_mill_columns_carry_hover_titles(self):
        payload, _ = mill_payload([])
        titles = {c["id"]: c.get("title") for c in payload["columns"]}
        self.assertEqual(titles["npi_high"], TERMS["stack"])
        self.assertEqual(titles["hours"], TERMS["hours"])
        self.assertEqual(titles["priority"], TERMS["priority"])
        self.assertEqual(titles["campus"], TERMS["campus"])
        self.assertIsNone(titles["street"])

    def test_campus_column_flags_leftover(self):
        payload, _ = mill_payload(
            [
                {
                    "colocator": "LL:42.3350:-71.1061",
                    "n_mill": 4,
                    "npi_high_at_cell": 5,
                    "max_area_near": 2123,
                    "hours_office": 10000,
                    "max_priority": 9,
                    "street": "75 Francis St",
                    "city": "Boston",
                    "state": "MA",
                    "zip": "02115",
                    "campus": True,
                    "campus_why": "OSM hospital 32 m: unnamed",
                }
            ]
        )
        row = payload["rows"][0]
        self.assertEqual(row["campus"], 1)
        self.assertIn("campus leftover", row["campus_html"])
        self.assertIn("OSM hospital", row["campus_html"])

    def test_flag_cells_hover_mill_and_solo(self):
        payload, _ = npi_payload(
            [
                {
                    "npi": "1",
                    "name": "Doe",
                    "first_name": "Jane",
                    "watch_mill": True,
                    "watch_solo": True,
                    "hours_office": 3000,
                    "office_vs_p90": 3,
                    "priority": 10,
                    "street": "1 Main",
                    "provider_type": "NP",
                }
            ]
        )
        titles = {c["id"]: c.get("title") for c in payload["columns"]}
        self.assertEqual(titles["kind"], TERMS["flag"])
        self.assertIn('title="', payload["rows"][0]["kind_html"])
        self.assertIn("mill", payload["rows"][0]["kind_html"])


class CampusFlag(unittest.TestCase):
    def test_parse_height_ft_and_m(self):
        self.assertAlmostEqual(parse_height_m("15 m"), 15)
        self.assertAlmostEqual(parse_height_m("49 ft"), 49 * 0.3048)

    def test_house_calls_is_not_campus(self):
        flag = classify_campus(
            osm_near=[{"dist_m": 10, "tags": {"building": "yes"}, "name": ""}],
            height_row={"height_max_m": 5.18, "class": ""},
        )
        self.assertFalse(flag["campus"])

    def test_francis_hospital_is_campus(self):
        flag = classify_campus(
            osm_near=[
                {
                    "dist_m": 32,
                    "tags": {"building": "hospital"},
                    "name": "",
                },
                {
                    "dist_m": 76,
                    "tags": {"building": "hospital", "building:levels": "15", "name": "Thorn Building"},
                    "name": "Thorn Building",
                },
            ],
            height_row={"height_max_m": 23.4, "class": ""},
        )
        self.assertTrue(flag["campus"])
        self.assertIn("hospital", flag["why"].lower())
        self.assertIn("15 floors", flag["why"])
        self.assertIn("23 m", flag["why"])

    def test_hackensack_class_hospital(self):
        flag = classify_campus(
            osm_near=[],
            height_row={
                "height_max_m": 13.63,
                "class": "hospital",
                "name": "John Theurer Cancer Center",
            },
        )
        self.assertTrue(flag["campus"])
        self.assertIn("hospital", flag["why"].lower())

    def test_parking_garage_12m_is_not_campus(self):
        flag = classify_campus(
            osm_near=[],
            height_row={"height_max_m": 12.3, "class": "parking"},
        )
        self.assertFalse(flag["campus"])


if __name__ == "__main__":
    unittest.main()
