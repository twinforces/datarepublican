XPATHS_BY_FORM = {
    "990": {
        "form_type": [
            ".//irs:ReturnHeader/irs:ReturnTypeCd",
            ".//ReturnHeader/ReturnTypeCd"
        ],
        "tax_year": [
            ".//irs:ReturnHeader/irs:TaxYr",
            ".//ReturnHeader/TaxYr"
        ],
        "filer_ein": [
            ".//irs:Filer/irs:EIN",
            ".//Filer/EIN"
        ],
        "filer_name": [
            ".//irs:Filer/irs:BusinessName/irs:BusinessNameLine1Txt",
            ".//Filer/BusinessName/BusinessNameLine1Txt"
        ],
        "receipt": [
            ".//irs:IRS990/irs:GrossReceiptsAmt",
            ".//GrossReceiptsAmt",
            ".//irs:IRS990/irs:TotalRevenueAmt",
            ".//TotalRevenueAmt",
            ".//irs:IRS990/irs:CYTotalRevenueAmt",
            ".//CYTotalRevenueAmt"
        ],
        "govt_grants": [
            ".//irs:IRS990/irs:GovernmentGrantsAmt",
            ".//GovernmentGrantsAmt"
        ],
        "contributions": [
            ".//irs:IRS990/irs:AllOtherContributionsAmt",
            ".//AllOtherContributionsAmt"
        ],
        "total_exp": [
            ".//irs:IRS990/irs:TotalFunctionalExpensesGrp/irs:TotalAmt",
            ".//TotalFunctionalExpensesGrp/TotalAmt",
            ".//irs:IRS990/irs:TotalExpensesAmt",
            ".//TotalExpensesAmt"
        ],
        "prog_exp": [
            ".//irs:IRS990/irs:TotalProgramServiceExpensesAmt",
            ".//TotalProgramServiceExpensesAmt"
        ],
        "travel": [
            ".//irs:IRS990/irs:TravelGrp/irs:TotalAmt",
            ".//TravelGrp/TotalAmt"
        ],
        "conferences": [
            ".//irs:IRS990/irs:ConferencesMeetingsGrp/irs:TotalAmt",
            ".//ConferencesMeetingsGrp/TotalAmt"
        ],
        "officer_comp_elements": [
            ".//irs:IRS990/irs:Form990PartVIISectionAGrp",
            ".//Form990PartVIISectionAGrp",
            ".//irs:IRS990/irs:OfficerDirectorTrusteeEmplGrp",
            ".//OfficerDirectorTrusteeEmplGrp"
        ],
        "officer_comp_value": [
            "irs:ReportableCompFromOrgAmt",
            "ReportableCompFromOrgAmt",
            "irs:CompensationAmt",
            "CompensationAmt"
        ],
        "officer_comp": [
            ".//irs:IRS990/irs:Form990PartVIISectionAGrp/irs:ReportableCompFromOrgAmt",
            ".//Form990PartVIISectionAGrp/ReportableCompFromOrgAmt",
            ".//irs:IRS990/irs:OfficerDirectorTrusteeEmplGrp/irs:CompensationAmt",
            ".//OfficerDirectorTrusteeEmplGrp/CompensationAmt"
        ],
        "grant_elements_f": [
            ".//irs:ReturnData/irs:IRS990ScheduleF",
            ".//ReturnData/IRS990ScheduleF"
        ],
        "grant_sub_elements_f": [
            ".//irs:GrantsToOrgOutsideUSGrp",
            ".//GrantsToOrgOutsideUSGrp",
            ".//irs:GrantsToOrganizationsOutsideUS",
            ".//GrantsToOrganizationsOutsideUS",
            ".//irs:GrantsToOrgsOutsideUS",
            ".//GrantsToOrgsOutsideUS",
            ".//irs:ForeignIndividualsGrantsGrp",
            ".//ForeignIndividualsGrantsGrp"
        ],
        "grant_elements_i": [
            ".//irs:ReturnData/irs:IRS990ScheduleI",
            ".//ReturnData/IRS990ScheduleI"
        ],
        "grant_sub_elements_i": [
            ".//irs:RecipientTable",
            ".//RecipientTable",
            ".//irs:GrantsOtherAsstToIndivInUSGrp",
            ".//GrantsOtherAsstToIndivInUSGrp"
        ],
        "grant_value": [
            "irs:CashGrantAmt",
            "CashGrantAmt"
        ],
        "foreign_exp_elements": [
            ".//irs:ReturnData/irs:IRS990ScheduleF",
            ".//ReturnData/IRS990ScheduleF"
        ],
        "foreign_exp_sub_elements": [
            ".//irs:StmtOfActyOutsdUSGrp",
            ".//StmtOfActyOutsdUSGrp",
            ".//irs:AccountActivitiesOutsideUSGrp",
            ".//AccountActivitiesOutsideUSGrp"
        ],
        "foreign_exp_value": [
            "irs:RegionTotalExpendituresAmt",
            "RegionTotalExpendituresAmt"
        ],
        "org_type": [
            ".//irs:IRS990/irs:Organization501cInd",
            ".//Organization501cInd",
            ".//irs:IRS990/irs:Organization501c3Ind",
            ".//Organization501c3Ind",
            ".//irs:IRS990/irs:Organization4947a1NotPFInd",
            ".//Organization4947a1NotPFInd"
        ],
        "foreign_office": [
            ".//irs:IRS990/irs:ForeignOfficeInd",
            ".//ForeignOfficeInd"
        ],
        "total_assets": [
            ".//irs:IRS990/irs:TotalAssetsEOYAmt",
            ".//TotalAssetsEOYAmt",
            ".//irs:IRS990/irs:TotalAssetsAmt",
            ".//TotalAssetsAmt"
        ]
    },
    "990EZ": {
        "form_type": [
            ".//irs:ReturnHeader/irs:ReturnTypeCd",
            ".//ReturnHeader/ReturnTypeCd"
        ],
        "tax_year": [
            ".//irs:ReturnHeader/irs:TaxYr",
            ".//ReturnHeader/TaxYr"
        ],
        "filer_ein": [
            ".//irs:Filer/irs:EIN",
            ".//Filer/EIN"
        ],
        "filer_name": [
            ".//irs:Filer/irs:BusinessName/irs:BusinessNameLine1Txt",
            ".//Filer/BusinessName/BusinessNameLine1Txt"
        ],
        "receipt": [
            ".//irs:IRS990EZ/irs:GrossReceiptsAmt",
            ".//GrossReceiptsAmt",
            ".//irs:IRS990EZ/irs:TotalRevenueAmt",
            ".//TotalRevenueAmt"
        ],
        "govt_grants": [
            ".//irs:IRS990EZ/irs:GovernmentGrantsAmt",
            ".//GovernmentGrantsAmt"
        ],
        "contributions": [
            ".//irs:IRS990EZ/irs:AllOtherContributionsAmt",
            ".//AllOtherContributionsAmt"
        ],
        "total_exp": [
            ".//irs:IRS990EZ/irs:TotalExpensesAmt",
            ".//TotalExpensesAmt"
        ],
        "prog_exp": [
            ".//irs:IRS990EZ/irs:TotalProgramServiceExpensesAmt",
            ".//TotalProgramServiceExpensesAmt"
        ],
        "travel": [
            ".//irs:IRS990ScheduleO/irs:SupplementalInformationDetail",
            ".//IRS990ScheduleO/SupplementalInformationDetail"
        ],
        "conferences": [
            ".//irs:IRS990ScheduleO/irs:SupplementalInformationDetail",
            ".//IRS990ScheduleO/SupplementalInformationDetail"
        ],
        "schedule_o_value": [
            "irs:ExplanationTxt",
            "ExplanationTxt"
        ],
        "officer_comp_elements": [
            ".//irs:IRS990EZ/irs:OfficerDirectorTrusteeEmplGrp",
            ".//OfficerDirectorTrusteeEmplGrp"
        ],
        "officer_comp_value": [
            "irs:CompensationAmt",
            "CompensationAmt"
        ],
        "officer_comp": [
            ".//irs:IRS990EZ/irs:OfficerDirectorTrusteeEmplGrp/irs:CompensationAmt",
            ".//OfficerDirectorTrusteeEmplGrp/CompensationAmt"
        ],
        "grant_elements_f": [
            ".//irs:ReturnData/irs:IRS990EZ/irs:IRS990ScheduleF",
            ".//ReturnData/IRS990EZ/IRS990ScheduleF"
        ],
        "grant_sub_elements_f": [
            ".//irs:GrantsToOrgOutsideUSGrp",
            ".//GrantsToOrgOutsideUSGrp",
            ".//irs:GrantsToOrganizationsOutsideUS",
            ".//GrantsToOrganizationsOutsideUS",
            ".//irs:GrantsToOrgsOutsideUS",
            ".//GrantsToOrgsOutsideUS",
            ".//irs:ForeignIndividualsGrantsGrp",
            ".//ForeignIndividualsGrantsGrp"
        ],
        "grant_elements_i": [
            ".//irs:ReturnData/irs:IRS990EZ/irs:IRS990ScheduleI",
            ".//ReturnData/IRS990EZ/IRS990ScheduleI"
        ],
        "grant_sub_elements_i": [
            ".//irs:RecipientTable",
            ".//RecipientTable",
            ".//irs:GrantsOtherAsstToIndivInUSGrp",
            ".//GrantsOtherAsstToIndivInUSGrp"
        ],
        "grant_elements_o": [
            ".//irs:IRS990ScheduleO/irs:SupplementalInformationDetail",
            ".//IRS990ScheduleO/SupplementalInformationDetail"
        ],
        "grant_value": [
            "irs:CashGrantAmt",
            "CashGrantAmt"
        ],
        "foreign_exp_elements": [
            ".//irs:ReturnData/irs:IRS990EZ/irs:IRS990ScheduleF",
            ".//ReturnData/IRS990EZ/IRS990ScheduleF"
        ],
        "foreign_exp_sub_elements": [
            ".//irs:StmtOfActyOutsdUSGrp",
            ".//StmtOfActyOutsdUSGrp",
            ".//irs:AccountActivitiesOutsideUSGrp",
            ".//AccountActivitiesOutsideUSGrp"
        ],
        "foreign_exp_value": [
            "irs:RegionTotalExpendituresAmt",
            "RegionTotalExpendituresAmt"
        ],
        "org_type": [
            ".//irs:IRS990EZ/irs:Organization501c3Ind",
            ".//Organization501c3Ind",
            ".//irs:IRS990EZ/irs:Organization4947a1NotPFInd",
            ".//Organization4947a1NotPFInd"
        ],
        "foreign_office": [
            ".//irs:IRS990EZ/irs:ForeignOfficeInd",
            ".//ForeignOfficeInd"
        ],
        "total_assets": [
            ".//irs:IRS990EZ/irs:TotalAssetsEOYAmt",
            ".//TotalAssetsEOYAmt",
            ".//irs:IRS990EZ/irs:TotalAssetsAmt",
            ".//TotalAssetsAmt"
        ]
    },
    "990PF": {
        "form_type": [
            ".//irs:ReturnHeader/irs:ReturnTypeCd",
            ".//ReturnHeader/ReturnTypeCd"
        ],
        "tax_year": [
            ".//irs:ReturnHeader/irs:TaxYr",
            ".//ReturnHeader/TaxYr"
        ],
        "filer_ein": [
            ".//irs:Filer/irs:EIN",
            ".//Filer/EIN"
        ],
        "filer_name": [
            ".//irs:Filer/irs:BusinessName/irs:BusinessNameLine1Txt",
            ".//Filer/BusinessName/BusinessNameLine1Txt"
        ],
        "receipt": [
            ".//irs:IRS990PF/irs:AnalysisOfRevenueAndExpenses/irs:DividendsRevAndExpnssAmt",
            ".//AnalysisOfRevenueAndExpenses/DividendsRevAndExpnssAmt",
            ".//irs:IRS990PF/irs:AnalysisOfRevenueAndExpenses/irs:NetGainSaleAstRevAndExpnssAmt",
            ".//AnalysisOfRevenueAndExpenses/NetGainSaleAstRevAndExpnssAmt",
            ".//irs:IRS990PF/irs:AnalysisOfRevenueAndExpenses/irs:OtherIncomeRevAndExpnssAmt",
            ".//AnalysisOfRevenueAndExpenses/OtherIncomeRevAndExpnssAmt"
        ],
        "govt_grants": [],
        "contributions": [],
        "total_exp": [
            ".//irs:IRS990PF/irs:AnalysisOfRevenueAndExpenses/irs:TotalExpensesRevAndExpnssAmt",
            ".//AnalysisOfRevenueAndExpenses/TotalExpensesRevAndExpnssAmt"
        ],
        "prog_exp": [
            ".//irs:IRS990PF/irs:AnalysisOfRevenueAndExpenses/irs:ContriPaidDsbrsChrtblAmt",
            ".//AnalysisOfRevenueAndExpenses/ContriPaidDsbrsChrtblAmt"
        ],
        "travel": [
            ".//irs:IRS990PF/irs:OtherExpensesSchedule/irs:OtherExpensesScheduleGrp",
            ".//OtherExpensesSchedule/OtherExpensesScheduleGrp"
        ],
        "conferences": [
            ".//irs:IRS990PF/irs:OtherExpensesSchedule/irs:OtherExpensesScheduleGrp",
            ".//OtherExpensesSchedule/OtherExpensesScheduleGrp"
        ],
        "expense_value": [
            "irs:RevenueAndExpensesPerBooksAmt",
            "RevenueAndExpensesPerBooksAmt"
        ],
        "expense_desc": [
            "irs:Desc",
            "Desc"
        ],
        "officer_comp_elements": [
            ".//irs:IRS990PF/irs:OfficerDirTrstKeyEmplGrp",
            ".//OfficerDirTrstKeyEmplGrp"
        ],
        "officer_comp_value": [
            "irs:CompensationAmt",
            "CompensationAmt"
        ],
        "officer_comp": [
            ".//irs:IRS990PF/irs:OfficerDirTrstKeyEmplGrp/irs:CompensationAmt",
            ".//OfficerDirTrstKeyEmplGrp/CompensationAmt"
        ],
        "grants": [
            ".//irs:IRS990PF/irs:SupplementaryInformationGrp/irs:TotalGrantOrContriPdDurYrAmt",
            ".//SupplementaryInformationGrp/TotalGrantOrContriPdDurYrAmt"
        ],
        "foreign_expenses": [
            ".//irs:ReturnData/irs:IRS990ScheduleF",
            ".//ReturnData/IRS990ScheduleF"
        ],
        "org_type": [
            ".//irs:IRS990PF/irs:Organization501c3ExemptPFInd",
            ".//Organization501c3ExemptPFInd",
            ".//irs:IRS990PF/irs:Organization501c3TaxablePFInd",
            ".//Organization501c3TaxablePFInd",
            ".//irs:IRS990PF/irs:Organization4947a1NotExemptCharitableTrustInd",
            ".//Organization4947a1NotExemptCharitableTrustInd",
            ".//irs:IRS990PF/irs:Organization4947a1Ind",
            ".//Organization4947a1Ind",
            ".//irs:IRS990PF/irs:Organization4947a1TrtdPFInd",
            ".//Organization4947a1TrtdPFInd"
        ],
        "foreign_office": [
            ".//irs:IRS990PF/irs:ForeignOfficeInd",
            ".//ForeignOfficeInd"
        ],
        "total_assets": [
            ".//irs:IRS990PF/irs:TotalAssetsEOYAmt",
            ".//TotalAssetsEOYAmt",
            ".//irs:IRS990PF/irs:TotalAssetsAmt",
            ".//TotalAssetsAmt"
        ]
    }
}