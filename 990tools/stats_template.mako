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

## Table Summaries

% for table_name, summary_data in table_summaries.items():
### ${table_name}

| Column Name | Type | Min | Max | Approx Unique | Avg | Std | Q25 | Q50 | Q75 | Count | Null % |
|-------------|------|-----|-----|--------------|-----|-----|-----|-----|-----|-------|--------|
% for row in summary_data:
| ${table_name}.${row[0]} | ${row[1]} | ${row[2] if row[2] is not None else ''} | ${row[3] if row[3] is not None else ''} | ${row[4] if row[4] is not None else ''} | ${row[5] if row[5] is not None else ''} | ${row[6] if row[6] is not None else ''} | ${row[7] if row[7] is not None else ''} | ${row[8] if row[8] is not None else ''} | ${row[9] if row[9] is not None else ''} | ${row[10] if row[10] is not None else ''} | ${row[11] if row[11] is not None else ''} |
% endfor

% endfor

## Processing Details

- **Step**: ${step_name}
- **Database**: ${db_path}
- **Timestamp**: ${timestamp}

## Notes

${notes}