<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>${cluster['canonical_address'][:60]}…</title>
  <style>
    ${css}
  </style>
</head>
<body>
  <nav><a href="index.html">← Index</a></nav>
  <header>
    <h1><a href="${cluster['maps_url']}" target="_blank" rel="noopener">${cluster['canonical_address']}</a></h1>
    <p class="meta">${generated_at} · suspicion score ${cluster['suspicion_score']:.0f}</p>
    <div class="chips">
% for code in cluster['reason_codes']:
      <span class="chip">${code}</span>
% endfor
    </div>
  </header>

  <section class="physical">
    <h2>Physical Footprint Assessment</h2>
    <p>${physical_note or 'No manual physical note on file. Review Google Maps / Street View via the address link above.'}</p>
  </section>

  <section class="cards">
    <div class="card"><strong>${cluster['multi_type_count']}</strong><span>address types</span></div>
    <div class="card"><strong>${cluster['dot_carrier_count']}</strong><span>DOT rows</span></div>
    <div class="card"><strong>${cluster['total_rows']:,}</strong><span>total address rows</span></div>
    <div class="card"><strong>${cluster['charity_count']}</strong><span>charities</span></div>
    <div class="card"><strong>${cluster['grant_count']}</strong><span>grants</span></div>
  </section>

  <p class="types"><strong>Types present:</strong> ${', '.join(cluster['address_types'])}</p>

% if charities:
  <section>
    <h2>Charities (top ${len(charities)})</h2>
    <table>
      <thead><tr><th>EIN</th><th>Name</th><th>Year</th><th>Receipts</th><th>Grift</th><th>Misrep</th></tr></thead>
      <tbody>
% for r in charities:
        <tr class="${'flag-misrep' if r.get('misrep') else ('flag-grift' if (r.get('grift_ratio') or 0) > 5 else '')}">
          <td>${r['ein']}</td>
          <td>${r['filer_name']}</td>
          <td>${r['tax_year']}</td>
          <td>${r['receipt_fmt']}</td>
          <td>${'%.1f' % r['grift_ratio'] if r.get('grift_ratio') is not None else '—'}</td>
          <td>${'yes' if r.get('misrep') else ''}</td>
        </tr>
% endfor
      </tbody>
    </table>
  </section>
% endif

% if officers:
  <section>
    <h2>Officers (top ${len(officers)})</h2>
    <table>
      <thead><tr><th>Name</th><th>Compensation</th><th>Year</th></tr></thead>
      <tbody>
% for r in officers:
        <tr><td>${r['display_name']}</td><td>${r['comp_fmt']}</td><td>${r['tax_year']}</td></tr>
% endfor
      </tbody>
    </table>
  </section>
% endif

% if grants:
  <section>
    <h2>Grants (top ${len(grants)})</h2>
    <table>
      <thead><tr><th>Filer EIN</th><th>Grantee</th><th>Amount</th><th>Year</th></tr></thead>
      <tbody>
% for r in grants:
        <tr><td>${r['filer_ein']}</td><td>${r['grantee_name']}</td><td>${r['amt_fmt']}</td><td>${r['tax_year']}</td></tr>
% endfor
      </tbody>
    </table>
  </section>
% endif

% if dot_carriers:
  <section>
    <h2>DOT Carriers (top ${len(dot_carriers)})</h2>
    <table>
      <thead><tr><th>DOT#</th><th>Legal Name</th><th>Status</th><th>Power Units</th><th>Phone</th></tr></thead>
      <tbody>
% for r in dot_carriers:
        <tr class="${'dot-heavy' if (r.get('power_units') or 0) >= 20 else ''}">
          <td>${r['dot_number']}</td>
          <td>${r['legal_name'] or r.get('dba_name') or ''}</td>
          <td>${r.get('status_code') or ''}</td>
          <td>${r.get('power_units') if r.get('power_units') is not None else '—'}</td>
          <td>${r.get('phone') or ''}</td>
        </tr>
% endfor
      </tbody>
    </table>
  </section>
% endif

  <footer><a href="index.html">← Back to index</a></footer>
</body>
</html>