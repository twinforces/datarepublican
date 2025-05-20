XPATHS_990EZ = {
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
        ".//irs:GrossReceiptsAmt",
        ".//irs:IRS990EZ/irs:TotalRevenueAmt",
        ".//irs:TotalRevenueAmt"
    ],
    "govt_grants": [
        ".//irs:IRS990EZ/irs:GovernmentGrantsAmt",
        ".//irs:GovernmentGrantsAmt",
        ".//irs:IRS990EZ/irs:GovtContriGrntAmt",
        ".//irs:GovtContriGrntAmt"
    ],
    "contributions": [
        ".//irs:IRS990EZ/irs:AllOtherContributionsAmt",
        ".//irs:AllOtherContributionsAmt",
        ".//irs:IRS990EZ/irs:TotalContributionsAmt",
        ".//irs:TotalContributionsAmt"
    ],
    "total_exp": [
        ".//irs:IRS990EZ/irs:TotalExpensesAmt",
        ".//irs:TotalExpensesAmt"
    ],
    "prog_exp": [
        ".//irs:IRS990EZ/irs:TotalProgramServiceExpensesAmt",
        ".//irs:TotalProgramServiceExpensesAmt"
    ],
    "travel": [
        ".//irs:IRS990ScheduleO/irs:SupplementalInformationDetail",
        ".//irs:IRS990ScheduleO/irs:SupplementalInformationDetail"
    ],
    "conferences": [
        ".//irs:IRS990ScheduleO/irs:SupplementalInformationDetail",
        ".//irs:IRS990ScheduleO/irs:SupplementalInformationDetail"
    ],
    "schedule_o_value": [
        "irs:ExplanationTxt",
        "irs:ExplanationTxt"
    ],
    "officer_comp_elements": [
        ".//irs:IRS990EZ/irs:OfficerDirectorTrusteeEmplGrp",
        ".//irs:OfficerDirectorTrusteeEmplGrp"
    ],
    "officer_comp_value": [
        "irs:CompensationAmt",
        "irs:CompensationAmt"
    ],
    "officer_comp": [
        ".//irs:IRS990EZ/irs:OfficerDirectorTrusteeEmplGrp/irs:CompensationAmt",
        ".//irs:OfficerDirectorTrusteeEmplGrp/irs:CompensationAmt"
    ],
    "grants_to_others": [
        ".//irs:ReturnData/irs:IRS990EZ/irs:IRS990ScheduleF/irs:GrantsToOrgOutsideUSGrp/irs:CashGrantAmt",
        ".//irs:ReturnData/irs:IRS990EZ/irs:IRS990ScheduleF/irs:GrantsToOrganizationsOutsideUS/irs:CashGrantAmt",
        ".//irs:ReturnData/irs:IRS990EZ/irs:IRS990ScheduleF/irs:GrantsToOrgsOutsideUS/irs:CashGrantAmt",
        ".//irs:ReturnData/irs:IRS990EZ/irs:IRS990ScheduleF/irs:ForeignIndividualsGrantsGrp/irs:CashGrantAmt",
        ".//irs:ReturnData/irs:IRS990EZ/irs:IRS990ScheduleI/irs:RecipientTable/irs:CashGrantAmt",
        ".//irs:ReturnData/irs:IRS990EZ/irs:IRS990ScheduleI/irs:GrantsOtherAsstToIndivInUSGrp/irs:CashGrantAmt",
        ".//irs:IRS990ScheduleO/irs:SupplementalInformationDetail/irs:ExplanationTxt[contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'disbursement')]"
    ],
    "grant_elements_f": [
        ".//irs:ReturnData/irs:IRS990EZ/irs:IRS990ScheduleF",
        ".//irs:ReturnData/irs:IRS990EZ/irs:IRS990ScheduleF"
    ],
    "grant_sub_elements_f": [
        ".//irs:GrantsToOrgOutsideUSGrp",
        ".//irs:GrantsToOrgOutsideUSGrp",
        ".//irs:GrantsToOrganizationsOutsideUS",
        ".//irs:GrantsToOrganizationsOutsideUS",
        ".//irs:GrantsToOrgsOutsideUS",
        ".//irs:GrantsToOrgsOutsideUS",
        ".//irs:ForeignIndividualsGrantsGrp",
        ".//irs:ForeignIndividualsGrantsGrp"
    ],
    "grant_elements_i": [
        ".//irs:ReturnData/irs:IRS990EZ/irs:IRS990ScheduleI",
        ".//irs:ReturnData/irs:IRS990EZ/irs:IRS990ScheduleI"
    ],
    "grant_sub_elements_i": [
        ".//irs:RecipientTable",
        ".//irs:RecipientTable",
        ".//irs:GrantsOtherAsstToIndivInUSGrp",
        ".//irs:GrantsOtherAsstToIndivInUSGrp"
    ],
    "grant_elements_o": [
        ".//irs:IRS990ScheduleO/irs:SupplementalInformationDetail",
        ".//irs:IRS990ScheduleO/irs:SupplementalInformationDetail"
    ],
    "grant_value": [
        "irs:CashGrantAmt",
        "irs:CashGrantAmt"
    ],
    "foreign_expenses": [
        ".//irs:ReturnData/irs:IRS990EZ/irs:IRS990ScheduleF/irs:StmtOfActyOutsdUSGrp/irs:RegionTotalExpendituresAmt",
        ".//irs:ReturnData/irs:IRS990EZ/irs:IRS990ScheduleF/irs:AccountActivitiesOutsideUSGrp/irs:RegionTotalExpendituresAmt"
    ],
    "foreign_exp_elements": [
        ".//irs:ReturnData/irs:IRS990EZ/irs:IRS990ScheduleF",
        ".//irs:ReturnData/irs:IRS990EZ/irs:IRS990ScheduleF"
    ],
    "foreign_exp_sub_elements": [
        ".//irs:StmtOfActyOutsdUSGrp",
        ".//irs:StmtOfActyOutsdUSGrp",
        ".//irs:AccountActivitiesOutsideUSGrp",
        ".//irs:AccountActivitiesOutsideUSGrp"
    ],
    "foreign_exp_value": [
        "irs:RegionTotalExpendituresAmt",
        "irs:RegionTotalExpendituresAmt"
    ],
    "org_type": [
        ".//irs:IRS990EZ/irs:Organization501cInd",
        ".//irs:Organization501cInd",
        ".//irs:IRS990EZ/irs:Organization501c3Ind",
        ".//irs:Organization501c3Ind",
        ".//irs:IRS990EZ/irs:Organization501c4Ind",
        ".//irs:Organization501c4Ind",
        ".//irs:IRS990EZ/irs:Organization501c5Ind",
        ".//irs:Organization501c5Ind",
        ".//irs:IRS990EZ/irs:Organization501c6Ind",
        ".//irs:Organization501c6Ind",
        ".//irs:IRS990EZ/irs:Organization501c7Ind",
        ".//irs:Organization501c7Ind",
        ".//irs:IRS990EZ/irs:Organization501c8Ind",
        ".//irs:Organization501c8Ind",
        ".//irs:IRS990EZ/irs:Organization501c9Ind",
        ".//irs:Organization501c9Ind",
        ".//irs:IRS990EZ/irs:Organization501c10Ind",
        ".//irs:Organization501c10Ind",
        ".//irs:IRS990EZ/irs:Organization501c19Ind",
        ".//irs:Organization501c19Ind",
        ".//irs:IRS990EZ/irs:Organization4947a1NotPFInd",
        ".//irs:Organization4947a1NotPFInd",
        ".//irs:TaxExemptOrganizationInd",
        ".//irs:ExemptOrganizationInd",
        ".//irs:TaxExemptStatus",
		".//irs:TypeOfOrganizationInd",
		".//irs:ReturnHeader/irs:TaxExemptOrganizationCd",
		".//irs:ReturnHeader/irs:ExemptStatusCd"
    ],
    "foreign_office": [
        ".//irs:IRS990EZ/irs:ForeignOfficeInd",
        ".//irs:ForeignOfficeInd",
        ".//irs:ForeignOfficeCountryCd",
        ".//irs:ForeignActivitiesInd"
    ],
    "total_assets": [
        ".//irs:IRS990EZ/irs:TotalAssetsEOYAmt",
        ".//irs:TotalAssetsEOYAmt",
        ".//irs:IRS990EZ/irs:TotalAssetsAmt",
        ".//irs:TotalAssetsAmt"
    ]
}