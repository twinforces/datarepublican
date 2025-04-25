```markdown
# Application for Award for Original Information (Form 211 Equivalent)

**Submission Date**: ${submission_date}

**To**:  
Internal Revenue Service Whistleblower Office – ICE  
1973 N. Rulon White Blvd. M/S 4110  
Ogden, UT 84404

## Section A: Information About the Person or Entity You Are Reporting

The following nonprofit organizations are reported for potential tax fraud based on IRS Form 990 data from 2021, indicating excessive officer compensation, high travel expenses, or questionable use of government grants.

% for i, ngo in enumerate(ngos, 1):
${i}. **Organization ${i}**  
   - **EIN**: ${ngo['filer_ein']}  
   - **Name**: ${ngo['filer_name']}  
   - **Address**: ${ngo.get('address', 'Unknown')}  
   - **Violation**: Reported officer compensation of ${ngo['comp_pct']:.2f}% ($${ngo['officer_comp']:,.0f}) and government grants of $${ngo['GovernmentGrantsAmt']:,.0f} in 2021, with total expenses of $${ngo['total_exp']:,.0f}, suggesting potential misuse of funds. Organization type: ${ngo['org_type']}.
% endfor

## Section B: Description of the Alleged Tax Violation

The above organizations were identified from IRS Form 990 data processed from the 2021 TEOS archive (file size 3.5 GB, significantly larger than 357 MB in 2020, suggesting increased nonprofit activity post-2020 election). Analysis indicates potential tax fraud through:
- **Excessive Officer Compensation**: `comp_pct` exceeding 10–25%, far above typical nonprofit standards.
- **Questionable Travel Expenses**: `travel_pct` exceeding 10%, indicating possible misuse.
- **Suspicious Grant Use**: Large government grants (> $1M–$10M) in 2021, often with low program expenses.
- **New Entities**: Some organizations appeared in 2021 but not 2020, potentially shell entities.

**Evidence**:
- Attached CSV file (`grift_candidates_2021_subset.csv`) detailing EINs, names, `comp_pct`, `travel_pct`, `GovernmentGrantsAmt`, and `total_exp`.
- Attached XML files (`<EIN>_public.xml`) from 2020 and 2021 TEOS archives.
- Analysis summary: Notes on ZIP size patterns (357 MB in 2020, 3.5 GB in 2021) and grift indicators.

## Section C: Information About Yourself

- **Name**: ${your_name}  
- **Address**: ${your_address}  
- **Phone**: ${your_phone}  
- **Email**: ${your_email}  
- **Anonymity Request**: ${anonymity_request}

## Section D: Declaration

I declare under penalty of perjury that the information provided in this submission is true, correct, and complete to the best of my knowledge.

**Signature**: _____________________________  
**Date**: ${submission_date}

## Attachments
- `grift_candidates_2021_subset.csv`: Data for reported NGOs.
- `<EIN>_public.xml`: Form 990 XML files (2020 and 2021).
- Analysis summary: Notes on ZIP size patterns and grift indicators.
```