#!/usr/bin/env python3
import tempfile
import unittest
from pathlib import Path

import duckdb

from usaspending_text_processor import (
    collapse_group_sql,
    csv_headers,
    list_csv_sources,
    parse_csv_name,
    UsaSpendingTextProcessor,
)

CONTRACT = """contract_award_unique_key,award_id_piid,recipient_uei,recipient_name,transaction_description,prime_award_base_transaction_description,product_or_service_code,product_or_service_code_description,naics_code,naics_description
CONT_AWD_1,N00019,ABC123DEF456,ACME LLC,short,FEDERAL SUPPLY SCHEDULE CONTRACT,DA01,IT AND TELECOM - BUSINESS APP,541330,ENGINEERING SERVICES
CONT_AWD_1,N00019,ABC123DEF456,ACME LLC,this is the longer modification description for personal care staffing,FEDERAL SUPPLY SCHEDULE CONTRACT,DA01,IT AND TELECOM - BUSINESS APP,541330,ENGINEERING SERVICES
"""

ASSIST = """assistance_award_unique_key,award_id_fain,recipient_uei,recipient_name,transaction_description,cfda_number,cfda_title
ASST_1,GRANT-9,ABC123DEF456,ACME LLC,Connecticut will increase access for cell and gene therapy for sickle cell disease,93.885,CELL AND GENE THERAPY ACCESS MODEL
"""


class TestParse(unittest.TestCase):
    def test_csv_name(self):
        self.assertEqual(
            parse_csv_name("FY2025_All_Contracts_Full_20261006_2.csv"),
            ("contract", 2025),
        )
        self.assertEqual(
            parse_csv_name("FY2022_All_Assistance_Full_20261006.csv"),
            ("assistance", 2022),
        )

    def test_list_csv_sources_year_window(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            (d / "FY2025_All_Contracts_Full_20261006_1.csv").write_text(CONTRACT)
            (d / "FY2022_All_Assistance_Full_20261006_1.csv").write_text(ASSIST)
            (d / "notes.txt").write_text("nope")
            got = list_csv_sources(d, 2025, 2025)
            self.assertEqual(len(got), 1)
            self.assertEqual(got[0][0], "contract")


class TestCollapse(unittest.TestCase):
    def test_longest_description_wins(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "FY2025_All_Contracts_Full_20261006_1.csv"
            path.write_text(CONTRACT, encoding="utf-8")
            headers = csv_headers(path)
            sql = collapse_group_sql(headers, "contract", 2025)
            con = duckdb.connect(":memory:")
            con.execute(f"CREATE TABLE t AS {sql}", [str(path)])
            row = con.execute(
                "SELECT award_key, description, psc, program_code FROM t"
            ).fetchone()
            self.assertEqual(row[0], "CONT_AWD_1")
            self.assertIn("personal care", row[1])
            self.assertEqual(row[2], "DA01")
            self.assertEqual(row[3], "541330")
            self.assertEqual(con.execute("SELECT COUNT(*) FROM t").fetchone()[0], 1)

    def test_end_to_end_fts(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            (d / "FY2025_All_Contracts_Full_20261006_1.csv").write_text(CONTRACT)
            (d / "FY2025_All_Assistance_Full_20261006_1.csv").write_text(ASSIST)
            db = d / "t.duckdb"
            stats = UsaSpendingTextProcessor(str(db), d, start_year=2025, end_year=2025).run()
            self.assertEqual(stats["usaspending_award_text"], 2)
            con = duckdb.connect(str(db), read_only=True)
            con.execute("LOAD fts")
            hits = con.execute(
                """
                SELECT award_key, fts_main_usaspending_award_text.match_bm25(award_key, 'sickle cell') AS score
                FROM usaspending_award_text
                WHERE score IS NOT NULL
                ORDER BY score DESC
                """
            ).fetchall()
            self.assertEqual(hits[0][0], "ASST_1")
            care = con.execute(
                """
                SELECT award_key
                FROM usaspending_award_text
                WHERE fts_main_usaspending_award_text.match_bm25(award_key, 'personal care') IS NOT NULL
                """
            ).fetchall()
            self.assertEqual(care[0][0], "CONT_AWD_1")


if __name__ == "__main__":
    unittest.main()
