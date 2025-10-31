#!/usr/bin/env python3
"""
xpaths.py - Unified XPath configuration for all IRS 990 form types

This module consolidates XPath expressions for Forms 990, 990EZ, and 990PF
into a single, unified configuration to reduce duplication and improve maintainability.
"""

from lxml import etree  # type: ignore

# Export tostring for convenience
tostring = etree.tostring

# Common namespaces used across all forms
NAMESPACES = {'irs': 'http://www.irs.gov/efile'}

# Common XPath patterns shared across forms
COMMON_XPATHS = {
    "form_type": [
        etree.XPath(".//irs:ReturnHeader/irs:ReturnTypeCd", namespaces=NAMESPACES),
        etree.XPath(".//ReturnHeader/ReturnTypeCd", namespaces=NAMESPACES),
    ],
    "tax_year": [
        etree.XPath(".//irs:ReturnHeader/irs:TaxYr", namespaces=NAMESPACES),
        etree.XPath(".//ReturnHeader/TaxYr", namespaces=NAMESPACES),
    ],
    "filer_ein": [
        etree.XPath(".//irs:Filer/irs:EIN", namespaces=NAMESPACES),
        etree.XPath(".//Filer/EIN", namespaces=NAMESPACES),
    ],
    "filer_name": [
        etree.XPath(".//irs:Filer/irs:BusinessName/irs:BusinessNameLine1Txt", namespaces=NAMESPACES),
        etree.XPath(".//Filer/BusinessName/BusinessNameLine1Txt", namespaces=NAMESPACES),
    ],
    "business_name_line1": [
        etree.XPath(".//irs:Filer/irs:BusinessName/irs:BusinessNameLine1Txt", namespaces=NAMESPACES),
        etree.XPath(".//Filer/BusinessName/BusinessNameLine1Txt", namespaces=NAMESPACES),
    ],
    "business_name_line2": [
        etree.XPath(".//irs:Filer/irs:BusinessName/irs:BusinessNameLine2Txt", namespaces=NAMESPACES),
        etree.XPath(".//Filer/BusinessName/BusinessNameLine2Txt", namespaces=NAMESPACES),
    ],
    "return_data": [
        etree.XPath(".//irs:ReturnData", namespaces=NAMESPACES),
        etree.XPath(".//ReturnData", namespaces=NAMESPACES),
    ],
    "address_line1": [
        etree.XPath(".//irs:Filer/irs:USAddress/irs:AddressLine1Txt", namespaces=NAMESPACES),
        etree.XPath(".//Filer/USAddress/AddressLine1Txt", namespaces=NAMESPACES),
    ],
    "address_line2": [
        etree.XPath(".//irs:Filer/irs:USAddress/irs:AddressLine2Txt", namespaces=NAMESPACES),
        etree.XPath(".//Filer/USAddress/AddressLine2Txt", namespaces=NAMESPACES),
    ],
    "city": [
        etree.XPath(".//irs:Filer/irs:USAddress/irs:CityNm", namespaces=NAMESPACES),
        etree.XPath(".//Filer/USAddress/CityNm", namespaces=NAMESPACES),
    ],
    "state": [
        etree.XPath(".//irs:Filer/irs:USAddress/irs:StateAbbreviationCd", namespaces=NAMESPACES),
        etree.XPath(".//Filer/USAddress/StateAbbreviationCd", namespaces=NAMESPACES),
    ],
    "zip_code": [
        etree.XPath(".//irs:Filer/irs:USAddress/irs:ZIPCd", namespaces=NAMESPACES),
        etree.XPath(".//Filer/USAddress/ZIPCd", namespaces=NAMESPACES),
    ],
    "recipient_address_line1": [
        etree.XPath(".//irs:RecipientUSAddress/irs:AddressLine1Txt", namespaces=NAMESPACES),
        etree.XPath(".//RecipientUSAddress/AddressLine1Txt", namespaces=NAMESPACES),
        etree.XPath(".//irs:RecipientForeignAddress/irs:AddressLine1Txt", namespaces=NAMESPACES),
        etree.XPath(".//RecipientForeignAddress/AddressLine1Txt", namespaces=NAMESPACES),
    ],
    "recipient_address_line2": [
        etree.XPath(".//irs:RecipientUSAddress/irs:AddressLine2Txt", namespaces=NAMESPACES),
        etree.XPath(".//RecipientUSAddress/AddressLine2Txt", namespaces=NAMESPACES),
        etree.XPath(".//irs:RecipientForeignAddress/irs:AddressLine2Txt", namespaces=NAMESPACES),
        etree.XPath(".//RecipientForeignAddress/AddressLine2Txt", namespaces=NAMESPACES),
    ],
    "recipient_city": [
        etree.XPath(".//irs:RecipientUSAddress/irs:CityNm", namespaces=NAMESPACES),
        etree.XPath(".//RecipientUSAddress/CityNm", namespaces=NAMESPACES),
        etree.XPath(".//irs:RecipientForeignAddress/irs:CityNm", namespaces=NAMESPACES),
        etree.XPath(".//RecipientForeignAddress/CityNm", namespaces=NAMESPACES),
    ],
    "recipient_state": [
        etree.XPath(".//irs:RecipientUSAddress/irs:StateAbbreviationCd", namespaces=NAMESPACES),
        etree.XPath(".//RecipientUSAddress/StateAbbreviationCd", namespaces=NAMESPACES),
        etree.XPath(".//irs:RecipientForeignAddress/irs:StateAbbreviationCd", namespaces=NAMESPACES),
        etree.XPath(".//RecipientForeignAddress/StateAbbreviationCd", namespaces=NAMESPACES),
    ],
    "recipient_zip_code": [
        etree.XPath(".//irs:RecipientUSAddress/irs:ZIPCd", namespaces=NAMESPACES),
        etree.XPath(".//RecipientUSAddress/ZIPCd", namespaces=NAMESPACES),
        etree.XPath(".//irs:RecipientForeignAddress/irs:ZIPCd", namespaces=NAMESPACES),
        etree.XPath(".//RecipientForeignAddress/ZIPCd", namespaces=NAMESPACES),
    ],
    # Common officer compensation patterns
    "officer_comp_value": [
        etree.XPath("irs:ReportableCompFromOrgAmt", namespaces=NAMESPACES),
        etree.XPath("ReportableCompFromOrgAmt", namespaces=NAMESPACES),
        etree.XPath("irs:CompensationAmt", namespaces=NAMESPACES),
        etree.XPath("CompensationAmt", namespaces=NAMESPACES),
    ],
    "officer_name": [
        etree.XPath("irs:PersonNm", namespaces=NAMESPACES),
        etree.XPath("PersonNm", namespaces=NAMESPACES),
    ],
    # Common grant patterns
    "grant_value": [
        etree.XPath("irs:CashGrantAmt", namespaces=NAMESPACES),
    ],
    # Common foreign expense patterns
    "foreign_exp_value": [
        etree.XPath("irs:RegionTotalExpendituresAmt", namespaces=NAMESPACES),
    ],
    # Common schedule O patterns
    "schedule_o_value": [
        etree.XPath(".//irs:ExplanationTxt", namespaces=NAMESPACES),
        etree.XPath(".//irs:SupplementalInformationDetail/irs:ExplanationTxt", namespaces=NAMESPACES),
        etree.XPath(".//irs:Form990ScheduleO/irs:Explanation", namespaces=NAMESPACES),
    ],
    # Filer address components
    "filer_address_line1": [
        etree.XPath(".//ReturnHeader/Filer/USAddress/AddressLine1Txt"),
        etree.XPath(".//Filer/USAddress/AddressLine1Txt"),
        etree.XPath(".//USAddress/AddressLine1Txt"),
    ],
    "filer_address_line2": [
        etree.XPath(".//ReturnHeader/Filer/USAddress/AddressLine2Txt"),
        etree.XPath(".//Filer/USAddress/AddressLine2Txt"),
        etree.XPath(".//USAddress/AddressLine2Txt"),
    ],
    "filer_city": [
        etree.XPath(".//ReturnHeader/Filer/USAddress/CityNm"),
        etree.XPath(".//Filer/USAddress/CityNm"),
        etree.XPath(".//USAddress/CityNm"),
    ],
    "filer_state": [
        etree.XPath(".//ReturnHeader/Filer/USAddress/StateAbbreviationCd"),
        etree.XPath(".//Filer/USAddress/StateAbbreviationCd"),
        etree.XPath(".//USAddress/StateAbbreviationCd"),
    ],
    "filer_zip_code": [
        etree.XPath(".//ReturnHeader/Filer/USAddress/ZIPCd"),
        etree.XPath(".//Filer/USAddress/ZIPCd"),
        etree.XPath(".//USAddress/ZIPCd"),
    ],
    "filer_name_xpaths": [
        etree.XPath(".//irs:ReturnHeader/irs:Filer/irs:BusinessName/irs:BusinessNameLine1Txt", namespaces=NAMESPACES),
        etree.XPath(".//ReturnHeader/Filer/BusinessName/BusinessNameLine1Txt"),
        etree.XPath(".//irs:ReturnHeader/irs:Filer/irs:Name/irs:BusinessNameLine1Txt", namespaces=NAMESPACES),
        etree.XPath(".//ReturnHeader/Filer/Name/BusinessNameLine1Txt")
    ],
    # Political contribution components
    "political_recipient_name": [
        etree.XPath(".//irs:RecipientNm", namespaces=NAMESPACES),
        etree.XPath(".//irs:RecipientName", namespaces=NAMESPACES),
        etree.XPath(".//RecipientNm"),
        etree.XPath(".//RecipientName"),
        etree.XPath("irs:RecipientNm", namespaces=NAMESPACES),
        etree.XPath("irs:RecipientName", namespaces=NAMESPACES),
        etree.XPath("RecipientNm"),
        etree.XPath("RecipientName"),
    ],
    "political_amount": [
        etree.XPath(".//irs:TotalDirectExpendAmt", namespaces=NAMESPACES),
        etree.XPath(".//irs:Amount", namespaces=NAMESPACES),
        etree.XPath(".//TotalDirectExpendAmt"),
        etree.XPath(".//Amount"),
        etree.XPath("irs:TotalDirectExpendAmt", namespaces=NAMESPACES),
        etree.XPath("irs:Amount", namespaces=NAMESPACES),
        etree.XPath("TotalDirectExpendAmt"),
        etree.XPath("Amount"),
    ],
    "political_ein": [
        etree.XPath(".//irs:EIN", namespaces=NAMESPACES),
        etree.XPath(".//irs:RecipientEIN", namespaces=NAMESPACES),
        etree.XPath(".//EIN"),
        etree.XPath(".//RecipientEIN"),
        etree.XPath("irs:EIN", namespaces=NAMESPACES),
        etree.XPath("irs:RecipientEIN", namespaces=NAMESPACES),
        etree.XPath("EIN"),
        etree.XPath("RecipientEIN"),
    ],
}

# Form-specific XPath configurations
XPATHS_990 = {
    **COMMON_XPATHS,  # Include all common patterns
    "receipt": [
        etree.XPath(".//irs:IRS990/irs:GrossReceiptsAmt", namespaces=NAMESPACES),
        etree.XPath(".//irs:GrossReceiptsAmt", namespaces=NAMESPACES),
        etree.XPath(".//irs:IRS990/irs:TotalRevenueAmt", namespaces=NAMESPACES),
        etree.XPath(".//irs:TotalRevenueAmt", namespaces=NAMESPACES),
        etree.XPath(".//irs:IRS990/irs:CYTotalRevenueAmt", namespaces=NAMESPACES),
        etree.XPath(".//irs:CYTotalRevenueAmt", namespaces=NAMESPACES),
    ],
    "govt_grants": [
        etree.XPath(".//irs:IRS990/irs:GovernmentGrantsAmt", namespaces=NAMESPACES),
        etree.XPath(".//irs:GovernmentGrantsAmt", namespaces=NAMESPACES),
    ],
    "contributions": [
        etree.XPath(".//irs:IRS990/irs:AllOtherContributionsAmt", namespaces=NAMESPACES),
        etree.XPath(".//irs:IRS990/irs:TotalContributionsAmt", namespaces=NAMESPACES),
        etree.XPath(".//irs:TotalContributionsAmt", namespaces=NAMESPACES),
    ],
    "total_exp": [
        etree.XPath(".//irs:IRS990/irs:TotalFunctionalExpensesGrp/irs:TotalAmt", namespaces=NAMESPACES),
        etree.XPath(".//irs:TotalFunctionalExpensesGrp/irs:TotalAmt", namespaces=NAMESPACES),
        etree.XPath(".//irs:IRS990/irs:TotalExpensesAmt", namespaces=NAMESPACES),
        etree.XPath(".//irs:TotalExpensesAmt", namespaces=NAMESPACES),
    ],
    "prog_exp": [
        etree.XPath(".//irs:IRS990/irs:TotalProgramServiceExpensesAmt", namespaces=NAMESPACES),
        etree.XPath(".//irs:TotalProgramServiceExpensesAmt", namespaces=NAMESPACES),
        etree.XPath(".//irs:ProgramServiceExpensesAmt", namespaces=NAMESPACES),
    ],
    "travel": [
        etree.XPath(".//irs:IRS990/irs:TravelGrp/irs:TotalAmt", namespaces=NAMESPACES),
        etree.XPath(".//irs:TravelGrp/irs:TotalAmt", namespaces=NAMESPACES),
    ],
    "conferences": [
        etree.XPath(".//irs:IRS990/irs:ConferencesMeetingsGrp/irs:TotalAmt", namespaces=NAMESPACES),
        etree.XPath(".//irs:ConferencesMeetingsGrp/irs:TotalAmt", namespaces=NAMESPACES),
    ],
    "travel": [
        etree.XPath(".//irs:IRS990EZ/irs:TravelGrp/irs:TotalAmt", namespaces=NAMESPACES),
        etree.XPath(".//irs:TravelGrp/irs:TotalAmt", namespaces=NAMESPACES),
    ],
    "conferences": [
        etree.XPath(".//irs:IRS990EZ/irs:ConferencesMeetingsGrp/irs:TotalAmt", namespaces=NAMESPACES),
        etree.XPath(".//irs:ConferencesMeetingsGrp/irs:TotalAmt", namespaces=NAMESPACES),
    ],
    "schedule_o": [
        etree.XPath(".//irs:IRS990ScheduleO/irs:SupplementalInformationDetail", namespaces=NAMESPACES),
    ],
    "officer_comp_elements": [
        etree.XPath(".//irs:IRS990/irs:Form990PartVIISectionAGrp", namespaces=NAMESPACES),
        etree.XPath(".//irs:Form990PartVIISectionAGrp", namespaces=NAMESPACES),
        etree.XPath(".//irs:IRS990/irs:OfficerDirectorTrusteeEmplGrp", namespaces=NAMESPACES),
        etree.XPath(".//irs:OfficerDirectorTrusteeEmplGrp", namespaces=NAMESPACES),
    ],
    "officer_comp": [
        etree.XPath(".//irs:IRS990/irs:Form990PartVIISectionAGrp/irs:ReportableCompFromOrgAmt", namespaces=NAMESPACES),
        etree.XPath(".//irs:IRS990/irs:Form990PartVIISectionAGrp/irs:ReportableCompFromOrgAmt", namespaces=NAMESPACES),
        etree.XPath(".//irs:IRS990/irs:OfficerDirectorTrusteeEmplGrp/irs:CompensationAmt", namespaces=NAMESPACES),
        etree.XPath(".//irs:OfficerDirectorTrusteeEmplGrp/irs:CompensationAmt", namespaces=NAMESPACES),
    ],
    "grants_to_others": [
        etree.XPath(".//irs:ReturnData/irs:IRS990ScheduleF/irs:GrantsToOrgOutsideUSGrp/irs:CashGrantAmt", namespaces=NAMESPACES),
        etree.XPath(".//irs:ReturnData/irs:IRS990ScheduleF/irs:GrantsToOrganizationsOutsideUS/irs:CashGrantAmt", namespaces=NAMESPACES),
        etree.XPath(".//irs:ReturnData/irs:IRS990ScheduleF/irs:GrantsToOrgsOutsideUS/irs:CashGrantAmt", namespaces=NAMESPACES),
        etree.XPath(".//irs:ReturnData/irs:IRS990ScheduleF/irs:ForeignIndividualsGrantsGrp/irs:CashGrantAmt", namespaces=NAMESPACES),
        etree.XPath(".//irs:ReturnData/irs:IRS990ScheduleI/irs:RecipientTable/irs:CashGrantAmt", namespaces=NAMESPACES),
        etree.XPath(".//irs:ReturnData/irs:IRS990ScheduleI/irs:GrantsOtherAsstToIndivInUSGrp/irs:CashGrantAmt", namespaces=NAMESPACES),
    ],
    "grant_elements_f": [
        etree.XPath(".//irs:ReturnData/irs:IRS990ScheduleF", namespaces=NAMESPACES),
    ],
    "grant_sub_elements_f": [
        etree.XPath(".//irs:GrantsToOrgOutsideUSGrp", namespaces=NAMESPACES),
        etree.XPath(".//irs:GrantsToOrganizationsOutsideUS", namespaces=NAMESPACES),
        etree.XPath(".//irs:GrantsToOrgsOutsideUS", namespaces=NAMESPACES),
        etree.XPath(".//irs:ForeignIndividualsGrantsGrp", namespaces=NAMESPACES),
    ],
    "grant_elements_i": [
        etree.XPath(".//irs:ReturnData/irs:IRS990ScheduleI", namespaces=NAMESPACES),
    ],
    "grant_sub_elements_i": [
        etree.XPath(".//irs:RecipientTable", namespaces=NAMESPACES),
        etree.XPath(".//irs:GrantsOtherAsstToIndivInUSGrp", namespaces=NAMESPACES),
    ],
    "foreign_expenses": [
        etree.XPath(".//irs:ReturnData/irs:IRS990ScheduleF/irs:StmtOfActyOutsdUSGrp/irs:RegionTotalExpendituresAmt", namespaces=NAMESPACES),
        etree.XPath(".//irs:ReturnData/irs:IRS990ScheduleF/irs:AccountActivitiesOutsideUSGrp/irs:RegionTotalExpendituresAmt", namespaces=NAMESPACES),
    ],
    "foreign_exp_elements": [
        etree.XPath(".//irs:ReturnData/irs:IRS990ScheduleF", namespaces=NAMESPACES),
    ],
    "foreign_exp_sub_elements": [
        etree.XPath(".//irs:StmtOfActyOutsdUSGrp", namespaces=NAMESPACES),
        etree.XPath(".//irs:AccountActivitiesOutsideUSGrp", namespaces=NAMESPACES),
    ],
    "org_type": [
        etree.XPath(".//irs:IRS990/irs:Organization501c3Ind", namespaces=NAMESPACES),
        etree.XPath(".//irs:IRS990/irs:Organization501cInd", namespaces=NAMESPACES),
        etree.XPath(".//irs:IRS990/irs:Organization4947a1NotPFInd", namespaces=NAMESPACES),
    ],
    "foreign_office": [
        etree.XPath(".//irs:IRS990/irs:ForeignOfficeInd", namespaces=NAMESPACES),
    ],
    "total_assets": [
        etree.XPath(".//irs:IRS990/irs:TotalAssetsEOYAmt", namespaces=NAMESPACES),
    ],
    "contractor_elements": [
        etree.XPath(".//irs:ReturnData/irs:IRS990/irs:ContractorCompensationGrp", namespaces=NAMESPACES),
        etree.XPath(".//irs:IRS990/irs:ContractorCompensationGrp", namespaces=NAMESPACES),
        etree.XPath(".//irs:ContractorCompensationGrp", namespaces=NAMESPACES),
    ],
    "contractor_name_line1": [
        etree.XPath("irs:ContractorName/irs:BusinessName/irs:BusinessNameLine1Txt", namespaces=NAMESPACES),
        etree.XPath("irs:ContractorName/irs:BusinessNameLine1Txt", namespaces=NAMESPACES),
        etree.XPath("ContractorName/BusinessName/BusinessNameLine1Txt"),
        etree.XPath("ContractorName/BusinessNameLine1Txt"),
    ],
    "contractor_name_line2": [
        etree.XPath("irs:ContractorName/irs:BusinessName/irs:BusinessNameLine2Txt", namespaces=NAMESPACES),
        etree.XPath("irs:ContractorName/irs:BusinessNameLine2Txt", namespaces=NAMESPACES),
        etree.XPath("ContractorName/BusinessName/BusinessNameLine2Txt"),
        etree.XPath("ContractorName/BusinessNameLine2Txt"),
    ],
    "contractor_address_line1": [
        etree.XPath("irs:ContractorAddress/irs:USAddress/irs:AddressLine1Txt", namespaces=NAMESPACES),
        etree.XPath("irs:ContractorAddress/irs:USAddress/irs:AddressLine1", namespaces=NAMESPACES),
        etree.XPath("ContractorAddress/USAddress/AddressLine1Txt"),
        etree.XPath("ContractorAddress/USAddress/AddressLine1"),
    ],
    "contractor_address_line2": [
        etree.XPath("irs:ContractorAddress/irs:USAddress/irs:AddressLine2Txt", namespaces=NAMESPACES),
        etree.XPath("irs:ContractorAddress/irs:USAddress/irs:AddressLine2", namespaces=NAMESPACES),
        etree.XPath("ContractorAddress/USAddress/AddressLine2Txt"),
        etree.XPath("ContractorAddress/USAddress/AddressLine2"),
    ],
    "contractor_city": [
        etree.XPath("irs:ContractorAddress/irs:USAddress/irs:CityNm", namespaces=NAMESPACES),
        etree.XPath("ContractorAddress/USAddress/CityNm"),
    ],
    "contractor_state": [
        etree.XPath("irs:ContractorAddress/irs:USAddress/irs:StateAbbreviationCd", namespaces=NAMESPACES),
        etree.XPath("ContractorAddress/USAddress/StateAbbreviationCd"),
    ],
    "contractor_zip_code": [
        etree.XPath("irs:ContractorAddress/irs:USAddress/irs:ZIPCd", namespaces=NAMESPACES),
        etree.XPath("ContractorAddress/USAddress/ZIPCd"),
    ],
}

# Grant-related XPath patterns
GRANT_XPATHS = {
    "990": [
        etree.XPath(".//irs:IRS990ScheduleI/irs:RecipientTable", namespaces=NAMESPACES),
        etree.XPath(".//irs:IRS990ScheduleF/irs:GrantsToOrgOutsideUSGrp", namespaces=NAMESPACES),
        etree.XPath(".//irs:IRS990ScheduleF/irs:GrantsToOrganizationsOutsideUS", namespaces=NAMESPACES),
        etree.XPath(".//irs:IRS990ScheduleF/irs:GrantsToOrgsOutsideUS", namespaces=NAMESPACES),
        etree.XPath(".//irs:IRS990ScheduleF/irs:ForeignIndividualsGrantsGrp", namespaces=NAMESPACES),
    ],
    "990EZ": [
        etree.XPath(".//irs:IRS990ScheduleI/irs:RecipientTable", namespaces=NAMESPACES),
        etree.XPath(".//irs:IRS990ScheduleF/irs:GrantsToOrgOutsideUSGrp", namespaces=NAMESPACES),
        etree.XPath(".//irs:IRS990ScheduleF/irs:GrantsToOrganizationsOutsideUS", namespaces=NAMESPACES),
        etree.XPath(".//irs:IRS990ScheduleF/irs:GrantsToOrgsOutsideUS", namespaces=NAMESPACES),
        etree.XPath(".//irs:IRS990ScheduleF/irs:ForeignIndividualsGrantsGrp", namespaces=NAMESPACES),
    ],
    "990PF": [
        etree.XPath(".//irs:IRS990PF/irs:SupplementaryInformationGrp", namespaces=NAMESPACES),
    ],
}

GRANT_EIN_XPATHS = [
    etree.XPath("irs:RecipientEIN", namespaces=NAMESPACES),
    etree.XPath("*[local-name()='RecipientEIN']", namespaces=NAMESPACES),
    etree.XPath("irs:EIN", namespaces=NAMESPACES),
    etree.XPath("*[local-name()='EIN']", namespaces=NAMESPACES),
]

GRANT_NAME_XPATHS = [
    etree.XPath("*[local-name()='RecipientNameBusiness']/*[local-name()='BusinessNameLine1Txt']", namespaces=NAMESPACES),
    etree.XPath("*[local-name()='RecipientNameBusiness']/*[local-name()='BusinessNameLine1']", namespaces=NAMESPACES),
    etree.XPath("*[local-name()='RecipientBusinessName']/*[local-name()='BusinessNameLine1Txt']", namespaces=NAMESPACES),
    etree.XPath("*[local-name()='RecipientBusinessName']/*[local-name()='BusinessNameLine1']", namespaces=NAMESPACES),
    etree.XPath("*[local-name()='RecipientNm']", namespaces=NAMESPACES),
    etree.XPath("*[local-name()='RecipientName']", namespaces=NAMESPACES),
]

GRANT_AMOUNT_XPATHS = [
    etree.XPath("*[local-name()='CashGrantAmt']", namespaces=NAMESPACES),
    etree.XPath("*[local-name()='Amount']", namespaces=NAMESPACES),
    etree.XPath("*[local-name()='TotalGrantOrContriPdDurYrAmt']", namespaces=NAMESPACES),
]

GRANT_FOREIGN_ADDRESS_XPATH = etree.XPath("irs:ForeignAddress", namespaces=NAMESPACES)
GRANT_COUNTRY_XPATH = etree.XPath("irs:CountryCd", namespaces=NAMESPACES)
GRANT_US_ADDRESS_XPATH = etree.XPath("irs:USAddress", namespaces=NAMESPACES)

# Schedule C (Political Contributions) XPath patterns
SCHEDULE_C_XPATHS = {
    "990": [
        etree.XPath(".//irs:IRS990ScheduleC/irs:PoliticalCampaignActyGrp", namespaces=NAMESPACES),
    ],
    "990EZ": [
        etree.XPath(".//irs:IRS990ScheduleC/irs:PoliticalCampaignActyGrp", namespaces=NAMESPACES),
    ],
    "990PF": [
        etree.XPath(".//irs:IRS990ScheduleC/irs:PoliticalCampaignActyGrp", namespaces=NAMESPACES),
    ],
}

# Schedule L (Contractors) XPath patterns
SCHEDULE_L_XPATHS = {
    "990": [
        # Also check directly under IRS990 for ContractorCompensationGrp
        etree.XPath(".//irs:IRS990/irs:ContractorCompensationGrp", namespaces=NAMESPACES),
        etree.XPath(".//irs:ContractorCompensationGrp", namespaces=NAMESPACES),
    ],
    "990EZ": [
         # Also check directly under IRS990EZ for ContractorCompensationGrp
        etree.XPath(".//irs:IRS990EZ/irs:ContractorCompensationGrp", namespaces=NAMESPACES),
        etree.XPath(".//irs:ContractorCompensationGrp", namespaces=NAMESPACES),
    ],
    "990PF": [
         # Also check directly under IRS990PF for CompensationOfHghstPdCntrctGrp
        etree.XPath(".//irs:IRS990PF/irs:CompensationOfHghstPdCntrctGrp", namespaces=NAMESPACES),
        etree.XPath(".//irs:CompensationOfHghstPdCntrctGrp", namespaces=NAMESPACES),
    ],
}

SCHEDULE_C_AMOUNT_XPATHS = [
    etree.XPath("irs:TotalDirectExpendAmt", namespaces=NAMESPACES),
    etree.XPath("irs:Amount", namespaces=NAMESPACES),
]

SCHEDULE_C_RECIPIENT_XPATHS = [
    etree.XPath("irs:RecipientNm", namespaces=NAMESPACES),
    etree.XPath("irs:RecipientName", namespaces=NAMESPACES),
]

SCHEDULE_C_EIN_XPATHS = [
    etree.XPath("irs:EIN", namespaces=NAMESPACES),
    etree.XPath("irs:RecipientEIN", namespaces=NAMESPACES),
]

XPATHS_990EZ = {
    **COMMON_XPATHS,  # Include all common patterns
    "receipt": [
        etree.XPath(".//irs:IRS990EZ/irs:GrossReceiptsAmt", namespaces=NAMESPACES),
        etree.XPath(".//irs:IRS990EZ/irs:TotalRevenueAmt", namespaces=NAMESPACES),
        etree.XPath(".//irs:GrossReceiptsAmt", namespaces=NAMESPACES),
        etree.XPath(".//irs:TotalRevenueAmt", namespaces=NAMESPACES),
    ],
    "contributions": [
        etree.XPath(".//irs:TotalContributionsAmt", namespaces=NAMESPACES),
    ],
    "total_exp": [
        etree.XPath(".//irs:IRS990EZ/irs:TotalExpensesAmt", namespaces=NAMESPACES),
        etree.XPath(".//irs:TotalExpensesAmt", namespaces=NAMESPACES),
    ],
    "prog_exp": [
        etree.XPath(".//irs:IRS990EZ/irs:TotalProgramServiceExpensesAmt", namespaces=NAMESPACES),
        etree.XPath(".//irs:TotalProgramServiceExpensesAmt", namespaces=NAMESPACES),
    ],
    "schedule_o": [
        etree.XPath(".//irs:IRS990ScheduleO/irs:SupplementalInformationDetail", namespaces=NAMESPACES),
    ],
    "officer_comp_elements": [
        etree.XPath(".//irs:IRS990EZ/irs:OfficerDirectorTrusteeEmplGrp", namespaces=NAMESPACES),
        etree.XPath(".//irs:OfficerDirectorTrusteeEmplGrp", namespaces=NAMESPACES),
    ],
    "officer_comp": [
        etree.XPath(".//irs:IRS990EZ/irs:OfficerDirectorTrusteeEmplGrp/irs:CompensationAmt", namespaces=NAMESPACES),
        etree.XPath(".//irs:OfficerDirectorTrusteeEmplGrp/irs:CompensationAmt", namespaces=NAMESPACES),
    ],
    "grants_to_others": [
        etree.XPath(".//irs:ReturnData/irs:IRS990EZ/irs:IRS990ScheduleF/irs:GrantsToOrgOutsideUSGrp/irs:CashGrantAmt", namespaces=NAMESPACES),
        etree.XPath(".//irs:ReturnData/irs:IRS990EZ/irs:IRS990ScheduleF/irs:GrantsToOrganizationsOutsideUS/irs:CashGrantAmt", namespaces=NAMESPACES),
        etree.XPath(".//irs:ReturnData/irs:IRS990EZ/irs:IRS990ScheduleF/irs:GrantsToOrgsOutsideUS/irs:CashGrantAmt", namespaces=NAMESPACES),
        etree.XPath(".//irs:ReturnData/irs:IRS990EZ/irs:IRS990ScheduleF/irs:ForeignIndividualsGrantsGrp/irs:CashGrantAmt", namespaces=NAMESPACES),
        etree.XPath(".//irs:ReturnData/irs:IRS990EZ/irs:IRS990ScheduleI/irs:RecipientTable/irs:CashGrantAmt", namespaces=NAMESPACES),
        etree.XPath(".//irs:ReturnData/irs:IRS990EZ/irs:IRS990ScheduleI/irs:GrantsOtherAsstToIndivInUSGrp/irs:CashGrantAmt", namespaces=NAMESPACES),
    ],
    "grant_elements_f": [
        etree.XPath(".//irs:ReturnData/irs:IRS990EZ/irs:IRS990ScheduleF", namespaces=NAMESPACES),
    ],
    "grant_sub_elements_f": [
        etree.XPath(".//irs:GrantsToOrgOutsideUSGrp", namespaces=NAMESPACES),
        etree.XPath(".//irs:GrantsToOrganizationsOutsideUS", namespaces=NAMESPACES),
        etree.XPath(".//irs:GrantsToOrgsOutsideUS", namespaces=NAMESPACES),
        etree.XPath(".//irs:ForeignIndividualsGrantsGrp", namespaces=NAMESPACES),
    ],
    "grant_elements_i": [
        etree.XPath(".//irs:ReturnData/irs:IRS990EZ/irs:IRS990ScheduleI", namespaces=NAMESPACES),
    ],
    "grant_sub_elements_i": [
        etree.XPath(".//irs:RecipientTable", namespaces=NAMESPACES),
        etree.XPath(".//irs:GrantsOtherAsstToIndivInUSGrp", namespaces=NAMESPACES),
    ],
    "grant_elements_o": [
        etree.XPath(".//irs:IRS990ScheduleO/irs:SupplementalInformationDetail", namespaces=NAMESPACES),
    ],
    "foreign_expenses": [
        etree.XPath(".//irs:ReturnData/irs:IRS990EZ/irs:IRS990ScheduleF/irs:StmtOfActyOutsdUSGrp/irs:RegionTotalExpendituresAmt", namespaces=NAMESPACES),
        etree.XPath(".//irs:ReturnData/irs:IRS990EZ/irs:IRS990ScheduleF/irs:AccountActivitiesOutsideUSGrp/irs:RegionTotalExpendituresAmt", namespaces=NAMESPACES),
    ],
    "foreign_exp_elements": [
        etree.XPath(".//irs:ReturnData/irs:IRS990EZ/irs:IRS990ScheduleF", namespaces=NAMESPACES),
    ],
    "foreign_exp_sub_elements": [
        etree.XPath(".//irs:StmtOfActyOutsdUSGrp", namespaces=NAMESPACES),
        etree.XPath(".//irs:AccountActivitiesOutsideUSGrp", namespaces=NAMESPACES),
    ],
    "org_type": [
        etree.XPath(".//irs:IRS990EZ/irs:Organization501c3Ind", namespaces=NAMESPACES),
        etree.XPath(".//irs:IRS990EZ/irs:Organization501cInd", namespaces=NAMESPACES),
        etree.XPath(".//irs:IRS990EZ/irs:Organization4947a1NotPFInd", namespaces=NAMESPACES),
    ],
    "foreign_office": [
        etree.XPath(".//irs:IRS990EZ/irs:ForeignOfficeInd", namespaces=NAMESPACES),
        etree.XPath(".//irs:ForeignOfficeCountryCd", namespaces=NAMESPACES),
    ],
    "total_assets": [
        etree.XPath(".//irs:IRS990EZ/irs:TotalAssetsEOYAmt", namespaces=NAMESPACES),
    ],
    "contractor_elements": [
        etree.XPath(".//irs:ReturnData/irs:IRS990EZ/irs:ContractorCompensationGrp", namespaces=NAMESPACES),
        etree.XPath(".//irs:IRS990EZ/irs:ContractorCompensationGrp", namespaces=NAMESPACES),
        etree.XPath(".//irs:ContractorCompensationGrp", namespaces=NAMESPACES),
    ],
    "contractor_name_line1": [
        etree.XPath("irs:ContractorName/irs:BusinessName/irs:BusinessNameLine1Txt", namespaces=NAMESPACES),
        etree.XPath("irs:ContractorName/irs:BusinessNameLine1Txt", namespaces=NAMESPACES),
        etree.XPath("ContractorName/BusinessName/BusinessNameLine1Txt"),
        etree.XPath("ContractorName/BusinessNameLine1Txt"),
    ],
    "contractor_name_line2": [
        etree.XPath("irs:ContractorName/irs:BusinessName/irs:BusinessNameLine2Txt", namespaces=NAMESPACES),
        etree.XPath("irs:ContractorName/irs:BusinessNameLine2Txt", namespaces=NAMESPACES),
        etree.XPath("ContractorName/BusinessName/BusinessNameLine2Txt"),
        etree.XPath("ContractorName/BusinessNameLine2Txt"),
    ],
    "contractor_address_line1": [
        etree.XPath("irs:ContractorAddress/irs:USAddress/irs:AddressLine1Txt", namespaces=NAMESPACES),
        etree.XPath("irs:ContractorAddress/irs:USAddress/irs:AddressLine1", namespaces=NAMESPACES),
        etree.XPath("ContractorAddress/USAddress/AddressLine1Txt"),
        etree.XPath("ContractorAddress/USAddress/AddressLine1"),
    ],
    "contractor_address_line2": [
        etree.XPath("irs:ContractorAddress/irs:USAddress/irs:AddressLine2Txt", namespaces=NAMESPACES),
        etree.XPath("irs:ContractorAddress/irs:USAddress/irs:AddressLine2", namespaces=NAMESPACES),
        etree.XPath("ContractorAddress/USAddress/AddressLine2Txt"),
        etree.XPath("ContractorAddress/USAddress/AddressLine2"),
    ],
    "contractor_city": [
        etree.XPath("irs:ContractorAddress/irs:USAddress/irs:CityNm", namespaces=NAMESPACES),
        etree.XPath("ContractorAddress/USAddress/CityNm"),
    ],
    "contractor_state": [
        etree.XPath("irs:ContractorAddress/irs:USAddress/irs:StateAbbreviationCd", namespaces=NAMESPACES),
        etree.XPath("ContractorAddress/USAddress/StateAbbreviationCd"),
    ],
    "contractor_zip_code": [
        etree.XPath("irs:ContractorAddress/irs:USAddress/irs:ZIPCd", namespaces=NAMESPACES),
        etree.XPath("ContractorAddress/USAddress/ZIPCd"),
    ],
}

XPATHS_990PF = {
    **COMMON_XPATHS,  # Include all common patterns
    "receipt": [
        etree.XPath(".//irs:IRS990PF/irs:AnalysisOfRevenueAndExpenses/irs:TotalRevAndExpnssAmt", namespaces=NAMESPACES),
        etree.XPath(".//irs:AnalysisOfRevenueAndExpenses/irs:TotalRevAndExpnssAmt", namespaces=NAMESPACES),
    ],
    "total_exp": [
        etree.XPath(".//irs:IRS990PF/irs:AnalysisOfRevenueAndExpenses/irs:TotalExpensesRevAndExpnssAmt", namespaces=NAMESPACES),
        etree.XPath(".//irs:AnalysisOfRevenueAndExpenses/irs:TotalExpensesRevAndExpnssAmt", namespaces=NAMESPACES),
    ],
    "prog_exp": [
        etree.XPath(".//irs:IRS990PF/irs:AnalysisOfRevenueAndExpenses/irs:ContriPaidDsbrsChrtblAmt", namespaces=NAMESPACES),
        etree.XPath(".//irs:AnalysisOfRevenueAndExpenses/irs:ContriPaidDsbrsChrtblAmt", namespaces=NAMESPACES),
    ],
    "schedule_expenses": [
        etree.XPath(".//irs:OtherExpensesSchedule/irs:OtherExpensesScheduleGrp", namespaces=NAMESPACES),
    ],
    "expense_value": [
        etree.XPath("irs:RevenueAndExpensesPerBooksAmt", namespaces=NAMESPACES),
        etree.XPath("irs:NetInvestmentIncomeAmt", namespaces=NAMESPACES),
        etree.XPath("irs:DisbursementsCharitablePrpsAmt", namespaces=NAMESPACES),
    ],
    "expense_desc": [
        etree.XPath("irs:Desc", namespaces=NAMESPACES),
    ],
    "officer_comp_elements": [
        etree.XPath(".//irs:IRS990PF/irs:OfficerDirTrstKeyEmplInfoGrp/irs:OfficerDirTrstKeyEmplGrp", namespaces=NAMESPACES),
        etree.XPath(".//irs:OfficerDirTrstKeyEmplInfoGrp/irs:OfficerDirTrstKeyEmplGrp", namespaces=NAMESPACES),
    ],
    "officer_comp": [
        etree.XPath(".//irs:IRS990PF/irs:OfficerDirTrstKeyEmplInfoGrp/irs:OfficerDirTrstKeyEmplGrp/irs:CompensationAmt", namespaces=NAMESPACES),
        etree.XPath(".//irs:OfficerDirTrstKeyEmplInfoGrp/irs:OfficerDirTrstKeyEmplGrp/irs:CompensationAmt", namespaces=NAMESPACES),
    ],
    "grants_to_others": [
        etree.XPath(".//irs:IRS990PF/irs:SupplementaryInformationGrp/irs:TotalGrantOrContriPdDurYrAmt", namespaces=NAMESPACES),
        etree.XPath(".//irs:SupplementaryInformationGrp/irs:TotalGrantOrContriPdDurYrAmt", namespaces=NAMESPACES),
    ],
    "org_type": [
        etree.XPath(".//irs:IRS990PF/irs:Organization501c3ExemptPFInd", namespaces=NAMESPACES),
        etree.XPath(".//irs:IRS990PF/irs:Organization4947a1TrtdPFInd", namespaces=NAMESPACES),
        etree.XPath(".//irs:IRS990PF/irs:Organization501c3TaxablePFInd", namespaces=NAMESPACES),
        etree.XPath(".//irs:Organization501c3ExemptPFInd", namespaces=NAMESPACES),
        etree.XPath(".//irs:Organization501c3TaxablePFInd", namespaces=NAMESPACES),
    ],
    "total_assets": [
        etree.XPath(".//irs:IRS990PF/irs:Form990PFBalanceSheetsGrp/irs:TotalAssetsEOYAmt", namespaces=NAMESPACES),
        etree.XPath(".//irs:Form990PFBalanceSheetsGrp/irs:TotalAssetsEOYAmt", namespaces=NAMESPACES),
    ],
    "total_assets_boy": [
        etree.XPath(".//irs:IRS990PF/irs:Form990PFBalanceSheetsGrp/irs:TotalAssetsBOYAmt", namespaces=NAMESPACES),
        etree.XPath(".//irs:Form990PFBalanceSheetsGrp/irs:TotalAssetsBOYAmt", namespaces=NAMESPACES),
    ],
    "total_liabilities": [
        etree.XPath(".//irs:IRS990PF/irs:Form990PFBalanceSheetsGrp/irs:TotalLiabilitiesEOYAmt", namespaces=NAMESPACES),
        etree.XPath(".//irs:Form990PFBalanceSheetsGrp/irs:TotalLiabilitiesEOYAmt", namespaces=NAMESPACES),
    ],
    "net_assets": [
        etree.XPath(".//irs:IRS990PF/irs:Form990PFBalanceSheetsGrp/irs:TotNetAstOrFundBalancesEOYAmt", namespaces=NAMESPACES),
        etree.XPath(".//irs:Form990PFBalanceSheetsGrp/irs:TotNetAstOrFundBalancesEOYAmt", namespaces=NAMESPACES),
    ],
    "contractor_elements": [
        etree.XPath(".//irs:ReturnData/irs:IRS990PF/irs:CompensationOfHghstPdCntrctGrp", namespaces=NAMESPACES),
        etree.XPath(".//irs:IRS990PF/irs:CompensationOfHghstPdCntrctGrp", namespaces=NAMESPACES),
        etree.XPath(".//irs:CompensationOfHghstPdCntrctGrp", namespaces=NAMESPACES),
    ],
    "contractor_name_line1": [
        etree.XPath("irs:BusinessName/irs:BusinessNameLine1Txt", namespaces=NAMESPACES),
        etree.XPath("irs:BusinessName/irs:BusinessNameLine1", namespaces=NAMESPACES),
        etree.XPath("BusinessName/BusinessNameLine1Txt"),
        etree.XPath("BusinessName/BusinessNameLine1"),
    ],
    "contractor_name_line2": [
        etree.XPath("irs:BusinessName/irs:BusinessNameLine2Txt", namespaces=NAMESPACES),
        etree.XPath("irs:BusinessName/irs:BusinessNameLine2", namespaces=NAMESPACES),
        etree.XPath("BusinessName/BusinessNameLine2Txt"),
        etree.XPath("BusinessName/BusinessNameLine2"),
    ],
    "contractor_address_line1": [
        etree.XPath("irs:USAddress/irs:AddressLine1Txt", namespaces=NAMESPACES),
        etree.XPath("irs:USAddress/irs:AddressLine1", namespaces=NAMESPACES),
        etree.XPath("USAddress/AddressLine1Txt"),
        etree.XPath("USAddress/AddressLine1"),
    ],
    "contractor_address_line2": [
        etree.XPath("irs:USAddress/irs:AddressLine2Txt", namespaces=NAMESPACES),
        etree.XPath("irs:USAddress/irs:AddressLine2", namespaces=NAMESPACES),
        etree.XPath("USAddress/AddressLine2Txt"),
        etree.XPath("USAddress/AddressLine2"),
    ],
    "contractor_city": [
        etree.XPath("irs:USAddress/irs:CityNm", namespaces=NAMESPACES),
        etree.XPath("USAddress/CityNm"),
    ],
    "contractor_state": [
        etree.XPath("irs:USAddress/irs:StateAbbreviationCd", namespaces=NAMESPACES),
        etree.XPath("USAddress/StateAbbreviationCd"),
    ],
    "contractor_zip_code": [
        etree.XPath("irs:USAddress/irs:ZIPCd", namespaces=NAMESPACES),
        etree.XPath("USAddress/ZIPCd"),
    ],
    "grant_elements": [
        etree.XPath(".//irs:IRS990PF/irs:SupplementaryInformationGrp/irs:GrantOrContributionPdDurYrGrp", namespaces=NAMESPACES),
        etree.XPath(".//irs:SupplementaryInformationGrp/irs:GrantOrContributionPdDurYrGrp", namespaces=NAMESPACES),
    ],
}