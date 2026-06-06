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

// Stable rainbow-style color for a string (org or person name)
// Steals the spirit of browse/models.js getColorForEIN + interpolateBand
function getColorForName(str) {
  if (!str) return '#64748b';
  let hash = 0;
  for (let i = 0; i < str.length; i++) {
    hash = str.charCodeAt(i) + ((hash << 5) - hash);
  }
  const t = (Math.abs(hash) % 1000) / 1000;
  // Use d3's rainbow for nice distinct colors (similar to browse's interpolateRainbow usage)
  return d3.interpolateRainbow(t);
}

// =====================================================
// D3 Visual Layer - Long March with Fellow Travelers
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

  // === Two-pass fellow traveler logic ===
  // Pass 1: collect all orgs the primary person was in
  const primaryOrgs = new Set();
  Object.values(primary.orgsByYear).forEach(orgList => {
    orgList.forEach(o => primaryOrgs.add(o.organization));
  });

  // Pass 2: find other people who were in any of those orgs in overlapping years
  const fellowTravelers = [];
  const seen = new Set([primary.fullname.toLowerCase()]);

  for (const p of allPeople) {
    if (seen.has(p.fullname.toLowerCase())) continue;

    let sharesOrgAndYear = false;
    for (const [year, orgList] of Object.entries(p.orgsByYear)) {
      if (!primary.orgsByYear[year]) continue;
      const primaryOrgsThisYear = new Set(primary.orgsByYear[year].map(o => o.organization));
      for (const o of orgList) {
        if (primaryOrgsThisYear.has(o.organization)) {
          sharesOrgAndYear = true;
          break;
        }
      }
      if (sharesOrgAndYear) break;
    }

    if (sharesOrgAndYear) {
      fellowTravelers.push(p);
      seen.add(p.fullname.toLowerCase());
    }
  }

  const width = Math.max(container.clientWidth, 980);
  const height = 520;
  const margin = { top: 50, right: 40, bottom: 70, left: 60 };

  const svg = d3.select(container)
    .append('svg')
    .attr('width', width)
    .attr('height', height)
    .attr('class', 'longmarch-viz');

  const xScale = d3.scalePoint()
    .domain(years)
    .range([margin.left, width - margin.right])
    .padding(0.6);

  // Timeline axis
  svg.append('g')
    .attr('transform', `translate(0, ${height - margin.bottom})`)
    .call(d3.axisBottom(xScale).tickFormat(d => d))
    .selectAll('text').style('font-size', '12px');

  // Title
  svg.append('text')
    .attr('x', width / 2)
    .attr('y', 26)
    .attr('text-anchor', 'middle')
    .style('font-size', '15px')
    .style('font-weight', '600')
    .text(`${primary.fullname} — Long March${fellowTravelers.length ? ` + ${fellowTravelers.length} fellow traveler(s)` : ''}`);

  // Render each year column
  years.forEach(year => {
    const x = xScale(year);
    const g = svg.append('g').attr('transform', `translate(${x}, 0)`);

    // Year label
    g.append('text')
      .attr('x', 0)
      .attr('y', margin.top - 10)
      .attr('text-anchor', 'middle')
      .style('font-size', '13px')
      .style('font-weight', '600')
      .text(year);

    // Primary person's orgs this year (larger, prominent)
    const primaryOrgsThisYear = primary.orgsByYear[year] || [];
    primaryOrgsThisYear.forEach((org, i) => {
      const y = margin.top + 50 + (i * 78);

      g.append('circle')
        .attr('cx', 0)
        .attr('cy', y)
        .attr('r', 26)
        .attr('fill', getColorForName(org.organization))
        .attr('stroke', '#1e2937')
        .attr('stroke-width', 2.5)
        .style('cursor', 'pointer');

      g.append('text')
        .attr('x', 0)
        .attr('y', y + 42)
        .attr('text-anchor', 'middle')
        .style('font-size', '10px')
        .style('fill', '#334155')
        .text(org.organization.length > 18 ? org.organization.slice(0,16)+'…' : org.organization);
    });

    // Fellow travelers in the same orgs this year (smaller, lighter)
    fellowTravelers.forEach((ft, ftIndex) => {
      const ftOrgsThisYear = ft.orgsByYear[year] || [];
      ftOrgsThisYear.forEach((org, i) => {
        // Only show if they share an org with primary this year
        const shares = primaryOrgsThisYear.some(po => po.organization === org.organization);
        if (!shares) return;

        const y = margin.top + 50 + (primaryOrgsThisYear.length * 78) + (ftIndex * 38) + (i * 38);

        g.append('circle')
          .attr('cx', 0)
          .attr('cy', y)
          .attr('r', 14)
          .attr('fill', getColorForName(ft.fullname))
          .attr('stroke', '#64748b')
          .attr('stroke-width', 1)
          .attr('opacity', 0.85);

        g.append('text')
          .attr('x', 0)
          .attr('y', y + 28)
          .attr('text-anchor', 'middle')
          .style('font-size', '9px')
          .style('fill', '#475569')
          .text(ft.fullname.length > 14 ? ft.fullname.slice(0,12)+'…' : ft.fullname);
      });
    });
  });

  // Prominent arcs for the primary person's path
  if (years.length > 1) {
    for (let i = 0; i < years.length - 1; i++) {
      const y1 = years[i];
      const y2 = years[i + 1];
      const x1 = xScale(y1);
      const x2 = xScale(y2);

      const path = d3.path();
      path.moveTo(x1, margin.top + 76);
      const midX = (x1 + x2) / 2;
      path.quadraticCurveTo(midX, margin.top + 20, x2, margin.top + 76);

      svg.append('path')
        .attr('d', path.toString())
        .attr('fill', 'none')
        .attr('stroke', '#f59e0b')
        .attr('stroke-width', 3)
        .attr('stroke-opacity', 0.85)
        .attr('stroke-dasharray', '6 3');
    }
  }

  // Legend
  const legendY = height - 28;
  svg.append('text')
    .attr('x', margin.left)
    .attr('y', legendY)
    .style('font-size', '11px')
    .style('fill', '#64748b')
    .text('Large colored circles = primary person’s organizations • Smaller circles = fellow travelers in same org/year • Dashed arcs = primary person’s movement');
}

// Boot
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', init);
} else {
  init();
}
