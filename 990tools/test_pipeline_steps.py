#!/usr/bin/env python3
"""--step all must cover every warehouse sidecar."""

import unittest

from irs990processor import IRS990Processor, PIPELINE_STEPS, STEP_ALIASES


class PipelineStepsTest(unittest.TestCase):
    def test_warehouse_sidecars_are_steps(self):
        for name in (
            "cms_puf",
            "cms_clia_pfs",
            "building_footprints",
            "usaspending_text",
            "cms_puf_hours",
            "cms_puf_score",
            "cms_puf_watch",
        ):
            self.assertIn(name, PIPELINE_STEPS)
            self.assertTrue(hasattr(IRS990Processor, f"run_{name}"))

    def test_from_scratch_order(self):
        idx = PIPELINE_STEPS.index
        self.assertLess(idx("medicare"), idx("cms_puf"))
        self.assertLess(idx("cms_puf"), idx("cms_clia_pfs"))
        self.assertLess(idx("cms_clia_pfs"), idx("building_footprints"))
        self.assertLess(idx("usaspending"), idx("usaspending_text"))
        self.assertLess(idx("cms_puf"), idx("cms_puf_hours"))
        self.assertLess(idx("cms_clia_pfs"), idx("cms_puf_hours"))
        self.assertLess(idx("building_footprints"), idx("cms_puf_hours"))
        self.assertLess(idx("geolocate_archive"), idx("cms_puf_hours"))
        self.assertLess(idx("address"), idx("cms_puf_hours"))
        self.assertLess(idx("cms_puf_hours"), idx("cms_puf_score"))
        self.assertLess(idx("cms_puf_score"), idx("cms_puf_watch"))

    def test_aliases_resolve_to_real_steps(self):
        for alias, target in STEP_ALIASES.items():
            self.assertIn(target, PIPELINE_STEPS)


if __name__ == "__main__":
    unittest.main()
