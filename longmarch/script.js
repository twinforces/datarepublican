// longmarch/script.js
import { ingestTSV, searchPeople, getSummary, people as allPeople } from './model.js';

// Sample data (replace with real .tsv.gz later)
const SAMPLE_TSV = `lastname	firstname	fullname	organization	title	year
Wetter	Pierce	Pierce T. Wetter III	Radius	Engineer	1995
Wetter	Pierce	Pierce T. Wetter III	SuperMac	Engineer	1996
Wetter	Pierce	Pierce T. Wetter III	SomeOrg	VP	2005
Smith	John	John Smith	SomeOrg	Analyst	2005
Smith	John	John Smith	AnotherOrg	Director	2010
`;

let dataLoaded = false;

async function init() {
  const lastInput = document.getElementById('lastNameInput');
  const firstInput = document.getElementById('firstNameInput');
  const searchBtn = document.getElementById('searchBtn');
  const summaryDiv = document.getElementById('summary');

  if (!dataLoaded) {
    await ingestTSV(SAMPLE_TSV);
    dataLoaded = true;
  }

  searchBtn.addEventListener('click', () => doSearch(lastInput, firstInput, summaryDiv));
  lastInput.addEventListener('keypress', (e) => { if (e.key === 'Enter') doSearch(lastInput, firstInput, summaryDiv); });
  firstInput.addEventListener('keypress', (e) => { if (e.key === 'Enter') doSearch(lastInput, firstInput, summaryDiv); });
}

function doSearch(lastInput, firstInput, summaryDiv) {
  const last = lastInput.value.trim();
  const first = firstInput.value.trim();

  if (!last) {
    summaryDiv.classList.add('hidden');
    const ph = document.getElementById('viz-placeholder');
    if (ph) ph.classList.remove('hidden');
    return;
  }

  const matches = searchPeople(last, first);
  const summaryText = getSummary(matches);

  summaryDiv.innerHTML = `
    <strong>Results:</strong> ${summaryText}
    <button id="vizBtn" class="ml-3 px-3 py-1 text-sm bg-blue-600 text-white rounded hover:bg-blue-700">Show Visualization</button>
  `;
  summaryDiv.classList.remove('hidden');

  const vizBtn = document.getElementById('vizBtn');
  if (vizBtn) {
    vizBtn.onclick = () => renderLongMarchViz(matches);
  }

  if (matches.length === 1) {
    renderLongMarchViz(matches);
  }
}

// Stable rainbow-style color (inspired by browse/models.js + interpolateRainbow)
function getColorForName(str) {
  if (!str) return '#64748b';
  let hash = 0;
  for (let i = 0; i < str.length; i++) {
    hash = str.charCodeAt(i) + ((hash << 5) - hash);
  }
  const t = (Math.abs(hash) % 1000) / 1000;
  return d3.interpolateRainbow(t);
}

// =====================================================
// D3 Visual Layer - Long March with d3.pack() + Fellow Travelers
// =====================================================

function renderLongMarchViz(peopleList) {
  const container = document.getElementById('viz-container');
  const placeholder = document.getElementById('viz-placeholder');
  if (placeholder) placeholder.classList.add('hidden');
  container.innerHTML = '';

  if (!peopleList || peopleList.length === 0) {
    container.innerHTML = '<p class="text-center text-gray-500 p-8">No people to visualize.</p>';
    return;
  }

  const primary = peopleList[0];
  const years = primary.sortedYears;
  if (years.length === 0) {
    container.innerHTML = '<p class="text-center text-gray-500 p-8">No year data.</p>';
    return;
  }

  // === Two-pass fellow traveler detection ===
  const primaryOrgs = new Set();
  Object.values(primary.orgsByYear).forEach(orgList => {
    orgList.forEach(o => primaryOrgs.add(o.organization));
  });

  const fellowTravelers = [];
  const seen = new Set([primary.fullname.toLowerCase()]);

  for (const p of allPeople) {
    if (seen.has(p.fullname.toLowerCase())) continue;

    let shares = false;
    for (const [year, orgList] of Object.entries(p.orgsByYear)) {
      if (!primary.orgsByYear[year]) continue;
      const primarySet = new Set(primary.orgsByYear[year].map(o => o.organization));
      if (orgList.some(o => primarySet.has(o.organization))) {
        shares = true;
        break;
      }
    }
    if (shares) {
      fellowTravelers.push(p);
      seen.add(p.fullname.toLowerCase());
    }
  }

  const width = Math.max(container.clientWidth, 1100);
  const height = 620;
  const margin = { top: 55, right: 30, bottom: 55, left: 50 };
  const columnWidth = 95;

  const svg = d3.select(container)
    .append('svg')
    .attr('width', width)
    .attr('height', height);

  const xScale = d3.scalePoint()
    .domain(years)
    .range([margin.left + 30, width - margin.right - 30])
    .padding(0.5);

  // Timeline axis
  svg.append('g')
    .attr('transform', `translate(0, ${height - margin.bottom})`)
    .call(d3.axisBottom(xScale).tickFormat(d => d))
    .selectAll('text').style('font-size', '12px');

  // Title
  svg.append('text')
    .attr('x', width / 2)
    .attr('y', 28)
    .attr('text-anchor', 'middle')
    .style('font-size', '15px')
    .style('font-weight', '600')
    .text(`${primary.fullname} — Long March${fellowTravelers.length ? ` + ${fellowTravelers.length} fellow traveler(s)` : ''}`);

  // Draw packed circles per year using d3.pack()
  years.forEach(year => {
    const x = xScale(year);

    // Collect items for this year
    const items = [];

    // Primary person's orgs (larger)
    (primary.orgsByYear[year] || []).forEach(org => {
      items.push({
        name: org.organization,
        size: 140,
        type: 'primary',
        color: getColorForName(org.organization)
      });
    });

    // Fellow travelers' orgs this year (smaller)
    fellowTravelers.forEach(ft => {
      (ft.orgsByYear[year] || []).forEach(org => {
        const primaryOrgsThisYear = new Set((primary.orgsByYear[year] || []).map(o => o.organization));
        if (primaryOrgsThisYear.has(org.organization)) {
          items.push({
            name: `${ft.fullname} @ ${org.organization}`,
            size: 55,
            type: 'fellow',
            color: getColorForName(ft.fullname)
          });
        }
      });
    });

    if (items.length === 0) return;

    // Build hierarchy for packing
    const root = d3.hierarchy({ name: String(year), children: items })
      .sum(d => d.size);

    const packLayout = d3.pack()
      .size([columnWidth, 280])
      .padding(4);

    const packed = packLayout(root);

    const yearGroup = svg.append('g')
      .attr('transform', `translate(${x - columnWidth / 2}, ${margin.top + 10})`);

    // Draw packed circles
    yearGroup.selectAll('circle.node')
      .data(packed.leaves())
      .enter()
      .append('circle')
      .attr('class', 'node')
      .attr('cx', d => d.x)
      .attr('cy', d => d.y)
      .attr('r', d => d.r)
      .attr('fill', d => d.data.color)
      .attr('stroke', d => d.data.type === 'primary' ? '#1e2937' : '#64748b')
      .attr('stroke-width', d => d.data.type === 'primary' ? 2.5 : 1)
      .attr('opacity', d => d.data.type === 'primary' ? 1 : 0.9);

    // Labels (only for primary or short names)
    yearGroup.selectAll('text.label')
      .data(packed.leaves().filter(d => d.data.type === 'primary' || d.r > 18))
      .enter()
      .append('text')
      .attr('class', 'label')
      .attr('x', d => d.x)
      .attr('y', d => d.y + d.r + 12)
      .attr('text-anchor', 'middle')
      .style('font-size', d => d.data.type === 'primary' ? '10px' : '8px')
      .style('fill', '#334155')
      .text(d => {
        const label = d.data.name.split(' @ ')[0]; // for fellow format
        return label.length > 16 ? label.slice(0, 14) + '…' : label;
      });

    // Year header
    yearGroup.append('text')
      .attr('x', columnWidth / 2)
      .attr('y', -5)
      .attr('text-anchor', 'middle')
      .style('font-size', '13px')
      .style('font-weight', '600')
      .text(year);
  });

  // Prominent arcs for primary person's path (above the packed groups)
  if (years.length > 1) {
    const arcY = margin.top + 35;
    for (let i = 0; i < years.length - 1; i++) {
      const y1 = years[i];
      const y2 = years[i + 1];
      const x1 = xScale(y1);
      const x2 = xScale(y2);

      const path = d3.path();
      path.moveTo(x1, arcY);
      const midX = (x1 + x2) / 2;
      path.quadraticCurveTo(midX, arcY - 65, x2, arcY);

      svg.append('path')
        .attr('d', path.toString())
        .attr('fill', 'none')
        .attr('stroke', '#f59e0b')
        .attr('stroke-width', 3.5)
        .attr('stroke-opacity', 0.9)
        .attr('stroke-dasharray', '7 3');
    }
  }

  // Legend
  svg.append('text')
    .attr('x', margin.left)
    .attr('y', height - 22)
    .style('font-size', '11px')
    .style('fill', '#64748b')
    .text('Packed circles per year (larger = primary person) • Rainbow colors by name • Dashed arcs = primary person movement • Smaller circles = fellow travelers in same org/year');
}

// Boot
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', init);
} else {
  init();
}
