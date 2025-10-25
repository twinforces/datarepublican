<%!
# stats_template.mako - Template for database statistics reports
%>
# Database Statistics Report - ${step_name}

Generated: ${timestamp}

## Summary

Total records across all tables: ${total_records}

## Table Counts

| Table | Count |
|-------|-------|
% for table_name, count in table_counts.items():
| ${table_name} | ${count} |
% endfor

## XmlFiles Group Counts

### Tax Year Distribution
| Tax Year | Count |
|----------|-------|
% for tax_year, count in xml_group_counts['tax_year']:
| ${tax_year} | ${count} |
% endfor

### Form Type Distribution
| Form Type | Count |
|-----------|-------|
% for form_type, count in xml_group_counts['form_type']:
| ${form_type} | ${count} |
% endfor

### Processing Status Distribution
| Processed | Count |
|-----------|-------|
% for processed, count in xml_group_counts['processed']:
| ${processed} | ${count} |
% endfor

### Processing Version Distribution
| Processing Version | Count |
|-------------------|-------|
% for version, count in xml_group_counts['processing_version']:
| ${version} | ${count} |
% endfor

### Error Message Prefix Distribution (Top 10)
| Error Message Prefix | Count |
|---------------------|-------|
% for error_prefix, count in xml_group_counts['error_message_prefix'][:10]:
| ${error_prefix} | ${count} |
% endfor

## XmlFiles File Size Histogram

% if xml_histogram:
| Bucket | Count |
|--------|-------|
% for bucket in xml_histogram:
| ${bucket['bucket']} | ${bucket['count']} |
% endfor
% else:
No histogram data available (no file_size data or histogram function failed).
% endif

## Table Summaries

% for table_name, summary_data in table_summaries.items():
### ${table_name}

| Column Name | Type | Min | Max | Approx Unique | Avg | Std | Q25 | Q50 | Q75 | Count | Null % |
|-------------|------|-----|-----|--------------|-----|-----|-----|-----|-----|-------|--------|
% for row in summary_data:
| ${table_name}.${row[0]} | ${row[1]} | ${row[2] if row[2] is not None else ''} | ${row[3] if row[3] is not None else ''} | ${row[4] if row[4] is not None else ''} | ${row[5] if row[5] is not None else ''} | ${row[6] if row[6] is not None else ''} | ${row[7] if row[7] is not None else ''} | ${row[8] if row[8] is not None else ''} | ${row[9] if row[9] is not None else ''} | ${row[10] if row[10] is not None else ''} | ${row[11] if row[11] is not None else ''} |
% endfor

% endfor

## Detailed Table Analysis

### Charities Analysis

#### Tax Year Distribution
| Tax Year | Count |
|----------|-------|
% for tax_year, count in charities_analysis['tax_year_counts']:
| ${tax_year} | ${count} |
% endfor

#### Organization Type Distribution
| Org Type | Count |
|----------|-------|
% for org_type, count in charities_analysis['org_type_counts']:
| ${org_type} | ${count} |
% endfor

#### Form Type Distribution
| Form Type | Count |
|-----------|-------|
% for form_type, count in charities_analysis['form_type_counts']:
| ${form_type} | ${count} |
% endfor

#### Financial Amount Histograms
% for col_name, histogram in charities_analysis['histograms'].items():
##### ${col_name} Histogram
| Bucket | Count |
|--------|-------|
% for bucket in histogram:
| ${bucket['bucket']} | ${bucket['count']} |
% endfor

% endfor

### Officers Analysis

#### Top 10 Last Names
| Last Name | Count |
|-----------|-------|
% for last_name, count in officers_analysis['top_last_names']:
| ${last_name} | ${count} |
% endfor

### Grants Analysis

% if 'grant_amt_histogram' in grants_analysis:
#### Grant Amount Histogram
| Bucket | Count |
|--------|-------|
% for bucket in grants_analysis['grant_amt_histogram']:
| ${bucket['bucket']} | ${bucket['count']} |
% endfor
% else:
No grant amount histogram (min == max or no data).
% endif

### Contractors Analysis

% if 'amount_histogram' in contractors_analysis:
#### Contractor Amount Histogram
| Bucket | Count |
|--------|-------|
% for bucket in contractors_analysis['amount_histogram']:
| ${bucket['bucket']} | ${bucket['count']} |
% endfor
% else:
No contractor amount histogram (min == max or no data).
% endif

### Political Contributions Analysis

% if 'amount_histogram' in political_contributions_analysis:
#### Political Contribution Amount Histogram
| Bucket | Count |
|--------|-------|
% for bucket in political_contributions_analysis['amount_histogram']:
| ${bucket['bucket']} | ${bucket['count']} |
% endfor
% else:
No political contribution amount histogram (min == max or no data).
% endif

### Addresses Analysis

#### Top 10 States
| State | Count |
|-------|-------|
% for state, count in addresses_analysis['top_states']:
| ${state} | ${count} |
% endfor

#### Top 10 ZIP Codes
| ZIP Code | Count |
|----------|-------|
% for zip_code, count in addresses_analysis['top_zip_codes']:
| ${zip_code} | ${count} |
% endfor

## Processing Details

- **Step**: ${step_name}
- **Database**: ${db_path}
- **Timestamp**: ${timestamp}

## Notes

${notes}