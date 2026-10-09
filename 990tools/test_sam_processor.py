#!/usr/bin/env python3
"""Smoke: SAM Public V2 DAT parse, Address.py phy/mail rows, slim COPY."""

from pathlib import Path
import zipfile

import duckdb

from models.sam_entity import SamEntity
from sam_processor import (
    ADDRESS_CSV_COLS,
    COPY_SELECT,
    SAM_TABLE_SQL,
    _address_csv_row,
    dat_to_slim_csv,
    is_header_or_trailer,
    parse_entity_row,
    pick_latest_monthly_utf8,
    split_dat_line,
    unzip_until_dat,
)


def _dat_line(**overrides) -> str:
    fields = [""] * 46
    fields[0] = "RV56IG5JM6G9"
    fields[3] = "1ABC2"
    fields[5] = "A"
    fields[6] = "Z2"
    fields[7] = "20200115"
    fields[8] = "20270115"
    fields[9] = "20261001"
    fields[10] = "20200120"
    fields[11] = "ACME RESEARCH &AMP; LABS"
    fields[12] = "ACME"
    fields[15] = "2410 HIGHWAY 15 SOUTH"
    fields[16] = "STE 100"
    fields[17] = "SUMTER"
    fields[18] = "SC"
    fields[19] = "29150"
    fields[20] = "9662"
    fields[21] = "USA"
    fields[26] = "https://example.test"
    fields[31] = "2X~A8"
    fields[32] = "541715"
    fields[39] = "PO BOX 10267"
    fields[40] = ""
    fields[41] = "WEST PALM BEACH"
    fields[42] = "33419"
    fields[43] = ""
    fields[44] = "USA"
    fields[45] = "FL"
    for key, value in overrides.items():
        idx = {
            "uei": 0,
            "cage": 3,
            "extract": 5,
            "legal": 11,
            "phy_l1": 15,
            "phy_l2": 16,
            "phy_city": 17,
            "phy_state": 18,
            "phy_zip": 19,
            "phy_zip4": 20,
            "phy_cc": 21,
            "mail_l1": 39,
            "mail_l2": 40,
            "mail_city": 41,
            "mail_zip": 42,
            "mail_zip4": 43,
            "mail_cc": 44,
            "mail_state": 45,
            "naics": 32,
        }[key]
        fields[idx] = value
    return "|".join(fields) + "|!end"


def test_skips_bof_eof():
    assert is_header_or_trailer(
        "BOF PUBLIC 20150911 20150911 0003861 0005253"
    )
    assert is_header_or_trailer(
        "EOF PUBLIC 20150911 20150911 0003861 0005253"
    )
    assert not is_header_or_trailer(_dat_line())


def test_parse_entity_row_mail_state_is_col_46():
    row = parse_entity_row(split_dat_line(_dat_line()))
    assert row is not None
    assert row["uei"] == "RV56IG5JM6G9"
    assert row["cage"] == "1ABC2"
    assert row["legal_name"] == "ACME RESEARCH &AMP; LABS"
    assert row["phy_state"] == "SC"
    assert row["phy_zip"] == "29150"
    assert row["phy_zip4"] == "9662"
    assert row["mail_city"] == "WEST PALM BEACH"
    assert row["mail_zip"] == "33419"
    assert row["mail_state"] == "FL"
    assert row["primary_naics"] == "541715"


def test_parse_skips_short_and_header_uei():
    assert parse_entity_row(["BOF", "x"]) is None
    assert parse_entity_row(["ueiSAM"] + [""] * 50) is None


def test_pick_latest_monthly_utf8():
    names = [
        "SAM_PUBLIC_UTF-8_DAILY_V2_20261004.ZIP",
        "SAM_PUBLIC_UTF-8_MONTHLY_V2_20260907.ZIP",
        "SAM_PUBLIC_UTF-8_MONTHLY_V2_20261005.ZIP",
        "SAM_PUBLIC_V2_MONTHLY_20261005.ZIP",
    ]
    assert pick_latest_monthly_utf8(names) == (
        "SAM_PUBLIC_UTF-8_MONTHLY_V2_20261005.ZIP"
    )


def test_build_phy_and_mail_addresses():
    row = parse_entity_row(split_dat_line(_dat_line()))
    assert row is not None
    entity = SamEntity(
        id="01a0dee0-28d4-7085-b116-16f25fc8940c",
        **{k: row[k] for k in row if k in SamEntity.__dataclass_fields__},
    )
    addrs = entity.build_addresses()
    types = {a.address_type: a for a in addrs}
    assert set(types) == {"sam_entity_phy", "sam_entity_mail"}
    phy = types["sam_entity_phy"]
    assert phy.owner_id == entity.id
    assert phy.ein == ""
    assert "&AMP;" not in phy.name
    assert "&" in phy.name
    assert phy.zip_code == "29150"
    assert phy.zip4 == "9662"
    assert phy.address_line1.endswith("South")
    mail = types["sam_entity_mail"]
    assert mail.po_box == "10267"
    assert mail.colocator == "PO:10267:33419"
    assert mail.state == "FL"


def test_foreign_phy_gets_fa_colocator():
    entity = SamEntity(
        id="01a0dee0-28d4-7085-b116-16f25fc8940d",
        uei="ABCD12345678",
        legal_name="MAPLE LABS",
        phy_line1="100 QUEEN ST",
        phy_city="OTTAWA",
        phy_state="ON",
        phy_zip="K1P 5M7",
        phy_country="CAN",
    )
    addrs = entity.build_addresses()
    assert len(addrs) == 1
    assert addrs[0].address_type == "sam_entity_phy"
    assert addrs[0].colocator == "FA:CAN"
    assert addrs[0].canonical_address


def test_empty_address_skipped():
    entity = SamEntity(
        id="01a0dee0-28d4-7085-b116-16f25fc8940e",
        uei="ABCD12345678",
        legal_name="NO ADDR INC",
    )
    assert entity.build_addresses() == []


def test_address_csv_row_roundtrip(tmp_path: Path):
    import csv

    entity = SamEntity(
        id="01a0dee0-28d4-7085-b116-16f25fc8940d",
        uei="RV56IG5JM6G9",
        legal_name="BOX ORG",
        mail_line1="PO BOX 10267",
        mail_city="West Palm Beach",
        mail_state="FL",
        mail_zip="33419",
        mail_country="USA",
    )
    addr = entity.build_addresses()[0]
    dest = tmp_path / "sam_entity_addresses.csv"
    with dest.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(ADDRESS_CSV_COLS))
        writer.writeheader()
        writer.writerow(_address_csv_row(addr))
    con = duckdb.connect(":memory:")
    row = con.execute(
        """
        SELECT po_box, colocator, zip_code, address_type, ein
        FROM read_csv(?, header=true, all_varchar=true)
        """,
        [str(dest)],
    ).fetchone()
    assert row[0] == "10267"
    assert row[1] == "PO:10267:33419"
    assert row[2] == "33419"
    assert row[3] == "sam_entity_mail"
    assert row[4] in ("", None)


def test_slim_csv_and_copy_select(tmp_path: Path):
    dat = tmp_path / "SAM_PUBLIC.dat"
    dat.write_text(
        "BOF PUBLIC 20261005 20261005 0000002 0000001\n"
        + _dat_line()
        + "\n"
        + _dat_line(uei="ZZZZZZZZZZZZ", legal="OTHER CO", mail_state="MD")
        + "\n"
        + "EOF PUBLIC 20261005 20261005 0000002 0000001\n",
        encoding="utf-8",
    )
    slim = tmp_path / "sam_entities_slim.csv"
    n = dat_to_slim_csv(dat, slim)
    assert n == 2
    con = duckdb.connect(":memory:")
    con.execute(SAM_TABLE_SQL)
    con.execute(
        f"INSERT INTO sam_entities ({_insert_cols()}) {COPY_SELECT}",
        [str(slim)],
    )
    rows = con.execute(
        "SELECT uei, legal_name, mail_state, registration_date, primary_naics "
        "FROM sam_entities ORDER BY uei"
    ).fetchall()
    assert len(rows) == 2
    acme = [r for r in rows if r[0] == "RV56IG5JM6G9"][0]
    assert "ACME" in acme[1]
    assert acme[2] == "FL"
    assert str(acme[3]) == "2020-01-15"
    assert acme[4] == "541715"
    other = [r for r in rows if r[0] == "ZZZZZZZZZZZZ"][0]
    assert other[2] == "MD"


def test_unzip_nested_zip_finds_dat(tmp_path: Path):
    inner_dir = tmp_path / "pack"
    inner_dir.mkdir()
    dat = inner_dir / "SAM_PUBLIC_UTF-8_MONTHLY_V2.dat"
    dat.write_text(_dat_line() + "\n", encoding="utf-8")
    inner_zip = tmp_path / "inner.zip"
    with zipfile.ZipFile(inner_zip, "w") as zf:
        zf.write(dat, arcname=dat.name)
    outer_zip = tmp_path / "SAM_PUBLIC_UTF-8_MONTHLY_V2_20261005.ZIP"
    with zipfile.ZipFile(outer_zip, "w") as zf:
        zf.write(inner_zip, arcname="inner.zip")
    extracted = unzip_until_dat(outer_zip, tmp_path / "extracted")
    assert extracted.suffix.lower() == ".dat"
    assert "RV56IG5JM6G9" in extracted.read_text(encoding="utf-8")


def _insert_cols() -> str:
    return """
id, uei, cage, extract_code, purpose,
registration_date, expiration_date, last_update_date, activation_date,
legal_name, dba_name,
phy_line1, phy_line2, phy_city, phy_state, phy_zip, phy_zip4, phy_country,
mail_line1, mail_line2, mail_city, mail_state, mail_zip, mail_zip4, mail_country,
entity_url, business_types, primary_naics
"""
