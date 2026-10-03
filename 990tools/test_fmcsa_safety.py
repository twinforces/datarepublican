#!/usr/bin/env python3
"""SMS ingest projection + colocator ranking (in-memory DuckDB)."""

from __future__ import annotations

import json
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path

import duckdb

from fmcsa_safety_processor import (
    DATASETS,
    INGEST_VERSION,
    FmcsaSafetyProcessor,
    attach_sms,
    load_sms_by_dot,
    normalize_dot_sql,
    set_flag_weights,
    sms_flag_true_sql,
)
from dot_reporting.cluster_table_payload import (
    build_detail_tables as shared_detail_tables,
)
from dot_reporting.generate_safety_gallery import (
    FAT_PER_CRASH,
    FAT_PER_FLAG,
    FAT_PER_MAINT,
    FAT_PER_OOS,
    FAT_PER_UNSAFE,
    TERMS,
    build_index_table,
    cluster_sql,
    measure_fatality_weights,
    rate_sql,
    slug,
    stack_sql,
    term,
    write_gallery,
)


class FakeDbOps:
    def __init__(self, conn: duckdb.DuckDBPyConnection):
        self.conn = conn

    @contextmanager
    def acquire_write_conn(self):
        yield self.conn

    def execute_query(self, query: str):
        return self.conn.execute(query)


def _spec(key: str) -> dict:
    return next(s for s in DATASETS if s["key"] == key)


class NormalizeDotTests(unittest.TestCase):
    def test_strips_leading_zeros(self):
        con = duckdb.connect(":memory:")
        sql = f"SELECT {normalize_dot_sql('x')} FROM (SELECT '000123' AS x)"
        self.assertEqual(con.execute(sql).fetchone()[0], "123")

    def test_blank_is_null(self):
        con = duckdb.connect(":memory:")
        sql = f"SELECT {normalize_dot_sql('x')} FROM (SELECT '  ' AS x)"
        self.assertIsNone(con.execute(sql).fetchone()[0])

    def test_flag_true_accepts_socrata_bool_and_y(self):
        con = duckdb.connect(":memory:")
        expr = sms_flag_true_sql("f")
        rows = con.execute(
            f"""
            SELECT f, {expr} AS hit FROM (VALUES
                ('Y'), ('true'), ('TRUE'), ('1'), ('N'), ('false'), ('')
            ) t(f)
            """
        ).fetchall()
        hits = {r[0]: r[1] for r in rows}
        self.assertTrue(hits["Y"])
        self.assertTrue(hits["true"])
        self.assertTrue(hits["TRUE"])
        self.assertTrue(hits["1"])
        self.assertFalse(hits["N"])
        self.assertFalse(hits["false"])
        self.assertFalse(hits[""])


class PromoteCsvTests(unittest.TestCase):
    def test_crash_csv_projects_keep_and_dot_norm(self):
        con = duckdb.connect(":memory:")
        proc = FmcsaSafetyProcessor(FakeDbOps(con), tempfile.mkdtemp())
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sms_input_crash.csv"
            path.write_text(
                "DOT_Number,Report_number,Fatalities,Tow_Away,Severity_Weight,Time_Weight\n"
                "00086876,TX1,2,true,2,3\n"
                "21800,CA2,0,false,1,1\n",
                encoding="utf-8",
            )
            n = proc._promote_csv(path, _spec("crash"))
        self.assertEqual(n, 2)
        dots = {
            r[0]: r[1]
            for r in con.execute(
                "SELECT dot_norm, fatalities FROM fmcsa_sms_crashes ORDER BY 1"
            ).fetchall()
        }
        self.assertEqual(dots["86876"], "2")
        self.assertEqual(dots["21800"], "0")
        cols = {r[0] for r in con.execute("DESCRIBE fmcsa_sms_crashes").fetchall()}
        self.assertIn("injuries", cols)
        self.assertIn("dot_norm", cols)
        missing = con.execute(
            "SELECT injuries FROM fmcsa_sms_crashes WHERE dot_norm = '86876'"
        ).fetchone()[0]
        self.assertIsNone(missing)

    def test_skip_when_marker_matches(self):
        tmp = Path(tempfile.mkdtemp())
        csv = tmp / "sms_input_crash.csv"
        csv.write_text("DOT_Number\n1\n", encoding="utf-8")
        marker = tmp / ".crash_ingest.json"
        marker.write_text(
            json.dumps(
                {
                    "source": str(csv.resolve()),
                    "size": csv.stat().st_size,
                    "mtime": csv.stat().st_mtime,
                    "ingest_version": INGEST_VERSION,
                }
            ),
            encoding="utf-8",
        )
        con = duckdb.connect(":memory:")
        con.execute("CREATE TABLE fmcsa_sms_crashes (dot_norm VARCHAR)")
        con.execute("INSERT INTO fmcsa_sms_crashes VALUES ('1')")
        proc = FmcsaSafetyProcessor(FakeDbOps(con), tmp)
        self.assertTrue(proc._ingest_current(csv, marker, "fmcsa_sms_crashes"))


class ClusterRankingTests(unittest.TestCase):
    def setUp(self):
        set_flag_weights()
        self.con = duckdb.connect(":memory:")
        self.con.execute(
            """
            CREATE TABLE Addresses (
                owner_id VARCHAR,
                address_type VARCHAR,
                colocator VARCHAR,
                canonical_address VARCHAR,
                zip_code VARCHAR,
                latitude DOUBLE,
                longitude DOUBLE
            );
            CREATE TABLE dot_carriers (
                id VARCHAR,
                dot_number VARCHAR,
                legal_name VARCHAR,
                dba_name VARCHAR,
                power_units INTEGER,
                status_code VARCHAR,
                phone VARCHAR
            );
            CREATE TABLE fmcsa_sms_crashes (
                dot_norm VARCHAR,
                fatalities VARCHAR,
                injuries VARCHAR,
                tow_away VARCHAR,
                severity_weight VARCHAR,
                time_weight VARCHAR
            );
            CREATE TABLE fmcsa_sms_inspections (
                dot_norm VARCHAR,
                oos_total VARCHAR
            );
            CREATE TABLE fmcsa_sms_basics (
                dot_norm VARCHAR,
                insp_total VARCHAR,
                driver_oos_insp_total VARCHAR,
                vehicle_oos_insp_total VARCHAR,
                unsafe_driv_ac VARCHAR,
                unsafe_driv_basic_alert VARCHAR,
                hos_driv_ac VARCHAR,
                hos_driv_basic_alert VARCHAR,
                driv_fit_ac VARCHAR,
                driv_fit_basic_alert VARCHAR,
                contr_subst_ac VARCHAR,
                contr_subst_basic_alert VARCHAR,
                veh_maint_ac VARCHAR,
                veh_maint_basic_alert VARCHAR,
                unsafe_load_ac VARCHAR,
                unsafe_load_basic_alert VARCHAR,
                crash_ind_ac VARCHAR,
                crash_ind_basic_alert VARCHAR
            );
            """
        )
        self.con.execute(
            """
            INSERT INTO dot_carriers VALUES
                ('c1', '000111', 'Fatal Co', NULL, 4, 'A', '555-0100'),
                ('c2', '222', 'Crash Co', NULL, 2, 'A', '555-0100'),
                ('c3', '333', 'OOS Co', NULL, 1, 'A', '555-0200'),
                ('c4', '444', 'Mail Only', NULL, 8, 'A', NULL),
                ('c5', '555', 'Zip Cluster', NULL, 1, 'A', NULL);
            INSERT INTO Addresses VALUES
                ('c1', 'dot_carrier_phy', 'LL:33.9:-84.3', '100 Yard Rd', '30301', 33.9, -84.3),
                ('c2', 'dot_carrier_phy', 'LL:33.9:-84.3', '100 Yard Rd', '30301', 33.9, -84.3),
                ('c3', 'dot_carrier_phy', 'LL:40.7:-74.0', '9 Dock St', '10013', 40.7, -74.0),
                ('c4', 'dot_carrier_mail', 'LL:33.9:-84.3', 'PO BOX 1', '30301', NULL, NULL),
                ('c5', 'dot_carrier_phy', 'ZIP:99999', '1 Nowhere', '99999', NULL, NULL);
            INSERT INTO fmcsa_sms_crashes VALUES
                ('111', '2', '1', 'true', '2', '3'),
                ('222', '0', '0', 'Y', '1', '1'),
                ('444', '9', '0', 'true', '3', '3'),
                ('555', '4', '0', 'true', '2', '2');
            INSERT INTO fmcsa_sms_inspections VALUES
                ('333', '3'),
                ('333', '0');
            INSERT INTO fmcsa_sms_basics VALUES
                ('222', '10', '0', '0',
                 'N', NULL, 'N', NULL, 'N', NULL, 'N', NULL, 'Y', NULL, NULL, NULL, NULL, NULL);
            """
        )

    def test_ranks_fatalities_then_crashes_then_oos(self):
        rows = self.con.execute(cluster_sql(), [10]).fetchall()
        cols = [d[0] for d in self.con.description]
        clusters = [dict(zip(cols, row)) for row in rows]
        keys = [c["cluster_key"] for c in clusters]
        self.assertEqual(keys[0], "LL:33.9:-84.3")
        self.assertEqual(keys[1], "LL:40.7:-74.0")
        self.assertNotIn("ZIP:99999", keys)
        top = clusters[0]
        self.assertEqual(int(top["fatalities"]), 2)
        self.assertEqual(int(top["crashes"]), 2)
        self.assertEqual(int(top["phy_carriers"]), 2)
        self.assertEqual(int(top["basic_alert_carriers"]), 1)
        self.assertEqual(int(clusters[1]["oos_inspections"]), 1)
        hot = top["hot_carriers"]
        names = [h["legal_name"] if isinstance(h, dict) else h["legal_name"] for h in hot]
        self.assertEqual(names[0], "Fatal Co")

    def test_measure_fatality_weights_from_sms_tables(self):
        w_crash, w_flag, w_oos, meta = measure_fatality_weights(self.con)
        self.assertEqual(meta["crash_records"], 4)
        self.assertEqual(meta["crash_fatalities"], 15)
        self.assertAlmostEqual(w_crash, 15 / 4)
        self.assertEqual(meta["basic_dots"], 1)
        self.assertEqual(meta["basic_flags"], 1)
        self.assertAlmostEqual(w_flag, 15 / 1)
        self.assertEqual(meta["flag_counts"]["maint"], 1)
        self.assertEqual(meta["flag_counts"]["unsafe"], 0)
        self.assertAlmostEqual(meta["fat_per_flag_cat"]["maint"], 15 / 1)
        self.assertEqual(meta["fat_per_flag_cat"]["unsafe"], 0.0)
        self.assertEqual(meta["oos_inspections"], 1)
        self.assertAlmostEqual(w_oos, 15 / 1)

    def test_spoil_rate_scales_by_fleet_size(self):
        """Expected-fatality weights: 26 crashes < 1 death; FedEx rate is tiny."""
        crash_rows = ",\n".join(
            f"('7101', '0', '0', 'true', '1', '1')" for _ in range(26)
        )
        self.con.execute(
            f"""
            INSERT INTO dot_carriers VALUES
                ('g1', '6101', 'Mega Fleet', NULL, 80000, 'A', '9015550000'),
                ('g2', '6103', 'Paper Crash At Mega', NULL, 1, 'A', '9015550001'),
                ('p1', '6102', 'One Truck Crash', NULL, 1, 'A', '5551110000'),
                ('d1', '7102', 'One Dead Truck', NULL, 1, 'A', '5552220000'),
                ('r1', '7101', 'Richmond Crash', NULL, 1, 'A', '8045550000');
            INSERT INTO Addresses VALUES
                ('g1', 'dot_carrier_phy', 'LL:35.1:-90.1', 'Mega Hub', '38118', 35.1, -90.1),
                ('g2', 'dot_carrier_phy', 'LL:35.1:-90.1', 'Mega Hub', '38118', 35.1, -90.1),
                ('p1', 'dot_carrier_phy', 'LL:34.0:-118.2', 'Tiny Lot', '90755', 34.0, -118.2),
                ('d1', 'dot_carrier_phy', 'LL:39.0:-84.5', 'Dead Lot', '41042', 39.0, -84.5),
                ('r1', 'dot_carrier_phy', 'LL:37.5:-77.4', 'Richmond Lot', '23223', 37.5, -77.4);
            INSERT INTO fmcsa_sms_crashes VALUES
                ('6101', '85', '0', 'true', '3', '3'),
                ('6103', '0', '0', 'true', '1', '1'),
                ('6102', '0', '0', 'true', '1', '1'),
                ('7102', '1', '0', 'true', '3', '3'),
                {crash_rows};
            """
        )
        rows = self.con.execute(rate_sql(), [10]).fetchall()
        cols = [d[0] for d in self.con.description]
        clusters = [dict(zip(cols, row)) for row in rows]
        by_key = {c["cluster_key"]: c for c in clusters}
        tiny = by_key["LL:34.0:-118.2"]
        mega = by_key["LL:35.1:-90.1"]
        dead = by_key["LL:39.0:-84.5"]
        richmond = by_key["LL:37.5:-77.4"]
        self.assertAlmostEqual(float(tiny["spoil_rate"]), FAT_PER_CRASH, places=5)
        self.assertAlmostEqual(float(tiny["spoil_in"]), FAT_PER_CRASH, places=5)
        # Outside: 85 deaths outweigh 2 weighted crashes. Inside: 1-PU crash is w_crash.
        self.assertAlmostEqual(float(mega["spoil_rate"]), 85 / 80001, places=6)
        self.assertAlmostEqual(float(mega["spoil_in"]), FAT_PER_CRASH, places=5)
        self.assertLess(float(mega["spoil_rate"]), float(tiny["spoil_rate"]))
        yard = next(c for c in clusters if c["cluster_key"] == "LL:33.9:-84.3")
        # outside: max(2 fat, 1 maint flag, 2*w_crash, 0 OOS) / 6 PU
        self.assertAlmostEqual(float(yard["spoil_rate"]), FAT_PER_MAINT / 6, places=5)
        # inside: Crash Co 1 maint flag / 2 PU
        self.assertAlmostEqual(float(yard["spoil_in"]), FAT_PER_MAINT / 2, places=5)
        self.assertGreater(FAT_PER_UNSAFE, FAT_PER_MAINT)
        self.assertAlmostEqual(float(dead["spoil_rate"]), 1.0, places=5)
        self.assertAlmostEqual(
            float(richmond["spoil_rate"]), 26 * FAT_PER_CRASH, places=5
        )
        self.assertLess(float(richmond["spoil_rate"]), float(dead["spoil_rate"]))
        self.assertGreater(float(richmond["spoil_rate"]), 0.5)
        # One maint flag (~23.5) on the 6-PU yard outranks a 1-truck death.
        self.assertGreater(float(yard["spoil_rate"]), float(dead["spoil_rate"]))
        self.assertEqual(clusters[0]["cluster_key"], "LL:33.9:-84.3")

    def test_unsafe_flag_outweighs_maint_flag(self):
        """Rare unsafe-driving alert scores more than a common maint alert."""
        self.con.execute(
            """
            INSERT INTO dot_carriers VALUES
                ('u1', '9101', 'Unsafe One Truck', NULL, 1, 'A', '5553330000'),
                ('m1b', '9102', 'Maint One Truck', NULL, 1, 'A', '5553330001');
            INSERT INTO Addresses VALUES
                ('u1', 'dot_carrier_phy', 'LL:41.0:-83.0', 'Unsafe Lot', '43420', 41.0, -83.0),
                ('m1b', 'dot_carrier_phy', 'LL:41.1:-83.1', 'Maint Lot', '44805', 41.1, -83.1);
            INSERT INTO fmcsa_sms_basics VALUES
                ('9101', '1', '0', '0',
                 'Y', NULL, 'N', NULL, 'N', NULL, 'N', NULL, 'N', NULL, NULL, NULL, NULL, NULL),
                ('9102', '1', '0', '0',
                 'N', NULL, 'N', NULL, 'N', NULL, 'N', NULL, 'Y', NULL, NULL, NULL, NULL, NULL);
            """
        )
        rows = self.con.execute(rate_sql(), [20]).fetchall()
        cols = [d[0] for d in self.con.description]
        by_key = {c["cluster_key"]: c for c in (dict(zip(cols, row)) for row in rows)}
        unsafe = by_key["LL:41.0:-83.0"]
        maint = by_key["LL:41.1:-83.1"]
        self.assertAlmostEqual(float(unsafe["spoil_in"]), FAT_PER_UNSAFE, places=5)
        self.assertAlmostEqual(float(maint["spoil_in"]), FAT_PER_MAINT, places=5)
        self.assertGreater(float(unsafe["spoil_in"]), float(maint["spoil_in"]))

    def test_mail_address_does_not_stack(self):
        rows = self.con.execute(cluster_sql(), [10]).fetchall()
        cols = [d[0] for d in self.con.description]
        clusters = [dict(zip(cols, row)) for row in rows]
        yard = next(c for c in clusters if c["cluster_key"] == "LL:33.9:-84.3")
        self.assertEqual(int(yard["fatalities"]), 2)

    def test_stack_rank_is_shared_phone_identity_farm(self):
        """Farms stay in. Fatalities on two DOTs of one phone beat a 1-fat mill."""
        self.con.execute(
            """
            INSERT INTO dot_carriers VALUES
                ('s1', '9001', 'Bad Anchor', NULL, 20, 'A', '5559001000'),
                ('s2', '9002', 'Paper Two', NULL, 0, 'A', '5559001000'),
                ('s3', '9003', 'Paper Three', NULL, 0, 'A', '5559001000'),
                ('s4', '9004', 'Paper Four', NULL, 0, 'A', '5559001000'),
                ('m1', '8001', 'Mill One-Fat', NULL, 1, 'A', '5558000000'),
                ('m2', '8002', 'Mill Quiet A', NULL, 0, 'A', '5558000000'),
                ('m3', '8003', 'Mill Quiet B', NULL, 0, 'A', '5558000000'),
                ('m4', '8004', 'Mill Quiet C', NULL, 0, 'A', '5558000000'),
                ('m5', '8005', 'Mill Quiet D', NULL, 0, 'A', '5558000000'),
                ('t1', '7001', 'Swap Old', NULL, 4, 'A', '4806272700'),
                ('t2', '7002', 'Swap New', NULL, 2, 'A', '4806272700'),
                ('f1', '6001', 'FedEx Pair Harm', NULL, 80000, 'A', '9014343000'),
                ('f2', '6002', 'FedEx Pair Quiet', NULL, 10, 'A', '9014343000'),
                ('r1', '5001', 'Allied Waste HQ', NULL, 1817, 'A', '4806272700'),
                ('r2', '5002', 'Republic Texas', NULL, 578, 'A', '4806272700'),
                ('r3', '5003', 'Republic Quiet', NULL, 400, 'A', '4806272700'),
                ('v1', '5101', 'United Van Lines', NULL, 4731, 'A', '6363055000'),
                ('v2', '5102', 'Paper Agent A', NULL, 1, 'A', '6363055000'),
                ('v3', '5103', 'Paper Agent B', NULL, 0, 'A', '6363055000'),
                ('v4', '5104', 'Paper Agent C', NULL, 1, 'A', '6363055000'),
                ('h1', '5201', 'Hill One', NULL, 1, 'A', '2134947314'),
                ('h2', '5202', 'Hill Two', NULL, 1, 'A', '2134947314'),
                ('h3', '5203', 'Hill Three', NULL, 1, 'A', '2134947314'),
                ('h4', '5204', 'Hill Four', NULL, 1, 'A', '2134947314');
            INSERT INTO Addresses VALUES
                ('s1', 'dot_carrier_phy', 'LL:10.0:10.0', '9 Bad Yard', '85001', 10.0, 10.0),
                ('s2', 'dot_carrier_phy', 'LL:10.0:10.0', '9 Bad Yard', '85001', 10.0, 10.0),
                ('s3', 'dot_carrier_phy', 'LL:10.0:10.0', '9 Bad Yard', '85001', 10.0, 10.0),
                ('s4', 'dot_carrier_phy', 'LL:10.0:10.0', '9 Bad Yard', '85001', 10.0, 10.0),
                ('m1', 'dot_carrier_phy', 'LL:20.0:20.0', '1 Mailbox Ste 200', '92154', 20.0, 20.0),
                ('m2', 'dot_carrier_phy', 'LL:20.0:20.0', '1 Mailbox Ste 200', '92154', 20.0, 20.0),
                ('m3', 'dot_carrier_phy', 'LL:20.0:20.0', '1 Mailbox Ste 200', '92154', 20.0, 20.0),
                ('m4', 'dot_carrier_phy', 'LL:20.0:20.0', '1 Mailbox Ste 200', '92154', 20.0, 20.0),
                ('m5', 'dot_carrier_phy', 'LL:20.0:20.0', '1 Mailbox Ste 200', '92154', 20.0, 20.0),
                ('t1', 'dot_carrier_phy', 'LL:30.0:30.0', 'Vinyl Lot', '85054', 30.0, 30.0),
                ('t2', 'dot_carrier_phy', 'LL:30.0:30.0', 'Vinyl Lot', '85054', 30.0, 30.0),
                ('f1', 'dot_carrier_phy', 'LL:35.0:-90.0', 'FedEx Pair', '38125', 35.0, -90.0),
                ('f2', 'dot_carrier_phy', 'LL:35.0:-90.0', 'FedEx Pair', '38125', 35.0, -90.0),
                ('r1', 'dot_carrier_phy', 'LL:33.68:-111.95', 'Phoenix Trash HQ', '85054', 33.68, -111.95),
                ('r2', 'dot_carrier_phy', 'LL:33.68:-111.95', 'Phoenix Trash HQ', '85054', 33.68, -111.95),
                ('r3', 'dot_carrier_phy', 'LL:33.68:-111.95', 'Phoenix Trash HQ', '85054', 33.68, -111.95),
                ('v1', 'dot_carrier_phy', 'LL:38.54:-90.44', 'Mayflower HQ', '63026', 38.54, -90.44),
                ('v2', 'dot_carrier_phy', 'LL:38.54:-90.44', 'Mayflower HQ', '63026', 38.54, -90.44),
                ('v3', 'dot_carrier_phy', 'LL:38.54:-90.44', 'Mayflower HQ', '63026', 38.54, -90.44),
                ('v4', 'dot_carrier_phy', 'LL:38.54:-90.44', 'Mayflower HQ', '63026', 38.54, -90.44),
                ('h1', 'dot_carrier_phy', 'LL:33.82:-118.18', '3377 California Ave', '90755', 33.82, -118.18),
                ('h2', 'dot_carrier_phy', 'LL:33.82:-118.18', '3377 California Ave', '90755', 33.82, -118.18),
                ('h3', 'dot_carrier_phy', 'LL:33.82:-118.18', '3377 California Ave', '90755', 33.82, -118.18),
                ('h4', 'dot_carrier_phy', 'LL:33.82:-118.18', '3377 California Ave', '90755', 33.82, -118.18);
            INSERT INTO fmcsa_sms_crashes VALUES
                ('9001', '6', '0', 'true', '3', '3'),
                ('8001', '1', '0', 'true', '1', '1'),
                ('7001', '2', '0', 'true', '2', '2'),
                ('7002', '1', '0', 'true', '1', '1'),
                ('6001', '85', '0', 'true', '3', '3'),
                ('5001', '4', '0', 'true', '3', '3'),
                ('5002', '2', '0', 'true', '2', '2'),
                ('5101', '1', '0', 'true', '1', '1');
            INSERT INTO fmcsa_sms_inspections VALUES
                ('5201', '2');
            """
        )
        rows = self.con.execute(stack_sql(), [10]).fetchall()
        cols = [d[0] for d in self.con.description]
        clusters = [dict(zip(cols, row)) for row in rows]
        keys = [c["cluster_key"] for c in clusters]
        self.assertEqual(keys[0], "LL:20.0:20.0")
        self.assertIn("LL:33.82:-118.18", keys)
        self.assertIn("LL:10.0:10.0", keys)
        self.assertIn("LL:30.0:30.0", keys)
        self.assertNotIn("LL:35.0:-90.0", keys)
        self.assertNotIn("LL:33.9:-84.3", keys)
        self.assertNotIn("LL:33.68:-111.95", keys)
        self.assertNotIn("LL:38.54:-90.44", keys)
        mill = clusters[0]
        self.assertEqual(int(mill["extra_identities"]), 4)
        self.assertEqual(int(mill["fat_dots"]), 1)
        # 1-truck death × 5 DOT records
        self.assertAlmostEqual(float(mill["spoil_in"]), 1.0, places=5)
        self.assertAlmostEqual(float(mill["mill_score"]), 5.0, places=5)
        hill = next(c for c in clusters if c["cluster_key"] == "LL:33.82:-118.18")
        self.assertEqual(int(hill["extra_identities"]), 3)
        self.assertEqual(int(hill["fat_dots"]), 0)
        self.assertEqual(int(hill["crash_dots"]), 0)
        # OOS-only mill: 1 OOS on a 1-PU apple × 4 DOT records
        self.assertAlmostEqual(float(hill["mill_score"]), 4 * FAT_PER_OOS, places=5)
        vinyl = next(c for c in clusters if c["cluster_key"] == "LL:30.0:30.0")
        self.assertEqual(int(vinyl["fat_dots"]), 2)
        self.assertEqual(int(vinyl["crash_dots"]), 2)
        # max(2/4, 1/2) × 2 DOT records = 1.0
        self.assertAlmostEqual(float(vinyl["mill_score"]), 1.0, places=5)
        self.assertGreater(float(mill["mill_score"]), float(vinyl["mill_score"]))
        self.assertGreater(float(mill["mill_score"]), float(hill["mill_score"]))

    def test_sms_lookup_attaches_by_dot_norm(self):
        sms = load_sms_by_dot(self.con)
        self.assertIsNotNone(sms)
        carriers = [{"dot_number": "000111", "legal_name": "Fatal Co"}]
        attach_sms(carriers, sms)
        self.assertEqual(carriers[0]["fatalities"], 2)
        self.assertEqual(carriers[0]["crashes"], 1)
        crash = [{"dot_number": "222", "legal_name": "Crash Co"}]
        attach_sms(crash, sms)
        self.assertTrue(crash[0]["basic_maint"])
        self.assertEqual(crash[0]["basic_flags"], 1)
        self.assertFalse(crash[0]["basic_hos"])

    def tearDown(self):
        set_flag_weights()
        self.con.close()


class GalleryHtmlTests(unittest.TestCase):
    def setUp(self):
        set_flag_weights()

    def test_writes_index_and_detail(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            write_gallery(
                [
                    {
                        "cluster_key": "LL:33.9:-84.3",
                        "canonical_address": "100 Yard Rd",
                        "zip_code": "30301",
                        "fatalities": 2,
                        "crashes": 3,
                        "injuries": 1,
                        "oos_inspections": 0,
                        "basic_alert_carriers": 1,
                        "phy_carriers": 2,
                        "hot_carriers": [
                            {
                                "dot_number": "111",
                                "legal_name": "Fatal Co",
                                "fatalities": 2,
                                "crashes": 1,
                                "oos_inspections": 0,
                                "basic_alert": False,
                            }
                        ],
                        "phy_roster": [
                            {
                                "dot_number": "111",
                                "legal_name": "Fatal Co",
                                "status_code": "A",
                                "power_units": 4,
                                "phone": "555-0100",
                                "fatalities": 2,
                                "crashes": 1,
                                "oos_inspections": 0,
                                "basic_alert": True,
                                "basic_flags": 1,
                                "basic_maint": True,
                            }
                        ],
                    }
                ],
                out,
                db_path="/tmp/test.duckdb",
                rates=[
                    {
                        "cluster_key": "LL:34.0:-118.2",
                        "canonical_address": "Tiny Lot",
                        "zip_code": "90755",
                        "fatalities": 0,
                        "crashes": 1,
                        "basic_alert_carriers": 0,
                        "power_units": 1,
                        "phy_carriers": 1,
                        "spoil_rate": 1.0,
                        "spoil_in": 1.0,
                    }
                ],
                stacks=[
                    {
                        "cluster_key": "LL:10.0:10.0",
                        "canonical_address": "9 Quiet Yard",
                        "zip_code": "85001",
                        "fatalities": 6,
                        "crashes": 1,
                        "injuries": 0,
                        "oos_inspections": 0,
                        "basic_alert_carriers": 0,
                        "phy_carriers": 4,
                        "extra_identities": 3,
                        "worst_fatalities": 6,
                        "stack_score": 200306,
                        "fat_dots": 2,
                        "harm_phones": 1,
                        "phone_fatalities": 6,
                        "biggest_phone": 4,
                        "hot_carriers": [],
                        "phy_roster": [],
                    }
                ],
            )
            index = (out / "index.html").read_text(encoding="utf-8")
            self.assertRegex(
                index, r"Generated \d{4}-\d{2}-\d{2} \d{2}:\d{2} P[DS]T"
            )
            self.assertNotIn(" UTC", index)
            self.assertIn("100 Yard Rd", index)
            self.assertIn("../master_index.html", index)
            self.assertIn("score per truck", index)
            self.assertIn("death-first", index)
            self.assertIn(f"unsafe-{slug('LL:33.9:-84.3')}.html", index)
            self.assertIn("ts-table-root", index)
            self.assertIn("__CLUSTER_TABLE__", index)
            self.assertIn("@tanstack/table-core", index)
            detail = (out / f"unsafe-{slug('LL:33.9:-84.3')}.html").read_text(
                encoding="utf-8"
            )
            self.assertIn("Fatal Co", detail)
            self.assertIn("safer.fmcsa.dot.gov", detail)
            self.assertIn("searchcarriers.com/company/111", detail)
            self.assertIn("motus.dot.gov", detail)
            self.assertIn("All physical USDOT numbers", detail)
            self.assertIn("__TS_TABLES__", detail)
            self.assertIn("ts-carriers", detail)
            self.assertIn("ts-phone-groups", detail)
            self.assertIn("Power units", detail)
            self.assertIn("Maint", detail)
            self.assertIn("Vehicle maintenance", detail)
            stacks = (out / "stacks.html").read_text(encoding="utf-8")
            self.assertIn("mill", stacks.lower())
            self.assertIn("vet-your-drivers", stacks.lower())
            self.assertIn("9 Quiet Yard", stacks)
            self.assertIn("index.html", stacks)
            self.assertIn("__CLUSTER_TABLE__", stacks)
            self.assertIn("ts-table-root", stacks)
            self.assertIn(TERMS["extra_quiet"], stacks)
            self.assertIn(TERMS["fat_dots"], stacks)
            self.assertIn(TERMS["score"], stacks)
            self.assertIn(TERMS["mill_score"], stacks)
            self.assertIn(TERMS["max_pu"], stacks)
            self.assertIn("Behavior Analysis and Safety Improvement Categories", stacks)
            self.assertIn(TERMS["crashes"], stacks)
            self.assertIn(TERMS["crash_dots"], stacks)
            rates = (out / "rates.html").read_text(encoding="utf-8")
            self.assertIn("Tiny Lot", rates)
            self.assertIn(TERMS["spoil_rate"], rates)
            self.assertIn("worst USDOT", rates)
            self.assertIn(f"{FAT_PER_CRASH:.4f}", rates)
            self.assertIn("per crash", rates)
            self.assertIn("sum(fatalities)/sum(metric)", rates)
            self.assertIn("Per flag", rates)
            self.assertIn("per U", rates)
            self.assertIn("per Maint", rates)
            self.assertIn("per OOS", rates)
            self.assertIn("one-truck yard", rates)
            rate_payload = build_index_table(
                [
                    {
                        "cluster_key": "LL:34.0:-118.2",
                        "canonical_address": "Tiny Lot",
                        "fatalities": 0,
                        "crashes": 1,
                        "basic_alert_carriers": 0,
                        "power_units": 1,
                        "phy_carriers": 1,
                        "spoil_rate": FAT_PER_CRASH,
                        "spoil_in": FAT_PER_CRASH,
                    }
                ],
                kind="rate",
            )
            rate_headers = [c["header"] for c in rate_payload["columns"]]
            self.assertEqual(rate_headers.count("Yard/PU"), 1)
            self.assertEqual(rate_headers[0], "● Rank")
            self.assertIn("Apple/PU", rate_headers)
            self.assertEqual(rate_payload["rows"][0]["rank"], 1)
            self.assertIn(TERMS["worst_fat"], detail)
            self.assertIn('class="term"', stacks)
            self.assertIn('class="term"', index)
            self.assertIn("SMS window", index)
            self.assertIn('class="term"', detail)
            self.assertIn(TERMS["phy"], detail)
            payload = build_index_table(
                [
                    {
                        "cluster_key": "LL:1:1",
                        "canonical_address": "Yard",
                        "extra_identities": 3,
                        "worst_fatalities": 6,
                        "stack_score": 200306,
                        "fat_dots": 2,
                        "phone_fatalities": 6,
                        "fatalities": 6,
                        "crashes": 1,
                        "oos_inspections": 0,
                        "phy_carriers": 4,
                    }
                ],
                kind="stack",
            )
            titles = {c["id"]: c.get("title") for c in payload["columns"]}
            self.assertEqual(titles["extra"], TERMS["extra_quiet"])
            self.assertEqual(titles["fat_dots"], TERMS["fat_dots"])
            self.assertEqual(titles["rank"], TERMS["rank_score"])
            self.assertEqual(titles["mill_score"], TERMS["mill_score"])
            self.assertEqual(
                payload["initialSort"],
                [
                    {"id": "mill_score", "desc": True},
                    {"id": "fat_dots", "desc": True},
                    {"id": "crashes", "desc": True},
                ],
            )
            self.assertIn('title="', term("OOS insp", "oos"))

    def test_shared_carrier_table_has_sc_and_sms(self):
        tables = shared_detail_tables(
            phone_groups=[
                (
                    "555-0100",
                    [
                        {
                            "dot_number": "111",
                            "legal_name": "Fatal Co",
                            "status_code": "A",
                            "power_units": 4,
                            "phone": "555-0100",
                            "canonical_address": "100 Yard Rd",
                            "fatalities": 2,
                            "crashes": 1,
                            "oos_inspections": 0,
                            "basic_alert": True,
                            "basic_flags": 1,
                            "basic_maint": True,
                        }
                    ],
                )
            ]
        )
        carriers = next(t for t in tables if t["rootId"] == "ts-carriers")
        self.assertIn("searchcarriers.com", carriers["rows"][0]["dot_html"])
        self.assertIn("motus.dot.gov", carriers["rows"][0]["dot_html"])
        ids = [c["id"] for c in carriers["columns"]]
        self.assertIn("fatalities", ids)
        self.assertIn("power_units", ids)
        self.assertIn("Maint", carriers["rows"][0]["basic"])
        self.assertIn("Vehicle maintenance", carriers["rows"][0]["basic_html"])
        self.assertIn("Universe weight", carriers["rows"][0]["basic_html"])
        self.assertAlmostEqual(carriers["rows"][0]["basic_w"], FAT_PER_MAINT, places=1)


if __name__ == "__main__":
    unittest.main()
