#!/usr/bin/env python3
"""PPP leftover grouping + geo nav depth."""

import json
import sys
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "spending_reporting"))
sys.path.insert(0, str(ROOT / "dot_reporting"))

from generate_galleries import write_index  # noqa: E402
from generate_geo_reports import (  # noqa: E402
    LEFTOVER_PPP_SUITES,
    PPP_CUTS,
    by_state_dirname,
    cluster_index_page,
    detail_page,
    federal_extra_heatmaps,
    fetch_clusters,
    tanstack_assets_html,
    want_suite,
    write_by_state_index,
    write_hub,
)
from generate_galleries import leftover_gallery_payload, page as gallery_page  # noqa: E402
from state_weights import (  # noqa: E402
    ELECTORAL_VOTES_2024,
    POP_2024,
    attach_federal_weights,
)


def _tiny_ppp(con) -> None:
    con.execute(
        """
        CREATE TABLE ppp_loans (
            id VARCHAR,
            current_approval_amount DOUBLE,
            forgiveness_amount DOUBLE,
            loan_status VARCHAR,
            jobs_reported INTEGER
        );
        CREATE TABLE Addresses (
            owner_id VARCHAR,
            address_type VARCHAR,
            colocator VARCHAR,
            state VARCHAR,
            zip_code VARCHAR,
            latitude DOUBLE,
            longitude DOUBLE,
            geocoding_id VARCHAR,
            canonical_address VARCHAR
        );
        CREATE TABLE Geocoding (
            geocoding_id VARCHAR, latitude DOUBLE, longitude DOUBLE
        );
        """
    )
    con.execute(
        """
        INSERT INTO ppp_loans VALUES
          ('a', 150000, 50000, 'Charged Off', 10),
          ('b', 200000, 200000, 'Paid in Full', 5),
          ('c', 90000, 90000, 'Paid in Full', 0);
        INSERT INTO Addresses VALUES
          ('a', 'ppp_borrower', 'LL:40:-74', 'NY', '10001', 40.7, -74.0, NULL, '1 MAIN ST NEW YORK NY 10001'),
          ('b', 'ppp_borrower', 'LL:40:-74', 'NY', '10001', 40.7, -74.0, NULL, '1 MAIN ST NEW YORK NY 10001'),
          ('c', 'ppp_borrower', 'LL:41:-73', 'CT', '06880', 41.1, -73.4, NULL, '9 PINE ST WESTPORT CT 06880');
        """
    )


def test_leftover_groupby_drops_fully_forgiven_cluster():
    con = duckdb.connect(":memory:")
    _tiny_ppp(con)
    rows = fetch_clusters(con, "ppp", "colocator", limit=10)
    assert len(rows) == 1
    assert rows[0]["cluster_key"] == "LL:40:-74"
    assert rows[0]["loan_n"] == 2
    assert rows[0]["leftover"] == 100000
    assert rows[0]["rank_metric_value"] == 100000


def test_address_slice_groups_by_canonical():
    con = duckdb.connect(":memory:")
    _tiny_ppp(con)
    con.execute(
        """
        INSERT INTO ppp_loans VALUES ('d', 80000, 0, 'Charged Off', 2);
        INSERT INTO Addresses VALUES
          ('d', 'ppp_borrower', 'LL:40:-74', 'NY', '10001', 40.7, -74.0, NULL,
           '9 OTHER ST NEW YORK NY 10001');
        """
    )
    coloc = fetch_clusters(con, "ppp", "colocator", limit=10)
    addrs = fetch_clusters(con, "ppp", "address", limit=10)
    assert len(coloc) == 1
    assert coloc[0]["loan_n"] == 3
    assert {r["cluster_key"] for r in addrs} == {
        "1 MAIN ST NEW YORK NY 10001",
        "9 OTHER ST NEW YORK NY 10001",
    }


def test_by_state_dirname_keeps_colocator_short_name():
    assert by_state_dirname("ppp", "colocator") == "ppp_by_state"
    assert by_state_dirname("ppp", "address") == "ppp_address_by_state"
    assert by_state_dirname("federal", "zipcode") == "federal_zipcode_by_state"
    assert by_state_dirname("federal", "loose_colocator") == "federal_loose_colocator_by_state"
    assert by_state_dirname("ppp_charged_off", "colocator") == "ppp_charged_off_by_state"
    assert by_state_dirname("ppp_forgiven", "zipcode") == "ppp_forgiven_zipcode_by_state"


def test_want_suite_ppp_is_leftover_only():
    assert want_suite("ppp_address", {"ppp"})
    assert want_suite("ppp_by_state", {"ppp"})
    assert "ppp_charged_off_address" not in LEFTOVER_PPP_SUITES
    assert not want_suite("ppp_charged_off_address", {"ppp"})
    assert not want_suite("ppp_forgiven_colocator", {"ppp"})
    assert want_suite("ppp_charged_off_address", {"ppp_charged_off"})
    assert want_suite("ppp_charged_off_by_state", {"ppp_charged_off"})
    assert not want_suite("ppp_forgiven_address", {"ppp_charged_off"})
    assert want_suite("federal_zipcode", {"federal"})
    assert not want_suite("ppp_address", {"federal"})


def test_charged_off_cut_is_status_only():
    con = duckdb.connect(":memory:")
    _tiny_ppp(con)
    rows = fetch_clusters(con, "ppp_charged_off", "colocator", limit=10)
    assert len(rows) == 1
    assert rows[0]["loan_n"] == 1
    assert rows[0]["leftover"] == 100000
    assert rows[0]["charged_n"] == 1


def test_forgiven_cut_keeps_leftover_zero():
    con = duckdb.connect(":memory:")
    _tiny_ppp(con)
    con.execute(
        """
        INSERT INTO ppp_loans VALUES
          ('e', 180000, 180000, 'Paid in Full', 3);
        INSERT INTO Addresses VALUES
          ('e', 'ppp_borrower', 'LL:39:-77', 'DC', '20001', 38.9, -77.0, NULL,
           '100 K ST WASHINGTON DC 20001');
        """
    )
    leftover = fetch_clusters(con, "ppp", "colocator", limit=10)
    forgiven = fetch_clusters(con, "ppp_forgiven", "colocator", limit=10)
    leftover_keys = {r["cluster_key"] for r in leftover}
    forgiven_keys = {r["cluster_key"] for r in forgiven}
    assert "LL:39:-77" not in leftover_keys
    assert "LL:39:-77" in forgiven_keys
    full = next(r for r in forgiven if r["cluster_key"] == "LL:39:-77")
    assert full["leftover"] == 0
    assert full["forgiven"] == 180000
    assert abs(full["pct"] - 1.0) < 1e-9


def test_zero_jobs_cut_ranks_approved():
    con = duckdb.connect(":memory:")
    _tiny_ppp(con)
    rows = fetch_clusters(con, "ppp_zero_jobs", "colocator", limit=10)
    assert len(rows) == 1
    assert rows[0]["cluster_key"] == "LL:41:-73"
    assert rows[0]["approved"] == 90000
    assert rows[0]["jobs_n"] == 0


def test_payroll_cut_ranks_per_job():
    con = duckdb.connect(":memory:")
    _tiny_ppp(con)
    rows = fetch_clusters(con, "ppp_payroll", "colocator", limit=10)
    assert len(rows) == 1
    assert rows[0]["cluster_key"] == "LL:40:-74"
    # a 15k/job + b 40k/job share the pin → 350k / 15 jobs
    assert abs(rows[0]["per_job"] - (350000 / 15)) < 1e-6


def test_five_ppp_cuts_exist():
    assert tuple(PPP_CUTS) == (
        "ppp_charged_off",
        "ppp_not_yet_paid",
        "ppp_forgiven",
        "ppp_payroll",
        "ppp_zero_jobs",
    )


def test_cluster_index_uses_trucking_breadcrumbs(tmp_path):
    clusters = [
        {
            "cluster_key": "1 MAIN ST NEW YORK NY 10001",
            "sample_address": "1 MAIN ST",
            "state": "NY",
            "loan_n": 1,
            "leftover": 100000,
            "forgiven": 0,
            "approved": 100000,
            "pct": 0,
            "jobs_n": 1,
            "per_job": 100000,
            "charged_n": 1,
            "open_n": 0,
            "paid_n": 0,
            "latitude": 40.7,
            "longitude": -74.0,
        }
    ]
    dest = tmp_path / "ppp_charged_off_address"
    cluster_index_page("ppp_charged_off", "address", clusters, dest, depth=1)
    html = (dest / "index.html").read_text(encoding="utf-8")
    assert 'aria-label="Breadcrumb"' in html
    assert 'href="../master_index.html">All reports</a>' in html
    assert "PPP Charged Off · Address" in html
    assert ">Building<" not in html
    assert ">Area<" not in html
    assert "class=\"widen-nav\"" not in html


def test_galleries_index_points_at_fun(tmp_path):
    write_index(tmp_path, True, {"charged_off": 200})
    html = (tmp_path / "index.html").read_text(encoding="utf-8")
    assert "Spending galleries" in html
    assert "Fun with Geography" in html
    assert "charged_off.html" in html
    assert "federal_pins.html" in html
    assert ">Building<" not in html


def test_cluster_index_emits_tanstack(tmp_path):
    clusters = [
        {
            "cluster_key": "LL:40:-74",
            "sample_address": "1 MAIN ST",
            "state": "NY",
            "loan_n": 2,
            "leftover": 100000,
            "forgiven": 250000,
            "approved": 350000,
            "pct": 250000 / 350000,
            "jobs_n": 15,
            "per_job": 350000 / 15,
            "charged_n": 1,
            "open_n": 0,
            "paid_n": 1,
            "latitude": 40.7,
            "longitude": -74.0,
        }
    ]
    dest = tmp_path / "ppp_charged_off_colocator"
    cluster_index_page("ppp_charged_off", "colocator", clusters, dest, depth=1)
    html = (dest / "index.html").read_text(encoding="utf-8")
    assert 'id="ts-table-root"' in html
    assert "window.__CLUSTER_TABLE__" in html
    assert "PPP Charged Off" in html
    assert "mountTanStackTable" in html
    assert '"id": "per_job"' in html
    assert "Not yet paid" in html
    assert "<%text>" not in html
    assert "emitted literally" not in html


def test_tanstack_assets_omit_mako_wrapper():
    html = tanstack_assets_html()
    assert "<%text>" not in html
    assert "</%doc>" not in html
    assert "emitted literally" not in html
    assert "mountTanStackTable" in html
    assert html.lstrip().startswith("<style>")


def test_detail_page_emits_tanstack(tmp_path):
    cluster = {
        "cluster_key": "LL:40:-74",
        "sample_address": "1 MAIN ST",
        "state": "NY",
        "slug": "1-main-st",
        "leftover": 100000,
        "forgiven": 50000,
        "approved": 150000,
        "pct": 50000 / 150000,
        "jobs_n": 10,
        "per_job": 15000,
        "loan_n": 1,
        "charged_n": 1,
        "open_n": 0,
        "zip_code": "10001",
        "colocator": "LL:40.7:-74.0",
        "latitude": 40.7,
        "longitude": -74.0,
    }
    bundle = {
        "loans": [(
            "ACME", "NYC", "NY", "10001", 10,
            150000, 50000, 100000, "Charged Off", "Bank",
        )],
        "sam": [("Acme LLC", "ABC123", "1ABC", 1_000_000, 3)],
        "types": [("ppp_borrower", 2)],
        "charities": [("Food Bank", "12-3456789", 25000)],
    }
    dest = tmp_path / "ppp_charged_off_colocator"
    dest.mkdir()
    detail_page(
        "ppp_charged_off", "colocator", cluster, dest, bundle,
        depth=1, reports_dir=tmp_path, context="national",
    )
    html = (dest / "1-main-st.html").read_text(encoding="utf-8")
    assert "window.__TS_TABLES__" in html
    assert 'id="ts-loans"' in html
    assert 'id="ts-sam"' in html
    assert 'id="ts-types"' in html
    assert 'id="ts-charities"' in html
    assert "mountTanStackTable" in html
    assert "<%text>" not in html
    assert "emitted literally" not in html
    assert '"id": "leftover"' in html
    assert 'aria-label="Breadcrumb"' in html
    assert "PPP Charged Off · Colocator" in html
    assert 'class="widen-nav"' in html
    assert "Loose colocator (0.5° cell)" in html
    assert "ZIP 10001" in html
    assert "State NY" in html
    assert ">Building<" not in html


def test_address_detail_widens_to_colocator_zip_state(tmp_path):
    from generate_address_reports import slugify_address

    cluster = {
        "cluster_key": "1 MAIN ST NEW YORK NY 10001",
        "sample_address": "1 MAIN ST NEW YORK NY 10001",
        "state": "NY",
        "slug": "1-main-st-new-york-ny-10001",
        "leftover": 100000,
        "forgiven": 0,
        "approved": 100000,
        "pct": 0,
        "jobs_n": 10,
        "per_job": 10000,
        "loan_n": 1,
        "charged_n": 1,
        "open_n": 0,
        "zip_code": "10001",
        "colocator": "LL:40.7:-74.0",
        "latitude": 40.7,
        "longitude": -74.0,
    }
    dest = tmp_path / "ppp_charged_off_address"
    dest.mkdir()
    coloc = tmp_path / "ppp_charged_off_colocator"
    coloc.mkdir()
    (coloc / f"{slugify_address('LL:40.7:-74.0')}.html").write_text("x", encoding="utf-8")
    (coloc / "index.html").write_text("idx", encoding="utf-8")
    zdir = tmp_path / "ppp_charged_off_zipcode"
    zdir.mkdir()
    (zdir / f"{slugify_address('10001')}.html").write_text("z", encoding="utf-8")
    loose_st = tmp_path / "ppp_charged_off_loose_colocator_by_state" / "states" / "NY"
    loose_st.mkdir(parents=True)
    (loose_st / "index.html").write_text("ny", encoding="utf-8")
    detail_page(
        "ppp_charged_off", "address", cluster, dest, {},
        depth=1, reports_dir=tmp_path, context="national",
    )
    html = (dest / "1-main-st-new-york-ny-10001.html").read_text(encoding="utf-8")
    assert "Widen:" in html
    assert "Colocator (all address variants)" in html
    assert "Loose colocator (0.5° cell)" in html
    assert "ZIP 10001" in html
    assert "State NY" in html
    assert "PPP Charged Off · Address" in html
    assert ">Building<" not in html
    assert 'class="widen-link missing"' not in html
    assert f"{slugify_address('LL:40.7:-74.0')}.html" in html


def test_by_state_index_emits_tanstack(tmp_path):
    stats = [
        {
            "state": "CA",
            "leftover": 1_000_000_000,
            "pass_clusters": 12,
            "show_n": 4,
        },
        {
            "state": "NY",
            "leftover": 500_000_000,
            "pass_clusters": 8,
            "show_n": 0,
        },
    ]
    dest = tmp_path / "ppp_charged_off_by_state"
    write_by_state_index(dest, "ppp_charged_off", "colocator", stats)
    html = (dest / "index.html").read_text(encoding="utf-8")
    assert "window.__CLUSTER_TABLE__" in html
    assert 'id="ts-table-root"' in html
    assert "mountTanStackTable" in html
    assert "<%text>" not in html
    assert '"id": "metric"' in html
    assert "Open →" in html
    assert 'aria-label="Breadcrumb"' in html
    assert "PPP Charged Off · Colocator · by state" in html
    assert ">Building<" not in html


def test_gallery_page_emits_tanstack(tmp_path):
    records = [
        ("ACME", "NYC", "NY", "10001", 10, 150000, 0, 150000, "Bank", 40.7, -74.0),
    ]
    out = tmp_path / "charged_off.html"
    gallery_page(
        "PPP charged off",
        "test",
        ["Borrower", "City", "St", "ZIP", "Jobs", "Approved", "Forgiven", "Leftover", "Lender"],
        [["ACME", "NYC", "NY", "10001", "10", "$150,000", "$0", "$150,000", "Bank"]],
        out,
        numeric={4, 5, 6, 7},
        payload=leftover_gallery_payload(records),
    )
    html = out.read_text(encoding="utf-8")
    assert "window.__CLUSTER_TABLE__" in html
    assert 'id="ts-table-root"' in html
    assert "mountTanStackTable" in html
    assert "<%text>" not in html
    assert '"id": "leftover"' in html


def test_write_hub_lists_suites_without_touching_parent(tmp_path):
    parent = tmp_path / "index.html"
    parent.write_text("GALLERIES SENTINEL", encoding="utf-8")
    geo = tmp_path / "geo"
    (geo / "ppp_colocator").mkdir(parents=True)
    (geo / "ppp_colocator" / "index.html").write_text("x", encoding="utf-8")
    write_hub(geo, {"ppp_colocator": 5, "ppp_by_state": "ok"})
    assert parent.read_text(encoding="utf-8") == "GALLERIES SENTINEL"
    hub = (geo / "index.html").read_text(encoding="utf-8")
    assert "ppp_colocator/index.html" in hub
    assert "Fun with Geography" in hub


def test_electoral_college_is_538():
    assert sum(ELECTORAL_VOTES_2024.values()) == 538
    assert ELECTORAL_VOTES_2024["DC"] == 3
    assert ELECTORAL_VOTES_2024["CA"] == 54
    assert ELECTORAL_VOTES_2024["TX"] == 40
    assert set(ELECTORAL_VOTES_2024) == set(POP_2024)
    assert POP_2024["CA"] == 39_431_263
    assert POP_2024["DC"] == 702_250


def test_attach_federal_weights_per_pop_and_ev():
    stats = [
        {"state": "DC", "dollars": 702_250},
        {"state": "WY", "dollars": 587_618},
        {"state": "XX", "dollars": 1_000_000},
    ]
    attach_federal_weights(stats)
    assert stats[0]["dollars_per_pop"] == 1.0
    assert stats[0]["dollars_per_ev"] == 702_250 / 3
    assert stats[1]["dollars_per_pop"] == 1.0
    assert stats[1]["electoral_votes"] == 3
    assert stats[2]["pop"] is None
    assert stats[2]["dollars_per_pop"] == 0.0
    assert stats[2]["dollars_per_ev"] == 0.0


def test_federal_extra_heatmaps_emit_pop_and_ev():
    stats = [
        {
            "state": "DC",
            "dollars": 3_000_000_000,
            "show_n": 10,
            "dollars_per_pop": 3_000_000_000 / 702_250,
            "dollars_per_ev": 1_000_000_000,
        },
        {
            "state": "CA",
            "dollars": 10_000_000_000,
            "show_n": 40,
            "dollars_per_pop": 10_000_000_000 / 39_431_263,
            "dollars_per_ev": 10_000_000_000 / 54,
        },
    ]
    html = federal_extra_heatmaps(stats, "colocator")
    assert "$ / pop" in html
    assert "$ / electoral vote" in html
    assert "Vintage 2024" in html
    assert "Electoral College" in html
    assert "SAM physical HQ" in html
    assert "states/DC/index.html" in html
    assert "states/CA/index.html" in html


def _overlap_cluster():
    return {
        "cluster_key": "1 MAIN ST NEW YORK NY 10001",
        "sample_address": "1 MAIN ST NEW YORK NY 10001",
        "state": "NY",
        "slug": "1-main-st-new-york-ny-10001",
        "leftover": 100000,
        "forgiven": 0,
        "approved": 100000,
        "pct": 0,
        "jobs_n": 10,
        "per_job": 10000,
        "loan_n": 1,
        "charged_n": 1,
        "open_n": 0,
        "zip_code": "10001",
        "colocator": "LL:40.7:-74.0",
        "latitude": 40.7,
        "longitude": -74.0,
    }


def test_bad_address_links_existing_fun_siblings(tmp_path):
    from generate_address_reports import slugify_address

    cluster = _overlap_cluster()
    dest = tmp_path / "ppp_charged_off_address"
    dest.mkdir()
    key = cluster["cluster_key"]
    med = tmp_path / "medicare_address_clusters"
    med.mkdir()
    med_fn = f"{slugify_address(f'medicare-address-{key}')}.html"
    (med / med_fn).write_text("medicare", encoding="utf-8")
    nyp = tmp_path / "ppp_not_yet_paid_address"
    nyp.mkdir()
    (nyp / f"{cluster['slug']}.html").write_text("nyp", encoding="utf-8")
    bundle = {
        "types": [
            ("nppes_mailing", 27),
            ("nppes_practice", 8),
            ("contractor", 2),
            ("ppp_borrower", 2),
        ],
        "loans": [],
        "sam": [],
        "charities": [],
    }
    detail_page(
        "ppp_charged_off", "address", cluster, dest, bundle,
        depth=1, reports_dir=tmp_path, context="national",
        search_roots=[tmp_path],
    )
    html = (dest / f"{cluster['slug']}.html").read_text(encoding="utf-8")
    assert 'class="bad-address"' in html
    assert "Medicare / NPPES" in html
    assert med_fn in html
    assert "PPP Not yet paid" in html
    assert "990 contractors" in html or "Contractors" in html
    assert "no detail page in this suite" in html
    assert "type_html" in html
    sidecar = json.loads(
        (dest / "data" / f"{cluster['slug']}.json").read_text(encoding="utf-8")
    )
    assert sidecar["types"][0][0] == "nppes_mailing"


def test_bad_address_skips_when_only_ppp_type(tmp_path):
    cluster = _overlap_cluster()
    dest = tmp_path / "ppp_charged_off_address"
    dest.mkdir()
    detail_page(
        "ppp_charged_off", "address", cluster, dest,
        {"types": [("ppp_borrower", 2)]},
        depth=1, reports_dir=tmp_path, context="national",
        search_roots=[tmp_path],
    )
    html = (dest / f"{cluster['slug']}.html").read_text(encoding="utf-8")
    assert 'class="bad-address"' not in html


def test_refresh_geo_overlap_inserts_section(tmp_path):
    from geo_overlap import refresh_geo_overlap
    from generate_address_reports import slugify_address

    cluster = _overlap_cluster()
    dest = tmp_path / "ppp_charged_off_address"
    dest.mkdir()
    detail_page(
        "ppp_charged_off", "address", cluster, dest,
        {"types": [("nppes_practice", 3)]},
        depth=1, reports_dir=tmp_path, context="national",
        search_roots=[tmp_path],
    )
    html_path = dest / f"{cluster['slug']}.html"
    before = html_path.read_text(encoding="utf-8")
    assert "no detail page in this suite" in before
    med = tmp_path / "medicare_address_clusters"
    med.mkdir()
    med_fn = f"{slugify_address(f'medicare-address-{cluster['cluster_key']}')}.html"
    (med / med_fn).write_text("medicare", encoding="utf-8")
    stats = refresh_geo_overlap(tmp_path, search_roots=[tmp_path])
    assert stats["updated"] >= 1
    after = html_path.read_text(encoding="utf-8")
    assert med_fn in after
    assert "no detail page in this suite" not in after
