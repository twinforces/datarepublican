<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Address Cluster Report — ${report_date}</title>
  <style>
    ${css}
  </style>
</head>
<body>
  <header>
    <h1>Address Cluster Report</h1>
    <p class="meta">Generated ${generated_at} · DB: ${db_path} · ${cluster_count} clusters</p>
    <p class="meta">Criteria: multi_type ≥ ${min_multi_type} OR dot_carriers ≥ ${min_dot_carriers}% if require_grift_signal: · grift/misrep required% endif</p>
  </header>
  <table>
    <thead>
      <tr>
        <th>Score</th>
        <th>Address</th>
        <th>Types</th>
        <th>DOT</th>
        <th>Rows</th>
        <th>Max Grift</th>
        <th>Misrep</th>
        <th>Reasons</th>
        <th></th>
      </tr>
    </thead>
    <tbody>
% for c in clusters:
      <tr class="${'dot-heavy' if c['dot_carrier_count'] >= min_dot_carriers else ''}">
        <td>${c['suspicion_score']:.0f}</td>
        <td><a href="${c['maps_url']}" target="_blank" rel="noopener">${c['canonical_address']}</a></td>
        <td>${c['multi_type_count']}</td>
        <td>${c['dot_carrier_count']}</td>
        <td>${c['total_rows']:,}</td>
        <td>${'%.1f' % c['max_grift_ratio'] if c['max_grift_ratio'] is not None else '—'}</td>
        <td>${c['misrep_count']}</td>
        <td>${', '.join(c['reason_codes'])}</td>
        <td><a href="${c['detail_file']}">Detail →</a></td>
      </tr>
% endfor
    </tbody>
  </table>
  <footer>
    <p>Full exports: <code>data/clusters.json</code> · Criteria: <code>export_metadata.json</code></p>
  </footer>
</body>
</html>