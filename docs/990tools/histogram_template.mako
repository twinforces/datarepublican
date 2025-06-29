${'##'} Percentile Report for ${org_type} - ${year}

% for metric in metrics:
${'###'} ${metric.replace('_pct', '').replace('_ratio', '').title()} Histogram
% if len(metrics[metric]['bins']) > 0:
**Histogram Table for ${metric}:**

| Value Range | Count | Min | Max | Cumulative % |
|-------------|-------|-----|-----|--------------|
% for value_range, (count, min_val, max_val, cum_pct) in metrics[metric]['bins']:
| ${value_range} | ${count} | ${'{:.2f}'.format(min_val) if min_val is not None else 'n/a'} | ${'{:.2f}'.format(max_val) if max_val is not None else 'n/a'} | ${'{:.2f}'.format(cum_pct)} |
% endfor

% else:
No valid histogram data for ${metric}.
% endif

${'###'} ${metric.replace('_pct', '').replace('_ratio', '').title()} Percentiles
% if len(metrics[metric]['percentiles']) > 0:
**Percentile Table for ${metric}:**

| Cumulative % | Value | Count | Min | Max |
|--------------|-------|-------|-----|-----|
% for cum_pct, (value, count, min_val, max_val) in metrics[metric]['percentiles']:
| ${cum_pct if isinstance(cum_pct, str) else '{:.0f}'.format(cum_pct)} | ${'{:.2f}'.format(value) if value is not None else 'n/a'} | ${count} | ${'{:.2f}'.format(min_val) if min_val is not None else 'n/a'} | ${'{:.2f}'.format(max_val) if max_val is not None else 'n/a'} |
% endfor

Valid rows: ${metrics[metric]['valid_rows']}
Top/bottom counts: top=${len(metrics[metric]['top_rows'])}, bottom=${len(metrics[metric]['bottom_rows'])}

${'####'} Top rows for ${metric}
${metrics[metric]['top_rows'].to_markdown(index=False, stralign='left', numalign='right')}

${'####'} Bottom rows for ${metric}
${metrics[metric]['bottom_rows'].to_markdown(index=False, stralign='left', numalign='right')}
% else:
No valid percentile data for ${metric}.
% endif

% endfor