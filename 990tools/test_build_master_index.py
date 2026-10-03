#!/usr/bin/env python3
"""Master index matrix + featured Rogues gallery."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from dot_reporting.build_master_index import (
    collect_featured,
    collect_suites,
    parse_suite,
    render_html,
)


class ParseSuiteTests(unittest.TestCase):
    def test_unsafe_colocators_is_not_a_matrix_cell(self):
        self.assertIsNone(parse_suite("unsafe_colocators"))

    def test_dot_national_undated(self):
        meta = parse_suite("colocator_clusters")
        self.assertEqual(meta["focus"], "dot")
        self.assertEqual(meta["slice"], "colocator")
        self.assertEqual(meta["geo"], "national")
        self.assertEqual(meta["day"], "")


class FeaturedTests(unittest.TestCase):
    def test_featured_ready_when_index_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            gal = root / "unsafe_colocators"
            gal.mkdir()
            (gal / "index.html").write_text("<html>volume</html>", encoding="utf-8")
            (gal / "rates.html").write_text("<html>rates</html>", encoding="utf-8")
            (gal / "stacks.html").write_text("<html>stacks</html>", encoding="utf-8")
            featured = collect_featured(root)
            self.assertEqual(len(featured), 1)
            self.assertTrue(featured[0]["ready"])
            self.assertEqual(featured[0]["href"], "unsafe_colocators/index.html")
            html = render_html([], all_suites=[], reports_dir=root, featured=featured)
            self.assertIn("Rogues galleries", html)
            self.assertIn("DOT SMS Rogues gallery", html)
            self.assertIn("unsafe_colocators/index.html", html)
            self.assertIn("unsafe_colocators/rates.html", html)
            self.assertIn("unsafe_colocators/stacks.html", html)
            self.assertIn("Identity farms", html)
            self.assertRegex(html, r"generated \d{4}-\d{2}-\d{2} \d{2}:\d{2} P[DS]T")

    def test_matrix_still_lists_dot_colocator(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            suite = root / "colocator_clusters"
            suite.mkdir()
            (suite / "index.html").write_text("<html>dot</html>", encoding="utf-8")
            suites = collect_suites(root)
            html = render_html(suites, all_suites=suites, reports_dir=root, featured=[])
            self.assertIn("colocator_clusters/index.html", html)
            self.assertIn("DOT / trucking", html)


if __name__ == "__main__":
    unittest.main()
