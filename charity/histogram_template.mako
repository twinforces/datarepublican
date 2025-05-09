% for org_type_data in org_types:
# ${org_type_data['org_type']}
**${org_type_data['org_type']}**: ${org_type_data['description']}

**Number of Organizations**: ${org_type_data['count']}

% for metric in org_type_data['metrics']:
### ${metric['title']}

| Range         | Count    | Percentile |
|---------------|----------|------------|
% for start, end, count, percentile in metric['data']:
| ${start}-${end}% | ${count} | ${f"{percentile:.2f}%"} |
% endfor

% endfor
% endfor