#!/usr/bin/env python3
"""Smoke: PPP COPY SQL parses a FOIA-shaped row; Address.py builds borrower rows."""

from pathlib import Path

import duckdb

from models.ppp_loan import PppLoan
from ppp_processor import (
    ADDRESS_CSV_COLS,
    COPY_SELECT,
    PPP_TABLE_SQL,
    _address_csv_row,
)

SAMPLE = """LoanNumber,DateApproved,SBAOfficeCode,ProcessingMethod,BorrowerName,BorrowerAddress,BorrowerCity,BorrowerState,BorrowerZip,LoanStatusDate,LoanStatus,Term,SBAGuarantyPercentage,InitialApprovalAmount,CurrentApprovalAmount,UndisbursedAmount,FranchiseName,ServicingLenderLocationID,ServicingLenderName,ServicingLenderAddress,ServicingLenderCity,ServicingLenderState,ServicingLenderZip,RuralUrbanIndicator,HubzoneIndicator,LMIIndicator,BusinessAgeDescription,ProjectCity,ProjectCountyName,ProjectState,ProjectZip,CD,JobsReported,NAICSCode,Race,Ethnicity,UTILITIES_PROCEED,PAYROLL_PROCEED,MORTGAGE_INTEREST_PROCEED,RENT_PROCEED,REFINANCE_EIDL_PROCEED,HEALTH_CARE_PROCEED,DEBT_INTEREST_PROCEED,BusinessType,OriginatingLenderLocationID,OriginatingLender,OriginatingLenderCity,OriginatingLenderState,Gender,Veteran,NonProfit,ForgivenessAmount,ForgivenessDate
9547507704,05/01/2020,0464,PPP,"SUMTER COATINGS, INC.",2410 Highway 15 South,Sumter,SC,29150-9662,12/18/2020,Paid in Full,24,100,769358.78,769358.78,0,,19248,Synovus Bank,1148 Broadway,COLUMBUS,GA,31901-2429,U,N,N,Existing or more than 2 years old,Sumter,SUMTER,SC,29150-9662,SC-05,62,325510,Unanswered,Unknown/NotStated,,769358.78,,,,,,Corporation,19248,Synovus Bank,COLUMBUS,GA,Unanswered,Unanswered,Y,773553.37,11/20/2020
"""


def test_copy_select_parses_foia_row(tmp_path: Path):
    csv_path = tmp_path / "public_150k_plus_smoke.csv"
    csv_path.write_text(SAMPLE, encoding="utf-8")
    con = duckdb.connect(":memory:")
    con.execute(PPP_TABLE_SQL)
    con.execute(
        f"""
        INSERT INTO ppp_loans (
            id, loan_number, date_approved, processing_method,
            borrower_name, borrower_address, borrower_city,
            borrower_state, borrower_zip, loan_status,
            current_approval_amount, jobs_reported, naics_code,
            business_type, nonprofit, forgiveness_amount,
            forgiveness_date, originating_lender,
            servicing_lender_name, rural_urban
        )
        {COPY_SELECT}
        """,
        [str(csv_path)],
    )
    row = con.execute(
        "SELECT loan_number, borrower_name, nonprofit, current_approval_amount "
        "FROM ppp_loans"
    ).fetchone()
    assert row[0] == "9547507704"
    assert "SUMTER" in row[1]
    assert row[2] is True
    assert abs(row[3] - 769358.78) < 0.01


def test_build_address_uses_address_py():
    loan = PppLoan(
        id="01a0dee0-28d4-7085-b116-16f25fc8940c",
        loan_number="9547507704",
        borrower_name="SUMTER COATINGS &AMP; INC.",
        borrower_address="2410 Highway 15 South",
        borrower_city="Sumter",
        borrower_state="SC",
        borrower_zip="29150-9662",
    )
    addr = loan.build_address()
    assert addr is not None
    assert addr.address_type == "ppp_borrower"
    assert addr.owner_id == loan.id
    assert "&AMP;" not in addr.name
    assert "&" in addr.name
    assert addr.zip_code == "29150"
    assert addr.zip4 == "9662"
    assert addr.po_box is None
    assert addr.canonical_address
    assert "29150" in addr.canonical_address


def test_build_address_po_box_colocator():
    loan = PppLoan(
        id="01a0dee0-28d4-7085-b116-16f25fc8940d",
        loan_number="1",
        borrower_name="BOX ORG",
        borrower_address="PO BOX 10267",
        borrower_city="West Palm Beach",
        borrower_state="FL",
        borrower_zip="33419",
    )
    addr = loan.build_address()
    assert addr is not None
    assert addr.po_box == "10267"
    assert addr.colocator == "PO:10267:33419"


def test_build_address_expands_suffix():
    loan = PppLoan(
        id="01a0dee0-28d4-7085-b116-16f25fc8940e",
        loan_number="1",
        borrower_name="FBC",
        borrower_address="6826 S TAMIAMI TRL",
        borrower_city="SARASOTA",
        borrower_state="FL",
        borrower_zip="34231",
    )
    addr = loan.build_address()
    assert addr is not None
    assert addr.address_line1.endswith("Trail")


def test_address_csv_row_roundtrip(tmp_path: Path):
    import csv

    loan = PppLoan(
        id="01a0dee0-28d4-7085-b116-16f25fc8940d",
        loan_number="1",
        borrower_name="BOX ORG",
        borrower_address="PO BOX 10267",
        borrower_city="West Palm Beach",
        borrower_state="FL",
        borrower_zip="33419",
    )
    addr = loan.build_address()
    assert addr is not None
    dest = tmp_path / "ppp_borrower_addresses.csv"
    with dest.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(ADDRESS_CSV_COLS))
        writer.writeheader()
        writer.writerow(_address_csv_row(addr))
    con = duckdb.connect(":memory:")
    row = con.execute(
        """
        SELECT po_box, colocator, zip_code, address_type
        FROM read_csv(?, header=true, all_varchar=true)
        """,
        [str(dest)],
    ).fetchone()
    assert row[0] == "10267"
    assert row[1] == "PO:10267:33419"
    assert row[2] == "33419"
    assert row[3] == "ppp_borrower"
