# xpaths.py
from lxml import etree

NAMESPACES = {'irs': 'http://www.irs.gov/efile'}

XPATHS_990EZ = {
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
    "schedule_o_value": [
        etree.XPath("irs:ExplanationTxt", namespaces=NAMESPACES),
    ],
    "officer_comp_elements": [
        etree.XPath(".//irs:IRS990EZ/irs:OfficerDirectorTrusteeEmplGrp", namespaces=NAMESPACES),
        etree.XPath(".//irs:OfficerDirectorTrusteeEmplGrp", namespaces=NAMESPACES),
    ],
    "officer_comp_value": [
        etree.XPath("irs:CompensationAmt", namespaces=NAMESPACES),
    ],
    "officer_name": [
        etree.XPath("irs:PersonNm", namespaces=NAMESPACES),
        etree.XPath("PersonNm", namespaces=NAMESPACES),
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
    "grant_value": [
        etree.XPath("irs:CashGrantAmt", namespaces=NAMESPACES),
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
    "foreign_exp_value": [
        etree.XPath("irs:RegionTotalExpendituresAmt", namespaces=NAMESPACES),
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
    "return_data": [
        etree.XPath(".//irs:ReturnData", namespaces=NAMESPACES),
        etree.XPath(".//ReturnData", namespaces=NAMESPACES),
    ],
}

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
    "officer_name": [
        etree.XPath("irs:PersonNm", namespaces=NAMESPACES),
        etree.XPath("PersonNm", namespaces=NAMESPACES),
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

XPATHS_990PF = {
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
        etree.XPath(".//irs:IRS990PF/irs:AnalysisOfRevenueAndExpenses/irs:DividendsRevAndExpnssAmt", namespaces=NAMESPACES),
        etree.XPath(".//irs:IRS990PF/irs:AnalysisOfRevenueAndExpenses/irs:OtherIncomeRevAndExpnssAmt", namespaces=NAMESPACES),
        etree.XPath(".//irs:IRS990PF/irs:AnalysisOfRevenueAndExpenses/irs:NetGainSaleAstRevAndExpnssAmt", namespaces=NAMESPACES),
        etree.XPath(".//irs:AnalysisOfRevenueAndExpenses/irs:DividendsRevAndExpnssAmt", namespaces=NAMESPACES),
        etree.XPath(".//irs:AnalysisOfRevenueAndExpenses/irs:NetGainSaleAstRevAndExpnssAmt", namespaces=NAMESPACES),
        etree.XPath(".//irs:AnalysisOfRevenueAndExpenses/irs:OtherIncomeRevAndExpnssAmt", namespaces=NAMESPACES),
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
    ],
    "expense_desc": [
        etree.XPath("irs:Desc", namespaces=NAMESPACES),
    ],
    "officer_comp_elements": [
        etree.XPath(".//irs:IRS990PF/irs:OfficerDirTrstKeyEmplGrp", namespaces=NAMESPACES),
        etree.XPath(".//irs:OfficerDirTrstKeyEmplGrp", namespaces=NAMESPACES),
    ],
    "officer_comp_value": [
        etree.XPath("irs:CompensationAmt", namespaces=NAMESPACES),
    ],
    "officer_name": [
        etree.XPath("irs:PersonNm", namespaces=NAMESPACES),
        etree.XPath("PersonNm", namespaces=NAMESPACES),
    ],
    "officer_comp": [
        etree.XPath(".//irs:IRS990PF/irs:OfficerDirTrstKeyEmplGrp/irs:CompensationAmt", namespaces=NAMESPACES),
        etree.XPath(".//irs:OfficerDirTrstKeyEmplGrp/irs:CompensationAmt", namespaces=NAMESPACES),
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
        etree.XPath(".//irs:TotalAssetsEOYAmt", namespaces=NAMESPACES),
    ],
    "return_data": [
        etree.XPath(".//irs:ReturnData", namespaces=NAMESPACES),
        etree.XPath(".//ReturnData", namespaces=NAMESPACES),
    ],
}

# Consolidated grant XPaths for all forms
GRANT_XPATHS = {
    "990EZ": [
        etree.XPath(".//irs:ReturnData/irs:IRS990EZ/irs:IRS990ScheduleF/irs:GrantsToOrgOutsideUSGrp", namespaces=NAMESPACES),
        etree.XPath(".//irs:ReturnData/irs:IRS990EZ/irs:IRS990ScheduleF/irs:GrantsToOrganizationsOutsideUS", namespaces=NAMESPACES),
        etree.XPath(".//irs:ReturnData/irs:IRS990EZ/irs:IRS990ScheduleF/irs:GrantsToOrgsOutsideUS", namespaces=NAMESPACES),
        etree.XPath(".//irs:ReturnData/irs:IRS990EZ/irs:IRS990ScheduleF/irs:ForeignIndividualsGrantsGrp", namespaces=NAMESPACES),
        etree.XPath(".//irs:ReturnData/irs:IRS990EZ/irs:IRS990ScheduleI/irs:RecipientTable", namespaces=NAMESPACES),
        etree.XPath(".//irs:ReturnData/irs:IRS990EZ/irs:IRS990ScheduleI/irs:GrantsOtherAsstToIndivInUSGrp", namespaces=NAMESPACES),
    ],
    "990": [
        etree.XPath(".//irs:ReturnData/irs:IRS990ScheduleF/irs:GrantsToOrgOutsideUSGrp", namespaces=NAMESPACES),
        etree.XPath(".//irs:ReturnData/irs:IRS990ScheduleF/irs:GrantsToOrganizationsOutsideUS", namespaces=NAMESPACES),
        etree.XPath(".//irs:ReturnData/irs:IRS990ScheduleF/irs:GrantsToOrgsOutsideUS", namespaces=NAMESPACES),
        etree.XPath(".//irs:ReturnData/irs:IRS990ScheduleF/irs:ForeignIndividualsGrantsGrp", namespaces=NAMESPACES),
        etree.XPath(".//irs:ReturnData/irs:IRS990ScheduleI/irs:RecipientTable", namespaces=NAMESPACES),
        etree.XPath(".//irs:ReturnData/irs:IRS990ScheduleI/irs:GrantsOtherAsstToIndivInUSGrp", namespaces=NAMESPACES),
    ],
    "990PF": [
        etree.XPath(".//irs:IRS990PF/irs:SupplementaryInformationGrp/irs:GrantOrContributionPdDurYrGrp", namespaces=NAMESPACES),
        etree.XPath(".//irs:IRS990PF/irs:SupplementaryInformationGrp", namespaces=NAMESPACES),
    ],
}

GRANT_EIN_XPATHS = [
    etree.XPath("irs:EIN | irs:RecipientEIN | irs:RecipientBusinessName/irs:EIN", namespaces=NAMESPACES),
]

GRANT_NAME_XPATHS = [
    etree.XPath("irs:RecipientNameBusiness | irs:RecipientBusinessName/irs:BusinessNameLine1Txt | irs:BusinessName/irs:BusinessNameLine1Txt", namespaces=NAMESPACES),
]

GRANT_AMOUNT_XPATHS = [
    etree.XPath("irs:CashGrantAmt | irs:TotalGrantOrContriPdDurYrAmt | irs:GrantOrContributionAmt | irs:Amount", namespaces=NAMESPACES),
]

GRANT_FOREIGN_ADDRESS_XPATH = etree.XPath("irs:RecipientForeignAddress", namespaces=NAMESPACES)

GRANT_COUNTRY_XPATH = etree.XPath("irs:RecipientForeignAddress/irs:CountryCd", namespaces=NAMESPACES)

GRANT_US_ADDRESS_XPATH = etree.XPath("irs:RecipientUSAddress/*", namespaces=NAMESPACES)

# New Schedule C XPaths for political contributions
SCHEDULE_C_XPATHS = {
    "990": [
        etree.XPath(".//irs:ReturnData/irs:IRS990ScheduleC/irs:PoliticalActivitiesGrp/irs:DirectExpendituresAmt", namespaces=NAMESPACES),
        etree.XPath(".//irs:ReturnData/irs:IRS990ScheduleC/irs:PoliticalCampaignActyInd", namespaces=NAMESPACES),
        etree.XPath(".//irs:ReturnData/irs:IRS990ScheduleC/irs:SupplementalInformationDetail", namespaces=NAMESPACES),
        etree.XPath(".//irs:ReturnData/irs:IRS990ScheduleC/irs:PoliticalExpendituresAmt", namespaces=NAMESPACES),
        etree.XPath(".//irs:ReturnData/irs:IRS990ScheduleC/irs:DirectPoliticalExpendituresAmt", namespaces=NAMESPACES),
    ],
    "990EZ": [
        etree.XPath(".//irs:ReturnData/irs:IRS990EZScheduleC/irs:PoliticalActivitiesGrp/irs:DirectExpendituresAmt", namespaces=NAMESPACES),
        etree.XPath(".//irs:ReturnData/irs:IRS990EZScheduleC/irs:PoliticalCampaignActyInd", namespaces=NAMESPACES),
        etree.XPath(".//irs:ReturnData/irs:IRS990EZScheduleC/irs:SupplementalInformationDetail", namespaces=NAMESPACES),
        etree.XPath(".//irs:ReturnData/irs:IRS990EZScheduleC/irs:PoliticalExpendituresAmt", namespaces=NAMESPACES),
        etree.XPath(".//irs:ReturnData/irs:IRS990EZScheduleC/irs:DirectPoliticalExpendituresAmt", namespaces=NAMESPACES),
    ],
    "990PF": [
        etree.XPath(".//irs:ReturnData/irs:IRS990PFScheduleC/irs:PoliticalActivitiesGrp/irs:DirectExpendituresAmt", namespaces=NAMESPACES),
        etree.XPath(".//irs:ReturnData/irs:IRS990PFScheduleC/irs:PoliticalCampaignActyInd", namespaces=NAMESPACES),
        etree.XPath(".//irs:ReturnData/irs:IRS990PFScheduleC/irs:SupplementalInformationDetail", namespaces=NAMESPACES),
        etree.XPath(".//irs:ReturnData/irs:IRS990PFScheduleC/irs:PoliticalExpendituresAmt", namespaces=NAMESPACES),
        etree.XPath(".//irs:ReturnData/irs:IRS990PFScheduleC/irs:DirectPoliticalExpendituresAmt", namespaces=NAMESPACES),
    ],
}

SCHEDULE_C_AMOUNT_XPATHS = [
    etree.XPath("irs:DirectExpendituresAmt | irs:PoliticalExpendituresAmt | irs:DirectPoliticalExpendituresAmt", namespaces=NAMESPACES),
]

SCHEDULE_C_RECIPIENT_XPATHS = [
    etree.XPath("irs:RecipientName | irs:RecipientBusinessName/irs:BusinessNameLine1Txt", namespaces=NAMESPACES),
]

SCHEDULE_C_EIN_XPATHS = [
    etree.XPath("irs:RecipientEIN", namespaces=NAMESPACES),
]