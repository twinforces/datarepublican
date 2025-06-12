/**
 * charityTypes.js - Object of IRS 501(c) and 4947(a)(1) organization types.
 *
 * This module defines a constant object ORGANIZATION_TYPES with details for each
 * 501(c)(1) through 501(c)(29) and 4947(a)(1) organization type, including short
 * description, purpose, key restrictions, and donations deductibility. Data is
 * sourced from IRS Publication 557 and web references, adapted from
 * 501c_organization_types.md for use in JavaScript scripts processing nonprofit data.
 *
 * @example
 * const { ORGANIZATION_TYPES } = require('./charityTypes');
 * const orgInfo = ORGANIZATION_TYPES['501(c)(3)'];
 * console.log(orgInfo.shortDescription); // Output: Charities
 */
const ORGANIZATION_TYPES = {
  "501(c)(1)": {
    shortDescription: "Federal Entities",
    purpose:
      "Corporations organized under an Act of Congress (e.g., Federal Credit Unions, Federal Reserve Banks). Serve public purposes like banking or housing.",
    keyRestrictions:
      "No Form 1024, 990 exempt. Contributions deductible only for public purposes. No Grift.",
    donations: "Yes (public purposes)",
  },
  "501(c)(2)": {
    shortDescription: "Property Holding",
    purpose:
      "Title-holding corporations for exempt organizations. Hold property and turn over income (less expenses) to a tax-exempt parent organization.",
    keyRestrictions:
      "Income must be paid to exempt parent. Limited to property management. No Grift. File Form 1024 once, 990 annually.",
    donations: "No",
  },
  "501(c)(3)": {
    shortDescription: "Charities",
    purpose:
      "Charitable, religious, educational, scientific, literary organizations, or those testing for public safety, fostering amateur sports, or preventing cruelty to children/animals. Includes public charities (e.g., CHAI) and private foundations.",
    keyRestrictions:
      "No Grift. No substantial lobbying (501(h) election allows limited lobbying). No political campaign activity. File Form 1023 once, 990 annually.",
    donations: "Yes",
  },
  "501(c)(4)": {
    shortDescription: "Civic Welfare",
    purpose:
      "Civic leagues or social welfare organizations promoting community welfare (e.g., homeowners associations, volunteer fire companies).",
    keyRestrictions:
      "No Grift. Unlimited lobbying if related to purpose. Contributions not tax-deductible unless for specific purposes (e.g., fire departments). File Form 1024 once, 990 annually.",
    donations: "No (except specific cases)",
  },
  "501(c)(5)": {
    shortDescription: "Labor/Agriculture",
    purpose:
      "Labor, agricultural, or horticultural organizations (e.g., unions, farm bureaus). Promote worker rights or agricultural interests.",
    keyRestrictions:
      "No Grift. Lobbying allowed if related to purpose. Contributions not deductible. File Form 1024 once, 990 annually.",
    donations: "No",
  },
  "501(c)(6)": {
    shortDescription: "Business Leagues",
    purpose:
      "Business leagues, chambers of commerce, or professional associations (e.g., American Solar Grazing Association). Improve business conditions.",
    keyRestrictions:
      "No Grift. Unlimited lobbying for business interests. Contributions deductible as business expenses. File Form 1024 once, 990 annually.",
    donations: "No (business expense)",
  },
  "501(c)(7)": {
    shortDescription: "Social Clubs",
    purpose:
      "Social or recreational clubs (e.g., country clubs, fraternities). Provide leisure activities for members.",
    keyRestrictions:
      "Non-discriminatory policy (except religion). Minimal public use (<$2,500/year or <5% receipts). Contributions not deductible. File Form 1024 once, 990 annually.",
    donations: "No",
  },
  "501(c)(8)": {
    shortDescription: "Fraternal Benefits",
    purpose:
      "Fraternal beneficiary societies operating under a lodge system, providing life, sickness, or other benefits (e.g., Elks).",
    keyRestrictions:
      "Must offer insurance/benefits. Non-discriminatory except for religion. Contributions not deductible. File Form 1024 once, 990 annually.",
    donations: "No",
  },
  "501(c)(9)": {
    shortDescription: "Employee Benefits",
    purpose:
      "Voluntary employees’ beneficiary associations providing life, sickness, or accident benefits to members (e.g., labor union benefits).",
    keyRestrictions:
      "Membership voluntary, tied to employer/union. No discrimination in benefits. Contributions not deductible. File Form 1024 once, 990 annually.",
    donations: "No",
  },
  "501(c)(10)": {
    shortDescription: "Fraternal Charity",
    purpose:
      "Domestic fraternal societies without insurance, devoting earnings to charitable, religious, or educational purposes (e.g., Masons).",
    keyRestrictions:
      "No insurance benefits. Funds for exempt purposes. Contributions not deductible. File Form 1024 once, 990 annually.",
    donations: "No",
  },
  "501(c)(11)": {
    shortDescription: "Teacher Pensions",
    purpose:
      "Teachers’ retirement fund associations. Pay retirement benefits to local teachers/school employees.",
    keyRestrictions:
      "Funded by taxes, donations, investments. Local scope. Contributions not deductible. File Form 1024 once, 990 annually.",
    donations: "No",
  },
  "501(c)(12)": {
    shortDescription: "Mutual Utilities",
    purpose:
      "Local benevolent life insurance associations, mutual irrigation, or telephone companies. Serve members’ needs.",
    keyRestrictions:
      "85%+ income from members for losses/expenses. Contributions not deductible. File Form 1024 once, 990 annually.",
    donations: "No",
  },
  "501(c)(13)": {
    shortDescription: "Cemeteries",
    purpose: "Cemetery companies operated for members or charitable purposes.",
    keyRestrictions:
      "No private profit. Funds for maintenance. Contributions deductible if charitable. File Form 1024 once, 990 annually.",
    donations: "Yes (if charitable)",
  },
  "501(c)(14)": {
    shortDescription: "Credit Unions",
    purpose:
      "State-chartered credit unions or mutual reserve funds. Provide financial services to members.",
    keyRestrictions:
      "State-regulated. No Grift. Contributions not deductible. File Form 1024 once, 990 annually.",
    donations: "No",
  },
  "501(c)(15)": {
    shortDescription: "Small Insurance",
    purpose:
      "Mutual insurance companies or associations (small insurers). Provide insurance to members.",
    keyRestrictions:
      "Gross receipts <$500,000. Contributions not deductible. File Form 1024 once, 990 annually.",
    donations: "No",
  },
  "501(c)(16)": {
    shortDescription: "Crop Finance",
    purpose:
      "Cooperative organizations financing crop operations. Support agricultural activities.",
    keyRestrictions:
      "Funds for crop financing. No private profit. Contributions not deductible. File Form 1024 once, 990 annually.",
    donations: "No",
  },
  "501(c)(17)": {
    shortDescription: "Unemployment Funds",
    purpose:
      "Supplemental unemployment benefit trusts. Pay benefits to employees during layoffs.",
    keyRestrictions:
      "Benefits only for involuntary layoffs. No Grift. Contributions not deductible. File Form 1024 once, 990 annually.",
    donations: "No",
  },
  "501(c)(18)": {
    shortDescription: "Old Pensions",
    purpose:
      "Employee-funded pension trusts (pre-1959). Provide pension benefits.",
    keyRestrictions:
      "Employee-funded only. No diversion of funds. Contributions not deductible. File Form 1024 once, 990 annually.",
    donations: "No",
  },
  "501(c)(19)": {
    shortDescription: "Veterans Groups",
    purpose:
      "Veterans’ organizations providing benefits or social activities for members.",
    keyRestrictions:
      "75%+ members are veterans. Contributions deductible if 90%+ for exempt purposes. File Form 1024 once, 990 annually.",
    donations: "Yes (90%+ exempt)",
  },
  "501(c)(20)": {
    shortDescription: "Legal Services",
    purpose:
      "Group legal services organizations (discontinued after 1992). Provided legal benefits.",
    keyRestrictions: "Obsolete. No new exemptions.",
    donations: "No",
  },
  "501(c)(21)": {
    shortDescription: "Black Lung Trusts",
    purpose: "Black lung benefit trusts. Fund benefits for coal miners.",
    keyRestrictions:
      "Specific to black lung claims. Contributions deductible. File Form 1024 once, 990 annually.",
    donations: "Yes",
  },
  "501(c)(22)": {
    shortDescription: "Pension Liability",
    purpose:
      "Withdrawal liability payment funds. Manage employer pension liabilities.",
    keyRestrictions:
      "Specific to pension plans. No Grift. Contributions not deductible. File Form 1024 once, 990 annually.",
    donations: "No",
  },
  "501(c)(23)": {
    shortDescription: "Old Veterans",
    purpose:
      "Veterans’ organizations (pre-1880, benefits-focused). Similar to 501(c)(19) but older.",
    keyRestrictions:
      "Rare. Contributions deductible if for exempt purposes. File Form 1024 once, 990 annually.",
    donations: "Yes (exempt purposes)",
  },
  "501(c)(24)": {
    shortDescription: "ERISA Trusts",
    purpose:
      "Trusts for ERISA Section 4049 plans (post-1980 groundwork for organizations with significant grants or foreign expenses).",
    keyRestrictions:
      "Specific to ERISA plans. No Grift. Contributions not deductible. File Form 1024 once, 990 annually.",
    donations: "No",
  },
  "501(c)(25)": {
    shortDescription: "Multi-Parent Hold",
    purpose:
      "Title-holding corporations with multiple parents. Hold property for exempt orgs.",
    keyRestrictions:
      "Up to 35 parents. Income to exempt orgs. No Grift. File Form 1024 once, 990 annually.",
    donations: "No",
  },
  "501(c)(26)": {
    shortDescription: "High-Risk Health",
    purpose:
      "State-sponsored high-risk health insurance organizations. Provide coverage to high-risk individuals.",
    keyRestrictions:
      "State-regulated. No private profit. Contributions not deductible. File Form 1024 once, 990 annually.",
    donations: "No",
  },
  "501(c)(27)": {
    shortDescription: "Workers Comp",
    purpose:
      "State-sponsored workers’ compensation reinsurance organizations. Manage insurance risks.",
    keyRestrictions:
      "State-regulated. No Grift. Contributions not deductible. File Form 1024 once, 990 annually.",
    donations: "No",
  },
  "501(c)(28)": {
    shortDescription: "Railroad Pensions",
    purpose:
      "National Railroad Retirement Investment Trust. Manage railroad retirement funds.",
    keyRestrictions:
      "Specific to railroad pensions. No Grift. Contributions not deductible. File Form 1024 once, 990 annually.",
    donations: "No",
  },
  "501(c)(29)": {
    shortDescription: "CO-OP Health",
    purpose:
      "CO-OP health insurance issuers under ACA. Provide consumer-oriented health plans.",
    keyRestrictions:
      "ACA-specific. No private profit. Contributions not deductible. File Form 1024 once, 990 annually.",
    donations: "No",
  },
  "4947(a)(1)": {
    shortDescription: "Charitable Trusts",
    purpose:
      "Nonexempt charitable trusts treated as private foundations for charitable purposes.",
    keyRestrictions:
      "No Grift. Subject to private foundation rules (e.g., excise taxes, distributions). File Form 1023 once, 990 annually.",
    donations: "Yes",
  },
  USG: {
    shortDescription: "US Government",
    purpose: "Place holder to show USG Government grants",
    donations: "Did you know you can send the government extra money?",
  },
};

// CommonJS export for Node.js
//module.exports = { ORGANIZATION_TYPES };

// ES Module export for modern JavaScript
export default ORGANIZATION_TYPES;
