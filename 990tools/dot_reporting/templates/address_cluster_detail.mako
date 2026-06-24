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
    <p class="meta">${generated_at} · suspicion score ${int(cluster.get('suspicion_score') or 0)}</p>
    <div class="chips">
% for code in cluster['reason_codes']:
      <span class="chip">${code}</span>
% endfor
% if cluster.get('phy_is_po_box'):
      <span class="chip" style="background:#fee2e2; color:#991b1b;">phy_po_box</span>
% endif
    </div>
  </header>

  <!-- Review Decision (client-side localStorage) -->
  <section id="review-section" 
           data-slug="${cluster.get('slug', '')}"
           style="margin: 1rem 0; padding: 1rem; border: 1px solid #ddd; border-radius: 8px; background: #f8f9fa;">
    <h3 style="margin-top:0; margin-bottom:0.5rem;">Review Decision</h3>
    <div style="margin-bottom: 0.75rem;">
      <label style="font-size:0.9rem; color:#555;">Notes / Analysis</label><br>
      <textarea id="review-notes" rows="4" style="width:100%; font-family: inherit; padding:0.5rem; border:1px solid #ccc; border-radius:4px;"></textarea>
    </div>
    <div style="display:flex; gap:0.5rem; flex-wrap:wrap;">
      <button onclick="saveDecision('not')" style="background:#166534; color:white; border:none; padding:8px 16px; border-radius:4px; cursor:pointer;">Not</button>
      <button onclick="saveDecision('sus-dot')" style="background:#b91c1c; color:white; border:none; padding:8px 16px; border-radius:4px; cursor:pointer;">Sus-DOT</button>
      <button onclick="saveDecision('sus-ins')" style="background:#d97706; color:white; border:none; padding:8px 16px; border-radius:4px; cursor:pointer;">Sus-Ins</button>
    </div>
    <div id="review-status" style="margin-top:0.5rem; font-size:0.85rem; color:#555;"></div>
  </section>

  <section class="physical">
    <h2>Physical Footprint Assessment</h2>
    <p>${physical_note or 'No manual physical note on file. Review Google Maps / Street View via the address link above.'}</p>

    <div style="margin-top: 0.5rem;">
      <a href="${cluster['maps_url']}" target="Maps" style="font-size: 0.9rem; color: #0b57d0; text-decoration: none;">
        Open in Google Maps →
      </a>
    </div>
  </section>

  <section class="cards">
    <div class="card"><strong>${cluster['multi_type_count']}</strong><span>address types</span></div>
    <div class="card"><strong>${cluster['dot_carrier_count']}</strong><span>DOT rows</span></div>
    <div class="card"><strong>${cluster['total_rows']}</strong><span>total address rows</span></div>
    <div class="card"><strong>${cluster['charity_count']}</strong><span>charities</span></div>
    <div class="card"><strong>${cluster['grant_count']}</strong><span>grants</span></div>
  </section>

  <p class="types"><strong>Types present:</strong> ${', '.join(cluster['address_types'])}</p>

  <!-- Active vs Inactive Summary -->
  <section>
    <h2>DOT Carrier Status Summary</h2>
    <div class="cards">
      <div class="card">
        <strong>${cluster.get('dot_active_count', 0)}</strong>
        <span>Active (A)</span>
        <div style="font-size:0.85rem; color:#166534;">${"{:,}".format(cluster.get('dot_active_power_units', 0))} Power Units</div>
      </div>
      <div class="card" style="border-color:#f87171;">
        <strong>${cluster.get('dot_inactive_count', 0)}</strong>
        <span>Inactive (I)</span>
        <div style="font-size:0.85rem; color:#b91c1c;">${"{:,}".format(cluster.get('dot_inactive_power_units', 0))} Power Units</div>
      </div>
      <div class="card">
        <strong>${cluster.get('inactive_pct', 0)}%</strong>
        <span>Inactive Ratio</span>
      </div>
    </div>
  </section>

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

% if phone_groups:
  <section>
    <h2>DOT Carriers by Phone (grouped)</h2>
    <p style="font-size:0.85rem; color:#666; margin-bottom:0.5rem;">
      Carriers sharing the same phone number are grouped. Shared phones + high inactive power units are a strong fraud signal.
    </p>
% for phone, carriers in phone_groups:
<%
  active = [r for r in carriers if r.get('status_code') == 'A']
  inactive = [r for r in carriers if r.get('status_code') == 'I']
  active_pu = sum(r.get('power_units') or 0 for r in active)
  inactive_pu = sum(r.get('power_units') or 0 for r in inactive)
%>
    <div style="margin-bottom: 1rem; border: 1px solid #ddd; border-radius: 6px; padding: 0.5rem 0.75rem;">
      <div style="font-weight:600; margin-bottom:0.35rem; display:flex; justify-content:space-between; align-items:center;">
        <span>${phone}</span>
        <span style="font-size:0.8rem; color:#555; font-weight:normal;">
          Active: ${len(active)} (${active_pu} PUs) | Inactive: ${len(inactive)} (${inactive_pu} PUs)
        </span>
      </div>

      % if active:
      <table style="margin:0 0 0.5rem 0; font-size:0.82rem;">
        <thead><tr><th>DOT#</th><th>Legal Name</th><th>Power Units</th></tr></thead>
        <tbody>
% for r in sorted(active, key=lambda x: x.get('power_units') or 0, reverse=True):
          <tr>
            <td>${r['dot_number']}</td>
            <td>${r['legal_name'] or r.get('dba_name') or ''}</td>
            <td>${r.get('power_units') if r.get('power_units') is not None else '—'}</td>
          </tr>
% endfor
        </tbody>
      </table>
      % endif

      % if inactive:
      <details style="font-size:0.82rem;">
        <summary style="cursor:pointer; color:#b91c1c; font-weight:500;">
          Show ${len(inactive)} Inactive carriers (${inactive_pu} PUs)
        </summary>
        <table style="margin-top:0.4rem;">
          <thead><tr><th>DOT#</th><th>Legal Name</th><th>Power Units</th></tr></thead>
          <tbody>
% for r in sorted(inactive, key=lambda x: x.get('power_units') or 0, reverse=True):
            <tr class="dot-heavy">
              <td>${r['dot_number']}</td>
              <td>${r['legal_name'] or r.get('dba_name') or ''}</td>
              <td>${r.get('power_units') if r.get('power_units') is not None else '—'}</td>
            </tr>
% endfor
          </tbody>
        </table>
      </details>
      % endif
    </div>
% endfor
  </section>
% endif

  <footer><a href="index.html">← Back to index</a></footer>

<%text>
<script>
const STORAGE_KEY = 'address_cluster_review_v2';
const reviewSection = document.getElementById('review-section');
const SLUG = reviewSection ? reviewSection.dataset.slug : '';

function getReviewState() {
  try { return JSON.parse(localStorage.getItem(STORAGE_KEY) || '{}'); } catch(e) { return {}; }
}
function saveReviewState(state) {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
}

function loadReview() {
  if (!SLUG) return;
  const state = getReviewState();
  const entry = state[SLUG];
  const textarea = document.getElementById('review-notes');
  const statusEl = document.getElementById('review-status');

  if (entry && typeof entry === 'object') {
    if (textarea) textarea.value = entry.notes || '';
    if (statusEl && entry.status) {
      statusEl.innerHTML = `Current: <strong>${entry.status}</strong> <span style="color:#888;">(${new Date(entry.updated_at || '').toLocaleString()})</span>`;
    }
  } else if (entry === true) {
    // legacy
    if (statusEl) statusEl.innerHTML = `Current: <strong>sus-dot</strong> (legacy)`;
  }
}

function saveDecision(status) {
  if (!SLUG) return;
  const textarea = document.getElementById('review-notes');
  const notes = textarea ? textarea.value.trim() : '';

  const state = getReviewState();
  state[SLUG] = {
    status: status,
    notes: notes,
    updated_at: new Date().toISOString()
  };
  saveReviewState(state);

  // brief visual feedback then go back
  const statusEl = document.getElementById('review-status');
  if (statusEl) statusEl.innerHTML = `Saved as <strong>${status}</strong>. Returning to index...`;

  setTimeout(() => {
    window.location.href = 'index.html';
  }, 650);
}

document.addEventListener('DOMContentLoaded', loadReview);
</script>
</%text>

</body>
</html>
