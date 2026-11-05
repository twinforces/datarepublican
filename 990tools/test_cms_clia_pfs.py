#!/usr/bin/env python3
import tempfile
import unittest
from pathlib import Path

from cms_clia_pfs_processor import (
    RVU_SKIP,
    pick_latest_clia_csv,
    load_pfs_rvu,
    load_work_time,
)

try:
    import duckdb
except ImportError:  # pragma: no cover
    duckdb = None


class TestPickLatestClia(unittest.TestCase):
    def test_picks_newest_quarter(self):
        datasets = [
            {
                "title": "Provider of Services File - Clinical Laboratories : 2026-04-01",
                "distribution": [
                    {
                        "format": "CSV",
                        "downloadURL": "https://example/q2.csv",
                    }
                ],
            },
            {
                "title": "Provider of Services File - Clinical Laboratories : 2026-07-01",
                "distribution": [
                    {
                        "format": "API",
                        "downloadURL": "https://example/api",
                    },
                    {
                        "format": "CSV",
                        "downloadURL": "https://example/q3.csv",
                    },
                ],
            },
            {
                "title": "Medicare Clinical Laboratory Fee Schedule Private Payer Rates and Volumes : 2018-12-31",
                "distribution": [{"format": "CSV", "downloadURL": "https://example/clfs.csv"}],
            },
        ]
        url, title = pick_latest_clia_csv(datasets)
        self.assertEqual(url, "https://example/q3.csv")
        self.assertIn("2026-07-01", title)


@unittest.skipIf(duckdb is None, "duckdb not installed")
class TestPfsLoads(unittest.TestCase):
    def test_rvu_skip_and_aliases(self):
        preamble = "\n".join([f"preamble {i}" for i in range(RVU_SKIP)])
        header = (
            "HCPCS,MOD,DESCRIPTION,CODE,PAYMENT,RVU,PE RVU,INDICATOR,PE RVU,INDICATOR,"
            "RVU,TOTAL,TOTAL,IND,DAYS,OP,OP,OP,PROC,SURG,SURG,SURG,SURG,IND,BASE,FACTOR,"
            "PROCEDURES,FLAG,INDICATOR,AMOUNT,AMOUNT,AMOUNT"
        )
        row = (
            "99213,,Office o/p est low 20 min,A,,1.30,1.46,,0.33,,0.09,2.85,1.72,0,XXX,"
            "0.00,1.00,0.00,0,0,0,0,0,9,,33.4009,09,0,99,0.00,0.00,0.00"
        )
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "pprrvu.csv"
            path.write_text(preamble + "\n" + header + "\n" + row + "\n", encoding="utf-8")
            con = duckdb.connect(":memory:")
            n = load_pfs_rvu(con, path)
            self.assertEqual(n, 1)
            got = con.execute(
                "SELECT hcpcs, work_rvu, pe_rvu_nonfac, pe_rvu_fac, mp_rvu, intra_op FROM cms_pfs_rvu"
            ).fetchone()
            self.assertEqual(got[0], "99213")
            self.assertAlmostEqual(got[1], 1.30)
            self.assertAlmostEqual(got[2], 1.46)
            self.assertAlmostEqual(got[3], 0.33)
            self.assertAlmostEqual(got[4], 0.09)
            self.assertAlmostEqual(got[5], 1.00)

    def test_work_time_99213_is_30_min(self):
        csv = (
            "cpt_code,Pre_Evaluation_Time,Pre_Positioning_time,Pre_Service_Scrub_Dress_Wait_tim,"
            "Median_Intra_Service_Time,Immediate_post_Service_time,_99204,_99211,_99212,_99213,"
            "_99214,_99215,_99231,_99232,_99233,_99238,_99239,_99291,_99292,Total_time,"
            "Efficiency Adjusted Total_time,Efficiency Adjusted Intra_time\n"
            "99213,5,0,0,20,5,0,0,0,0,0,0,0,0,0,0,0,0,0,30,30,20\n"
        )
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "work.csv"
            path.write_text(csv, encoding="utf-8")
            con = duckdb.connect(":memory:")
            n = load_work_time(con, path)
            self.assertEqual(n, 1)
            got = con.execute(
                "SELECT cpt_code, intra_min, total_min, adj_total_min FROM cms_pfs_work_time"
            ).fetchone()
            self.assertEqual(got[0], "99213")
            self.assertAlmostEqual(got[1], 20.0)
            self.assertAlmostEqual(got[2], 30.0)
            self.assertAlmostEqual(got[3], 30.0)


if __name__ == "__main__":
    unittest.main()
