from lxml import etree

NAMESPACES = {'irs': 'http://www.irs.gov/efile'}

XPATHS_990 = {
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
    "schedule_o": [
        etree.XPath(".//irs:IRS990/irs:TravelGrp/irs:TotalAmt", namespaces=NAMESPACES),
        etree.XPath(".//irs:TravelGrp/irs:TotalAmt", namespaces=NAMESPACES),
        etree.XPath(".//irs:IRS990/irs:ConferencesMeetingsGrp/irs:TotalAmt", namespaces=NAMESPACES),
        etree.XPath(".//irs:ConferencesMeetingsGrp/irs:TotalAmt", namespaces=NAMESPACES),
    ],
    "officer_comp_elements": [
        etree.XPath(".//irs:IRS990/irs:Form990PartVIISectionAGrp", namespaces=NAMESPACES),
        etree.XPath(".//irs:Form990PartVIISectionAGrp", namespaces=NAMESPACES),
        etree.XPath(".//irs:IRS990/irs:OfficerDirectorTrusteeEmplGrp", namespaces=NAMESPACES),
        etree.XPath(".//irs:OfficerDirectorTrusteeEmplGrp", namespaces=NAMESPACES),
    ],
    "officer_comp_value": [
        etree.XPath("irs:ReportableCompFromOrgAmt", namespaces=NAMESPACES),
        etree.XPath("ReportableCompFromOrgAmt", namespaces=NAMESPACES),
        etree.XPath("irs:CompensationAmt", namespaces=NAMESPACES),
        etree.XPath("CompensationAmt", namespaces=NAMESPACES),
    ],
    "officer_comp": [
        etree.XPath(".//irs:IRS990/irs:Form990PartVIISectionAGrp/irs:ReportableCompFromOrgAmt", namespaces=NAMESPACES),
        etree.XPath(".//irs:Form990PartVIISectionAGrp/irs:ReportableCompFromOrgAmt", namespaces=NAMESPACES),
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
    "grant_value": [
        etree.XPath("irs:CashGrantAmt", namespaces=NAMESPACES),
    ],
    "foreign_expenses": [
        etree.XPath(".//irs:ReturnData/irs:IRS990ScheduleF/irs:StmtOfActyOutsdUSGrp/irs:RegionTotalExpendituresAmt", namespaces=NAMESPACES),
        etree.XPath(".//irs:ReturnData/irs:IRS990ScheduleF/irs:AccountActivitiesOutsideUSGrp/irs:RegionTotalExpendituresAmt", namespaces=NAMESPACES),
    ],
    "schedule_o_value": [
        etree.XPath(".//irs:ExplanationTxt", namespaces=NAMESPACES),
        etree.XPath(".//irs:SupplementalInformationDetail/irs:ExplanationTxt", namespaces=NAMESPACES),
        etree.XPath(".//irs:Form990ScheduleO/irs:Explanation", namespaces=NAMESPACES),
    ],
    "foreign_exp_elements": [
        etree.XPath(".//irs:ReturnData/irs:IRS990ScheduleF", namespaces=NAMESPACES),
    ],
    "foreign_exp_sub_elements": [
        etree.XPath(".//irs:StmtOfActyOutsdUSGrp", namespaces=NAMESPACES),
        etree.XPath(".//irs:AccountActivitiesOutsideUSGrp", namespaces=NAMESPACES),
    ],
    "foreign_exp_value": [
        etree.XPath("irs:RegionTotalExpendituresAmt", namespaces=NAMESPACES),
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
    "return_data": [
        etree.XPath(".//irs:ReturnData", namespaces=NAMESPACES),
        etree.XPath(".//ReturnData", namespaces=NAMESPACES),
    ],
}
