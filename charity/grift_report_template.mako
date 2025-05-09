# Grift Report for Tax Year ${tax_year}

% for org_type in sorted(set([cand['org_type'] for cand in top_grift + top_grants])):
## ${org_type}
**${org_type}**: ${ORG_TYPE_DESCRIPTIONS.get(org_type, 'No description available.')}

### Top Grift Ratio
| EIN | Name | Type | Grift Ratio (%) | Grift Ratio Percentile | Rating | Domestic Misrepresentation (%) | Foreign Expenses (%) | Notes |
|-----|------|------|-----------------|-----------------------|--------|-------------------------------|----------------------|-------|
% for candidate in [cand for cand in top_grift if cand['org_type'] == org_type]:
| ${candidate['filer_ein']} | ${candidate['filer_name']} | ${candidate['org_type']} | ${candidate['grift_ratio']} | ${candidate['grift_ratio_percentile']} | ${candidate['grift_rating']} | ${candidate['domestic_misrep_pct']} | ${candidate['foreign_exp_pct']} | ${"International charity with low domestic spending" if candidate['filer_ein'] in ["271414646", "520851555"] and candidate['grift_ratio'] < 10 and candidate['foreign_exp_pct'] > 10 else ""} |
% endfor

### Top Grants to Others (Informational)
| EIN | Name | Type | Grants to Others (%) | Grants Percentile | Grants Rating |
|-----|------|------|----------------------|-------------------|---------------|
% for candidate in [cand for cand in top_grants if cand['org_type'] == org_type]:
| ${candidate['filer_ein']} | ${candidate['filer_name']} | ${candidate['org_type']} | ${candidate['grants_pct']} | ${candidate['grants_pct_percentile']} | ${candidate['grants_rating']} |
% endfor

% endfor

## Calculation Explanations
- **Grift Ratio (%)**: Percentage of total expenses spent on officer compensation, travel, and conferences, indicating potential self-dealing. Calculated as: `(officer_comp + travel_amt + conferences_amt) / total_exp * 100`. Clamped to 0–100.
- **Grift Ratio Percentile**: Percentile of grift ratio within the organization’s 501(c) type, where higher percentiles indicate higher domestic spending relative to peers. Ratings: A (80–100), B (60–79), C (40–59), D (20–39), F (0–19).
- **Grants to Others (%) (Informational)**: Percentage of total expenses allocated to grants to others, calculated as: `grants_to_others / total_exp * 100`. Provided for context, not a direct grift indicator.
- **Domestic Misrepresentation (%) (Informational)**: Same as grift ratio if flagged (grift ratio > 10 and foreign expenses < 10% of total expenses). Indicates potential misrepresentation of domestic focus.
- **Foreign Expenses (%) (Informational)**: Percentage of total expenses spent on foreign activities, calculated as: `foreign_expenses / total_exp * 100`. Notable for international charities (e.g., CHAI, Amnesty) if low grift ratio (< 10%) and high foreign expenses (> 10%), suggesting minimal domestic spending.