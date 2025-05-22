XPATHS_990PF = {
    "form_type": [
        ".//irs:ReturnHeader/irs:ReturnTypeCd",
        ".//ReturnHeader/ReturnTypeCd",
    ],
    "tax_year": [
        ".//irs:ReturnHeader/irs:TaxYr",
        ".//ReturnHeader/TaxYr",
    ],
    "filer_ein": [
        ".//irs:Filer/irs:EIN",
        ".//Filer/EIN",
    ],
    "filer_name": [
        ".//irs:Filer/irs:BusinessName/irs:BusinessNameLine1Txt",
        ".//Filer/BusinessName/BusinessNameLine1Txt",
    ],
    "receipt": [
        ".//irs:IRS990PF/irs:AnalysisOfRevenueAndExpenses/irs:DividendsRevAndExpnssAmt",
        ".//irs:AnalysisOfRevenueAndExpenses/irs:DividendsRevAndExpnssAmt",
        ".//irs:IRS990PF/irs:AnalysisOfRevenueAndExpenses/irs:NetGainSaleAstRevAndExpnssAmt",
        ".//irs:AnalysisOfRevenueAndExpenses/irs:NetGainSaleAstRevAndExpnssAmt",
        ".//irs:IRS990PF/irs:AnalysisOfRevenueAndExpenses/irs:OtherIncomeRevAndExpnssAmt",
        ".//irs:AnalysisOfRevenueAndExpenses/irs:OtherIncomeRevAndExpnssAmt",
    ],
    "govt_grants": [
        ".//irs:IRS990PF/irs:AnalysisOfRevenueAndExpenses/irs:GovtContriGrntAmt",
        ".//irs:AnalysisOfRevenueAndExpenses/irs:GovtContriGrntAmt",
        ".//irs:GovtContriGrntAmt",
    ],
    "contributions": [
        ".//irs:IRS990PF/irs:AnalysisOfRevenueAndExpenses/irs:ContributionsReceivedAmt",
        ".//irs:AnalysisOfRevenueAndExpenses/irs:ContributionsReceivedAmt",
        ".//irs:ContributionsReceivedAmt",
    ],
    "total_exp": [
        ".//irs:IRS990PF/irs:AnalysisOfRevenueAndExpenses/irs:TotalExpensesRevAndExpnssAmt",
        ".//irs:AnalysisOfRevenueAndExpenses/irs:TotalExpensesRevAndExpnssAmt",
        ".//irs:TotalExpensesRevAndExpnssAmt",
    ],
    "prog_exp": [
        ".//irs:IRS990PF/irs:AnalysisOfRevenueAndExpenses/irs:ContriPaidDsbrsChrtblAmt",
        ".//irs:AnalysisOfRevenueAndExpenses/irs:ContriPaidDsbrsChrtblAmt",
        ".//irs:ContriPaidDsbrsChrtblAmt",
    ],
    "travel": [
        ".//irs:IRS990PF/irs:OtherExpensesSchedule/irs:OtherExpensesScheduleGrp",
        ".//irs:OtherExpensesSchedule/irs:OtherExpensesScheduleGrp",
    ],
    "conferences": [
        ".//irs:IRS990PF/irs:OtherExpensesSchedule/irs:OtherExpensesScheduleGrp",
        ".//irs:OtherExpensesSchedule/irs:OtherExpensesScheduleGrp",
    ],
    "expense_value": [
        "irs:RevenueAndExpensesPerBooksAmt",
    ],
    "expense_desc": [
        "irs:Desc",
    ],
    "officer_comp_elements": [
        ".//irs:IRS990PF/irs:OfficerDirTrstKeyEmplGrp",
        ".//irs:OfficerDirTrstKeyEmplGrp",
    ],
    "officer_comp_value": [
        "irs:CompensationAmt",
    ],
    "officer_comp": [
        ".//irs:IRS990PF/irs:OfficerDirTrstKeyEmplGrp/irs:CompensationAmt",
        ".//irs:OfficerDirTrstKeyEmplGrp/irs:CompensationAmt",
    ],
    "grants_to_others": [
        ".//irs:IRS990PF/irs:SupplementaryInformationGrp/irs:TotalGrantOrContriPdDurYrAmt",
        ".//irs:SupplementaryInformationGrp/irs:TotalGrantOrContriPdDurYrAmt",
        ".//irs:TotalGrantOrContriPdDurYrAmt",
    ],
    "foreign_expenses": [
        ".//irs:ReturnData/irs:IRS990ScheduleF/irs:StmtOfActyOutsdUSGrp/irs:RegionTotalExpendituresAmt",
        ".//irs:ReturnData/irs:IRS990ScheduleF/irs:AccountActivitiesOutsideUSGrp/irs:RegionTotalExpendituresAmt",
    ],
    "org_type": [
        ".//irs:IRS990PF/irs:Organization501c3ExemptPFInd",
        ".//irs:Organization501c3ExemptPFInd",
        ".//irs:IRS990PF/irs:Organization501c3TaxablePFInd",
        ".//irs:Organization501c3TaxablePFInd",
        ".//irs:IRS990PF/irs:Organization4947a1NotExemptCharitableTrustInd",
        ".//irs:Organization4947a1NotExemptCharitableTrustInd",
        ".//irs:IRS990PF/irs:Organization4947a1Ind",
        ".//irs:Organization4947a1Ind",
        ".//irs:IRS990PF/irs:Organization4947a1TrtdPFInd",
        ".//irs:Organization4947a1TrtdPFInd",
    ],
    "foreign_office": [
        ".//irs:IRS990PF/irs:ForeignOfficeInd",
        ".//irs:ForeignOfficeInd",
        ".//irs:ForeignOfficeCountryCd",
        ".//irs:ForeignActivitiesInd",
    ],
    "total_assets": [
        ".//irs:IRS990PF/irs:TotalAssetsEOYAmt",
        ".//irs:TotalAssetsEOYAmt",
        ".//irs:IRS990PF/irs:TotalAssetsAmt",
        ".//irs:TotalAssetsAmt",
    ],
}
