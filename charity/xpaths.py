XPATHS_BY_FORM = {
    "990": {
        "form_type": [
            ".//{http://www.irs.gov/efile}ReturnHeader/{http://www.irs.gov/efile}ReturnTypeCd",
            ".//ReturnHeader/ReturnTypeCd"
        ],
        "tax_year": [
            ".//{http://www.irs.gov/efile}ReturnHeader/{http://www.irs.gov/efile}TaxYr",
            ".//irs:ReturnHeader/irs:TaxYr",
            ".//{http://www.irs.gov/efile}ReturnHeader/TaxYr",
            ".//ReturnHeader/{http://www.irs.gov/efile}TaxYr",
            ".//ReturnHeader/TaxYr"
        ],
        "filer_ein": [
            ".//{http://www.irs.gov/efile}Filer/{http://www.irs.gov/efile}EIN",
            ".//Filer/EIN"
        ],
        "filer_name": [
            ".//{http://www.irs.gov/efile}Filer/{http://www.irs.gov/efile}BusinessName/{http://www.irs.gov/efile}BusinessNameLine1Txt",
            ".//Filer/BusinessName/BusinessNameLine1Txt",
            ".//{http://www.irs.gov/efile}Filer/{http://www.irs.gov/efile}BusinessName/{http://www.irs.gov/efile}BusinessNameLine1",
            ".//Filer/BusinessName/BusinessNameLine1"
        ],
        "receipt": [
            ".//{http://www.irs.gov/efile}GrossReceiptsAmt",
            ".//GrossReceiptsAmt",
            ".//{http://www.irs.gov/efile}TotalRevenueAmt",
            ".//TotalRevenueAmt",
            ".//{http://www.irs.gov/efile}CYTotalRevenueAmt",
            ".//CYTotalRevenueAmt"
        ],
        "govt_grants": [
            ".//{http://www.irs.gov/efile}GovernmentGrantsAmt",
            ".//GovernmentGrantsAmt"
        ],
        "contributions": [
            ".//{http://www.irs.gov/efile}AllOtherContributionsAmt",
            ".//AllOtherContributionsAmt"
        ],
        "total_exp": [
            ".//{http://www.irs.gov/efile}TotalFunctionalExpensesGrp/{http://www.irs.gov/efile}TotalAmt",
            ".//TotalFunctionalExpensesGrp/TotalAmt",
            ".//{http://www.irs.gov/efile}TotalExpensesAmt",
            ".//TotalExpensesAmt"
        ],
        "prog_exp": [
            ".//{http://www.irs.gov/efile}TotalProgramServiceExpensesAmt",
            ".//TotalProgramServiceExpensesAmt"
        ],
        "travel": [
            ".//{http://www.irs.gov/efile}TravelGrp/{http://www.irs.gov/efile}TotalAmt",
            ".//TravelGrp/TotalAmt"
        ],
        "conferences": [
            ".//{http://www.irs.gov/efile}ConferencesMeetingsGrp/{http://www.irs.gov/efile}TotalAmt",
            ".//ConferencesMeetingsGrp/TotalAmt"
        ],
        "officer_comp_elements": [
            ".//{http://www.irs.gov/efile}Form990PartVIISectionAGrp",
            ".//Form990PartVIISectionAGrp",
            ".//{http://www.irs.gov/efile}OfficerDirectorTrusteeEmplGrp",
            ".//OfficerDirectorTrusteeEmplGrp"
        ],
        "officer_comp_value": [
            "{http://www.irs.gov/efile}ReportableCompFromOrgAmt",
            "ReportableCompFromOrgAmt",
            "{http://www.irs.gov/efile}CompensationAmt",
            "CompensationAmt"
        ],
        "grant_elements_f": [
            ".//{http://www.irs.gov/efile}ReturnData/{http://www.irs.gov/efile}IRS990ScheduleF",
            ".//ReturnData/IRS990ScheduleF"
        ],
        "grant_sub_elements_f": [
            ".//{http://www.irs.gov/efile}GrantsToOrgOutsideUSGrp",
            ".//GrantsToOrgOutsideUSGrp",
            ".//{http://www.irs.gov/efile}GrantsToOrganizationsOutsideUS",
            ".//GrantsToOrganizationsOutsideUS",
            ".//{http://www.irs.gov/efile}GrantsToOrgsOutsideUS",
            ".//GrantsToOrgsOutsideUS",
            ".//{http://www.irs.gov/efile}ForeignIndividualsGrantsGrp",
            ".//ForeignIndividualsGrantsGrp"
        ],
        "grant_elements_i": [
            ".//{http://www.irs.gov/efile}ReturnData/{http://www.irs.gov/efile}IRS990ScheduleI",
            ".//ReturnData/IRS990ScheduleI"
        ],
        "grant_sub_elements_i": [
            ".//{http://www.irs.gov/efile}RecipientTable",
            ".//RecipientTable",
            ".//{http://www.irs.gov/efile}GrantsOtherAsstToIndivInUSGrp",
            ".//GrantsOtherAsstToIndivInUSGrp"
        ],
        "grant_value": [
            "{http://www.irs.gov/efile}CashGrantAmt",
            "CashGrantAmt"
        ],
        "foreign_exp_elements": [
            ".//{http://www.irs.gov/efile}ReturnData/{http://www.irs.gov/efile}IRS990ScheduleF",
            ".//ReturnData/IRS990ScheduleF"
        ],
        "foreign_exp_sub_elements": [
            ".//{http://www.irs.gov/efile}StmtOfActyOutsdUSGrp",
            ".//StmtOfActyOutsdUSGrp",
            ".//{http://www.irs.gov/efile}AccountActivitiesOutsideUSGrp",
            ".//AccountActivitiesOutsideUSGrp"
        ],
        "foreign_exp_value": [
            "{http://www.irs.gov/efile}RegionTotalExpendituresAmt",
            "RegionTotalExpendituresAmt"
        ],
        "org_type": [
            ".//{http://www.irs.gov/efile}Organization501cInd",
            ".//Organization501cInd",
            ".//{http://www.irs.gov/efile}Organization501c3Ind",
            ".//Organization501c3Ind",
            ".//{http://www.irs.gov/efile}Organization4947a1NotPFInd",
            ".//Organization4947a1NotPFInd"
        ],
        "foreign_office": [
            ".//{http://www.irs.gov/efile}ForeignOfficeInd",
            ".//ForeignOfficeInd"
        ],
        "total_assets": [
            ".//{http://www.irs.gov/efile}TotalAssetsEOYAmt",
            ".//TotalAssetsEOYAmt",
            ".//{http://www.irs.gov/efile}TotalAssetsAmt",
            ".//TotalAssetsAmt"
        ]
    },
    "990EZ": {
        "form_type": [
            ".//{http://www.irs.gov/efile}ReturnHeader/{http://www.irs.gov/efile}ReturnTypeCd",
            ".//ReturnHeader/ReturnTypeCd"
        ],
        "tax_year": [
            ".//{http://www.irs.gov/efile}ReturnHeader/{http://www.irs.gov/efile}TaxYr",
            ".//irs:ReturnHeader/irs:TaxYr",
            ".//{http://www.irs.gov/efile}ReturnHeader/TaxYr",
            ".//ReturnHeader/{http://www.irs.gov/efile}TaxYr",
            ".//ReturnHeader/TaxYr"
        ],
        "filer_ein": [
            ".//{http://www.irs.gov/efile}Filer/{http://www.irs.gov/efile}EIN",
            ".//Filer/EIN"
        ],
        "filer_name": [
            ".//{http://www.irs.gov/efile}Filer/{http://www.irs.gov/efile}BusinessName/{http://www.irs.gov/efile}BusinessNameLine1Txt",
            ".//Filer/BusinessName/BusinessNameLine1Txt",
            ".//{http://www.irs.gov/efile}Filer/{http://www.irs.gov/efile}BusinessName/{http://www.irs.gov/efile}BusinessNameLine1",
            ".//Filer/BusinessName/BusinessNameLine1"
        ],
        "receipt": [
            ".//{http://www.irs.gov/efile}GrossReceiptsAmt",
            ".//GrossReceiptsAmt",
            ".//{http://www.irs.gov/efile}TotalRevenueAmt",
            ".//TotalRevenueAmt"
        ],
        "govt_grants": [
            ".//{http://www.irs.gov/efile}GovernmentGrantsAmt",
            ".//GovernmentGrantsAmt"
        ],
        "contributions": [
            ".//{http://www.irs.gov/efile}AllOtherContributionsAmt",
            ".//AllOtherContributionsAmt"
        ],
        "total_exp": [
            ".//{http://www.irs.gov/efile}TotalExpensesAmt",
            ".//TotalExpensesAmt"
        ],
        "prog_exp": [
            ".//{http://www.irs.gov/efile}TotalProgramServiceExpensesAmt",
            ".//TotalProgramServiceExpensesAmt"
        ],
        "travel": [
            ".//{http://www.irs.gov/efile}IRS990ScheduleO/{http://www.irs.gov/efile}SupplementalInformationDetail",
            ".//IRS990ScheduleO/SupplementalInformationDetail"
        ],
        "conferences": [
            ".//{http://www.irs.gov/efile}IRS990ScheduleO/{http://www.irs.gov/efile}SupplementalInformationDetail",
            ".//IRS990ScheduleO/SupplementalInformationDetail"
        ],
        "schedule_o_value": [
            "{http://www.irs.gov/efile}ExplanationTxt",
            "ExplanationTxt"
        ],
        "officer_comp_elements": [
            ".//{http://www.irs.gov/efile}OfficerDirectorTrusteeEmplGrp",
            ".//OfficerDirectorTrusteeEmplGrp"
        ],
        "officer_comp_value": [
            "{http://www.irs.gov/efile}CompensationAmt",
            "CompensationAmt"
        ],
        "grant_elements_f": [
            ".//{http://www.irs.gov/efile}ReturnData/{http://www.irs.gov/efile}IRS990EZ/{http://www.irs.gov/efile}IRS990ScheduleF",
            ".//ReturnData/IRS990EZ/IRS990ScheduleF"
        ],
        "grant_sub_elements_f": [
            ".//{http://www.irs.gov/efile}GrantsToOrgOutsideUSGrp",
            ".//GrantsToOrgOutsideUSGrp",
            ".//{http://www.irs.gov/efile}GrantsToOrganizationsOutsideUS",
            ".//GrantsToOrganizationsOutsideUS",
            ".//{http://www.irs.gov/efile}GrantsToOrgsOutsideUS",
            ".//GrantsToOrgsOutsideUS",
            ".//{http://www.irs.gov/efile}ForeignIndividualsGrantsGrp",
            ".//ForeignIndividualsGrantsGrp"
        ],
        "grant_elements_i": [
            ".//{http://www.irs.gov/efile}ReturnData/{http://www.irs.gov/efile}IRS990EZ/{http://www.irs.gov/efile}IRS990ScheduleI",
            ".//ReturnData/IRS990EZ/IRS990ScheduleI"
        ],
        "grant_sub_elements_i": [
            ".//{http://www.irs.gov/efile}RecipientTable",
            ".//RecipientTable",
            ".//{http://www.irs.gov/efile}GrantsOtherAsstToIndivInUSGrp",
            ".//GrantsOtherAsstToIndivInUSGrp"
        ],
        "grant_elements_o": [
            ".//{http://www.irs.gov/efile}IRS990ScheduleO/{http://www.irs.gov/efile}SupplementalInformationDetail",
            ".//IRS990ScheduleO/SupplementalInformationDetail"
        ],
        "grant_value": [
            "{http://www.irs.gov/efile}CashGrantAmt",
            "CashGrantAmt"
        ],
        "foreign_exp_elements": [
            ".//{http://www.irs.gov/efile}ReturnData/{http://www.irs.gov/efile}IRS990EZ/{http://www.irs.gov/efile}IRS990ScheduleF",
            ".//ReturnData/IRS990EZ/IRS990ScheduleF"
        ],
        "foreign_exp_sub_elements": [
            ".//{http://www.irs.gov/efile}StmtOfActyOutsdUSGrp",
            ".//StmtOfActyOutsdUSGrp",
            ".//{http://www.irs.gov/efile}AccountActivitiesOutsideUSGrp",
            ".//AccountActivitiesOutsideUSGrp"
        ],
        "foreign_exp_value": [
            "{http://www.irs.gov/efile}RegionTotalExpendituresAmt",
            "RegionTotalExpendituresAmt"
        ],
        "org_type": [
            ".//{http://www.irs.gov/efile}Organization501c3Ind",
            ".//Organization501c3Ind",
            ".//{http://www.irs.gov/efile}Organization4947a1NotPFInd",
            ".//Organization4947a1NotPFInd"
        ],
        "foreign_office": [
            ".//{http://www.irs.gov/efile}ForeignOfficeInd",
            ".//ForeignOfficeInd"
        ],
        "total_assets": [
            ".//{http://www.irs.gov/efile}TotalAssetsEOYAmt",
            ".//TotalAssetsEOYAmt",
            ".//{http://www.irs.gov/efile}TotalAssetsAmt",
            ".//TotalAssetsAmt"
        ]
    },
    "990PF": {
        "form_type": [
            ".//{http://www.irs.gov/efile}ReturnHeader/{http://www.irs.gov/efile}ReturnTypeCd",
            ".//ReturnHeader/ReturnTypeCd"
        ],
        "tax_year": [
            ".//{http://www.irs.gov/efile}ReturnHeader/{http://www.irs.gov/efile}TaxYr",
            ".//irs:ReturnHeader/irs:TaxYr",
            ".//{http://www.irs.gov/efile}ReturnHeader/TaxYr",
            ".//ReturnHeader/{http://www.irs.gov/efile}TaxYr",
            ".//ReturnHeader/TaxYr"
        ],
        "filer_ein": [
            ".//{http://www.irs.gov/efile}Filer/{http://www.irs.gov/efile}EIN",
            ".//Filer/EIN"
        ],
        "filer_name": [
            ".//{http://www.irs.gov/efile}Filer/{http://www.irs.gov/efile}BusinessName/{http://www.irs.gov/efile}BusinessNameLine1Txt",
            ".//Filer/BusinessName/BusinessNameLine1Txt",
            ".//{http://www.irs.gov/efile}Filer/{http://www.irs.gov/efile}BusinessName/{http://www.irs.gov/efile}BusinessNameLine1",
            ".//Filer/BusinessName/BusinessNameLine1"
        ],
        "receipt": [
            ".//{http://www.irs.gov/efile}AnalysisOfRevenueAndExpenses/{http://www.irs.gov/efile}DividendsRevAndExpnssAmt",
            ".//AnalysisOfRevenueAndExpenses/DividendsRevAndExpnssAmt",
            ".//{http://www.irs.gov/efile}AnalysisOfRevenueAndExpenses/{http://www.irs.gov/efile}NetGainSaleAstRevAndExpnssAmt",
            ".//AnalysisOfRevenueAndExpenses/NetGainSaleAstRevAndExpnssAmt",
            ".//{http://www.irs.gov/efile}AnalysisOfRevenueAndExpenses/{http://www.irs.gov/efile}OtherIncomeRevAndExpnssAmt",
            ".//AnalysisOfRevenueAndExpenses/OtherIncomeRevAndExpnssAmt"
        ],
        "govt_grants": [],
        "contributions": [],
        "total_exp": [
            ".//{http://www.irs.gov/efile}AnalysisOfRevenueAndExpenses/{http://www.irs.gov/efile}TotalExpensesRevAndExpnssAmt",
            ".//AnalysisOfRevenueAndExpenses/TotalExpensesRevAndExpnssAmt"
        ],
        "prog_exp": [
            ".//{http://www.irs.gov/efile}AnalysisOfRevenueAndExpenses/{http://www.irs.gov/efile}ContriPaidDsbrsChrtblAmt",
            ".//AnalysisOfRevenueAndExpenses/ContriPaidDsbrsChrtblAmt"
        ],
        "travel": [
            ".//{http://www.irs.gov/efile}OtherExpensesSchedule/{http://www.irs.gov/efile}OtherExpensesScheduleGrp",
            ".//OtherExpensesSchedule/OtherExpensesScheduleGrp"
        ],
        "conferences": [
            ".//{http://www.irs.gov/efile}OtherExpensesSchedule/{http://www.irs.gov/efile}OtherExpensesScheduleGrp",
            ".//OtherExpensesSchedule/OtherExpensesScheduleGrp"
        ],
        "expense_value": [
            "{http://www.irs.gov/efile}RevenueAndExpensesPerBooksAmt",
            "RevenueAndExpensesPerBooksAmt"
        ],
        "expense_desc": [
            "{http://www.irs.gov/efile}Desc",
            "Desc"
        ],
        "officer_comp_elements": [
            ".//{http://www.irs.gov/efile}OfficerDirTrstKeyEmplGrp",
            ".//OfficerDirTrstKeyEmplGrp"
        ],
        "officer_comp_value": [
            "{http://www.irs.gov/efile}CompensationAmt",
            "CompensationAmt"
        ],
        "grants": [
            ".//{http://www.irs.gov/efile}SupplementaryInformationGrp/{http://www.irs.gov/efile}TotalGrantOrContriPdDurYrAmt",
            ".//SupplementaryInformationGrp/TotalGrantOrContriPdDurYrAmt"
        ],
        "foreign_exp": [],
        "org_type": [
            ".//{http://www.irs.gov/efile}Organization501c3ExemptPFInd",
            ".//Organization501c3ExemptPFInd",
            ".//{http://www.irs.gov/efile}Organization501c3TaxablePFInd",
            ".//Organization501c3TaxablePFInd",
            ".//{http://www.irs.gov/efile}Organization4947a1NotExemptCharitableTrustInd",
            ".//Organization4947a1NotExemptCharitableTrustInd",
            ".//{http://www.irs.gov/efile}Organization4947a1Ind",
            ".//Organization4947a1Ind",
            ".//{http://www.irs.gov/efile}Organization4947a1TrtdPFInd",
            ".//Organization4947a1TrtdPFInd"
        ],
        "foreign_office": [
            ".//{http://www.irs.gov/efile}ForeignOfficeInd",
            ".//ForeignOfficeInd"
        ],
        "total_assets": [
            ".//{http://www.irs.gov/efile}TotalAssetsEOYAmt",
            ".//TotalAssetsEOYAmt",
            ".//{http://www.irs.gov/efile}TotalAssetsAmt",
            ".//TotalAssetsAmt"
        ]
    }
}