XPATHS_990 = {
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
        ".//irs:IRS990/irs:GrossReceiptsAmt",
        ".//irs:GrossReceiptsAmt",
        ".//irs:IRS990/irs:TotalRevenueAmt",
        ".//irs:TotalRevenueAmt",
        ".//irs:IRS990/irs:CYTotalRevenueAmt",
        ".//irs:CYTotalRevenueAmt",
    ],
    "govt_grants": [
        ".//irs:IRS990/irs:GovernmentGrantsAmt",
        ".//irs:GovernmentGrantsAmt",
        ".//irs:IRS990/irs:GovtContriGrntAmt",
        ".//irs:GovtContriGrntAmt",
        ".//irs:ContributionsAndGrantsAmt",
    ],
    "contributions": [
        ".//irs:IRS990/irs:AllOtherContributionsAmt",
        ".//irs:AllOtherContributionsAmt",
        ".//irs:IRS990/irs:TotalContributionsAmt",
        ".//irs:TotalContributionsAmt",
    ],
    "total_exp": [
        ".//irs:IRS990/irs:TotalFunctionalExpensesGrp/irs:TotalAmt",
        ".//irs:TotalFunctionalExpensesGrp/irs:TotalAmt",
        ".//irs:IRS990/irs:TotalExpensesAmt",
        ".//irs:TotalExpensesAmt",
    ],
    "prog_exp": [
        ".//irs:IRS990/irs:TotalProgramServiceExpensesAmt",
        ".//irs:TotalProgramServiceExpensesAmt",
        ".//irs:ProgramServiceExpensesAmt",
    ],
    "travel": [
        ".//irs:IRS990/irs:TravelGrp/irs:TotalAmt",
        ".//irs:TravelGrp/irs:TotalAmt",
        ".//irs:TravelExpensesAmt",
    ],
    "conferences": [
        ".//irs:IRS990/irs:ConferencesMeetingsGrp/irs:TotalAmt",
        ".//irs:ConferencesMeetingsGrp/irs:TotalAmt",
    ],
    "officer_comp_elements": [
        ".//irs:IRS990/irs:Form990PartVIISectionAGrp",
        ".//irs:Form990PartVIISectionAGrp",
        ".//irs:IRS990/irs:OfficerDirectorTrusteeEmplGrp",
        ".//irs:OfficerDirectorTrusteeEmplGrp",
    ],
    "officer_comp_value": [
        "irs:ReportableCompFromOrgAmt",
        "ReportableCompFromOrgAmt",
        "irs:CompensationAmt",
        "CompensationAmt",
    ],
    "officer_comp": [
        ".//irs:IRS990/irs:Form990PartVIISectionAGrp/irs:ReportableCompFromOrgAmt",
        ".//irs:Form990PartVIISectionAGrp/irs:ReportableCompFromOrgAmt",
        ".//irs:IRS990/irs:OfficerDirectorTrusteeEmplGrp/irs:CompensationAmt",
        ".//irs:OfficerDirectorTrusteeEmplGrp/irs:CompensationAmt",
    ],
    "grants_to_others": [
        ".//irs:ReturnData/irs:IRS990ScheduleF/irs:GrantsToOrgOutsideUSGrp/irs:CashGrantAmt",
        ".//irs:ReturnData/irs:IRS990ScheduleF/irs:GrantsToOrganizationsOutsideUS/irs:CashGrantAmt",
        ".//irs:ReturnData/irs:IRS990ScheduleF/irs:GrantsToOrgsOutsideUS/irs:CashGrantAmt",
        ".//irs:ReturnData/irs:IRS990ScheduleF/irs:ForeignIndividualsGrantsGrp/irs:CashGrantAmt",
        ".//irs:ReturnData/irs:IRS990ScheduleI/irs:RecipientTable/irs:CashGrantAmt",
        ".//irs:ReturnData/irs:IRS990ScheduleI/irs:GrantsOtherAsstToIndivInUSGrp/irs:CashGrantAmt",
    ],
    "grant_elements_f": [
        ".//irs:ReturnData/irs:IRS990ScheduleF",
    ],
    "grant_sub_elements_f": [
        ".//irs:GrantsToOrgOutsideUSGrp",
        ".//irs:GrantsToOrganizationsOutsideUS",
        ".//irs:GrantsToOrgsOutsideUS",
        ".//irs:ForeignIndividualsGrantsGrp",
    ],
    "grant_elements_i": [
        ".//irs:ReturnData/irs:IRS990ScheduleI",
    ],
    "grant_sub_elements_i": [
        ".//irs:RecipientTable",
        ".//irs:GrantsOtherAsstToIndivInUSGrp",
    ],
    "grant_value": [
        "irs:CashGrantAmt",
    ],
    "foreign_expenses": [
        ".//irs:ReturnData/irs:IRS990ScheduleF/irs:StmtOfActyOutsdUSGrp/irs:RegionTotalExpendituresAmt",
        ".//irs:ReturnData/irs:IRS990ScheduleF/irs:AccountActivitiesOutsideUSGrp/irs:RegionTotalExpendituresAmt",
    ],
    "schedule_o_value": [
        ".//irs:ExplanationTxt",
        ".//irs:SupplementalInformationDetail/irs:ExplanationTxt",
        ".//irs:Form990ScheduleO/irs:Explanation"
    ],
    "foreign_exp_elements": [
        ".//irs:ReturnData/irs:IRS990ScheduleF",
    ],
    "foreign_exp_sub_elements": [
        ".//irs:StmtOfActyOutsdUSGrp",
        ".//irs:AccountActivitiesOutsideUSGrp",
    ],
    "foreign_exp_value": [
        "irs:RegionTotalExpendituresAmt",
    ],
    "org_type": [
        ".//irs:IRS990/irs:Organization501cInd",
        ".//irs:Organization501cInd",
        ".//irs:IRS990/irs:Organization501c3Ind",
        ".//irs:Organization501c3Ind",
        ".//irs:IRS990/irs:Organization501c4Ind",
        ".//irs:Organization501c4Ind",
        ".//irs:IRS990/irs:Organization501c5Ind",
        ".//irs:Organization501c5Ind",
        ".//irs:IRS990/irs:Organization501c6Ind",
        ".//irs:Organization501c6Ind",
        ".//irs:IRS990/irs:Organization4947a1NotPFInd",
        ".//irs:Organization4947a1NotPFInd",
        ".//irs:TaxExemptOrganizationInd",
    ],
    "foreign_office": [
        ".//irs:IRS990/irs:ForeignOfficeInd",
        ".//irs:ForeignOfficeInd",
        ".//irs:ForeignOfficeCountryCd",
        ".//irs:ForeignActivitiesInd",
    ],
    "total_assets": [
        ".//irs:IRS990/irs:TotalAssetsEOYAmt",
        ".//irs:TotalAssetsEOYAmt",
        ".//irs:IRS990/irs:TotalAssetsAmt",
        ".//irs:TotalAssetsAmt",
    ],
}
