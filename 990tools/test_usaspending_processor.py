#!/usr/bin/env python3
"""Smoke: slim SELECT maps archive-shaped contract and assistance headers."""

from pathlib import Path

import duckdb

from usaspending_processor import (
    ACTIONS_TABLE_SQL,
    BY_UEI_SQL,
    clamp_years,
    csv_headers,
    parse_zip_name,
    resolve_stamp,
    select_sql,
    stamp_from_disk,
    year_specs,
)

CONTRACT_SAMPLE = """contract_award_unique_key,award_id_piid,recipient_uei,recipient_name,federal_action_obligation,total_dollars_obligated,action_date,awarding_agency_name,naics_code,recipient_city_name,recipient_state_code,recipient_zip_5_code,primary_place_of_performance_city_name,primary_place_of_performance_state_code,primary_place_of_performance_zip_5_code
CONT_AWD_1,N00019,ABC123DEF456,ACME LLC,1000.5,1000.5,2024-11-01,Department of Defense,336411,Grainfield,KS,67737,Quinter,KS,67752
"""

ASSIST_SAMPLE = """assistance_award_unique_key,award_id_fain,recipient_uei,recipient_name,federal_action_obligation,total_obligated_amount,action_date,awarding_agency_name,cfda_number,recipient_city_name,recipient_state_code,recipient_zip_4_code,primary_place_of_performance_city_name,primary_place_of_performance_state_code,primary_place_of_performance_zip_4
ASST_1,GRANT-9,ABC123DEF456,ACME LLC,250.0,250.0,2025-01-15,Department of Agriculture,10.001,Grainfield,KS,67737-6020,Grainfield,KS,67737-6020
"""


def _load(con, csv_path: Path, family: str, fy: int) -> None:
    headers = csv_headers(csv_path)
    select = select_sql(headers, family, fy)
    con.execute(
        f"""
        INSERT INTO usaspending_actions
        SELECT {select}
        FROM read_csv(?, header=true, ignore_errors=true, auto_detect=true, all_varchar=true)
        """,
        [str(csv_path)],
    )


def test_slim_copy_contract_and_assistance(tmp_path: Path):
    cpath = tmp_path / "contracts.csv"
    apath = tmp_path / "assist.csv"
    cpath.write_text(CONTRACT_SAMPLE, encoding="utf-8")
    apath.write_text(ASSIST_SAMPLE, encoding="utf-8")
    con = duckdb.connect(":memory:")
    con.execute(ACTIONS_TABLE_SQL)
    _load(con, cpath, "contract", 2025)
    _load(con, apath, "assistance", 2025)
    rows = con.execute(
        "SELECT award_family, award_id, recipient_uei, obligation, program_code, recipient_zip, pop_zip "
        "FROM usaspending_actions ORDER BY award_family"
    ).fetchall()
    assert len(rows) == 2
    assist, contract = rows
    assert assist[0] == "assistance"
    assert assist[1] == "GRANT-9"
    assert assist[2] == "ABC123DEF456"
    assert abs(assist[3] - 250.0) < 0.01
    assert assist[4] == "10.001"
    assert assist[5] == "67737"
    assert contract[0] == "contract"
    assert contract[1] == "N00019"
    assert abs(contract[3] - 1000.5) < 0.01
    assert contract[4] == "336411"
    con.execute(BY_UEI_SQL)
    uei = con.execute("SELECT uei, obligation, award_n FROM usaspending_by_uei").fetchone()
    assert uei[0] == "ABC123DEF456"
    assert abs(uei[1] - 1250.5) < 0.01
    assert uei[2] == 2


def test_clamp_years_uei_floor():
    assert clamp_years(2017, 2030) == (2022, 2030)
    assert clamp_years(2022, 2027) == (2022, 2027)
    assert clamp_years(2025, 2025) == (2025, 2025)


def test_year_specs_990_window():
    specs = year_specs(2017, 2018, "20261006")
    names = [n for _, _, n in specs]
    assert names == [
        "FY2017_All_Contracts_Full_20261006.zip",
        "FY2017_All_Assistance_Full_20261006.zip",
        "FY2018_All_Contracts_Full_20261006.zip",
        "FY2018_All_Assistance_Full_20261006.zip",
    ]
    assert parse_zip_name(names[0]) == ("contract", 2017)
    assert parse_zip_name(names[1]) == ("assistance", 2017)
    # Archive floor is FY2008; 990 start-year 2000 still starts there.
    early = year_specs(2000, 2008, "20261006")
    assert early[0][1] == 2008
    assert year_specs(2028, 2027, "20261006") == []


def test_stamp_from_disk_picks_newest(tmp_path: Path):
    (tmp_path / "FY2025_All_Contracts_Full_20260915.zip").write_bytes(b"x")
    (tmp_path / "FY2017_All_Assistance_Full_20261006.zip").write_bytes(b"x")
    assert stamp_from_disk(tmp_path) == "20261006"
    assert resolve_stamp(tmp_path, stamp="20251115") == "20251115"
