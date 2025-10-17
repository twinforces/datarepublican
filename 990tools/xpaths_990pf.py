from lxml import etree

NAMESPACES = {'irs': 'http://www.irs.gov/efile'}

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
    "business_name_line1": [
        etree.XPath(".//irs:Filer/irs:BusinessName/irs:BusinessNameLine1Txt", namespaces=NAMESPACES),
        etree.XPath(".//Filer/BusinessName/BusinessNameLine1Txt", namespaces=NAMESPACES),
    ],
    "business_name_line2": [
        etree.XPath(".//irs:Filer/irs:BusinessName/irs:BusinessNameLine2Txt", namespaces=NAMESPACES),
        etree.XPath(".//Filer/BusinessName/BusinessNameLine2Txt", namespaces=NAMESPACES),
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