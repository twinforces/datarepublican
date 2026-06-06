// longmarch/script.js
import { ingestTSV, searchPeople, getSummary } from './model.js';

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
    document.getElementById('viz-placeholder').classList.remove('hidden');
    return;
  }

  const matches = searchPeople(last, first);
  const summaryText = getSummary(matches);

  summaryDiv.innerHTML = `
    <strong>Results:</strong> ${summaryText}
    <button id="vizBtn" class="ml-3 px-3 py-1 text-sm bg-blue-600 text-white rounded hover:bg-blue-700">Show Visualization</button>
  `;
  summaryDiv.classList.remove('hidden');

  // Wire viz button
  const vizBtn = document.getElementById('vizBtn');
  if (vizBtn) {
    vizBtn.onclick = () => renderLongMarchViz(matches);
  }

  // Auto-render if exactly one strong match (common case)
  if (matches.length === 1) {
    renderLongMarchViz(matches);
  }
}

// =====================================================
// D3 Visual Layer - Long March Timeline + Arcs
// =====================================================

function renderLongMarchViz(peopleList) {
  const container = document.getElementById('viz-container');
  const placeholder = document.getElementById('viz-placeholder');
  if (placeholder) placeholder.classList.add('hidden');

  container.innerHTML = ''; // clear previous

  if (!peopleList || peopleList.length === 0) {
    container.innerHTML = '<p class="text-center text-gray-500 p-8">No people to visualize.</p>';
    return;
  }

  // For first sketch: focus on the first (or only) person
  const person = peopleList[0];
  const years = person.sortedYears;
  if (years.length === 0) {
    container.innerHTML = '<p class="text-center text-gray-500 p-8">No year data for this person.</p>';
    return;
  }

  const width = Math.max(container.clientWidth, 900);
  const height = 480;
  const margin = { top: 40, right: 40, bottom: 60, left: 60 };

  const svg = d3.select(container)
    .append('svg')
    .attr('width', width)
    .attr('height', height)
    .attr('class', 'longmarch-viz');

  // Year scale (horizontal timeline)
  const xScale = d3.scalePoint()
    .domain(years)
    .range([margin.left, width - margin.right])
    .padding(0.5);

  // Draw timeline axis
  svg.append('g')
    .attr('transform', `translate(0, ${height - margin.bottom})`)
    .call(d3.axisBottom(xScale).tickFormat(d => d))
    .selectAll('text')
    .style('font-size', '12px');

  // Title
  svg.append('text')
    .attr('x', width / 2)
    .attr('y', 24)
    .attr('text-anchor', 'middle')
    .style('font-size', '16px')
    .style('font-weight', '600')
    .text(`${person.fullname} — Long March Through Institutions`);

  // For each year, draw org circles (simple vertical stack for sketch; packing comes next)
  const yearGroups = svg.selectAll('.year-group')
    .data(years)
    .enter()
    .append('g')
    .attr('class', 'year-group')
    .attr('transform', d => `translate(${xScale(d)}, 0)`);

  yearGroups.each(function(year) {
    const orgs = person.orgsByYear[year] || [];
    const g = d3.select(this);

    // Vertical position for circles in this year column
    const yStart = margin.top + 60;
    const spacing = 70;

    orgs.forEach((org, i) => {
      const y = yStart + (i * spacing);

      // Circle for the org
      g.append('circle')
        .attr('cx', 0)
        .attr('cy', y)
        .attr('r', 22)
        .attr('fill', '#3b82f6')
        .attr('stroke', '#1e40af')
        .attr('stroke-width', 2)
        .style('cursor', 'pointer')
        .on('mouseover', function() {
          d3.select(this).attr('fill', '#60a5fa');
        })
        .on('mouseout', function() {
          d3.select(this).attr('fill', '#3b82f6');
        });

      // Org name label
      g.append('text')
        .attr('x', 0)
        .attr('y', y + 38)
        .attr('text-anchor', 'middle')
        .style('font-size', '11px')
        .style('fill', '#374151')
        .text(org.organization.length > 22 ? org.organization.slice(0, 20) + '…' : org.organization);

      // Year label above
      if (i === 0) {
        g.append('text')
          .attr('x', 0)
          .attr('y', margin.top + 30)
          .attr('text-anchor', 'middle')
          .style('font-size', '13px')
          .style('font-weight', '600')
          .text(year);
      }
    });
  });

  // Draw curved arcs connecting the person's path across years
  if (years.length > 1) {
    for (let i = 0; i < years.length - 1; i++) {
      const y1 = years[i];
      const y2 = years[i + 1];

      const x1 = xScale(y1);
      const x2 = xScale(y2);

      // Simple vertical center for first org in each year (can be improved with better positioning)
      const yPos1 = margin.top + 60;
      const yPos2 = margin.top + 60;

      // Curved path (arc-like)
      const path = d3.path();
      path.moveTo(x1, yPos1);
      const midX = (x1 + x2) / 2;
      path.quadraticCurveTo(midX, yPos1 - 80, x2, yPos2);

      svg.append('path')
        .attr('d', path.toString())
        .attr('fill', 'none')
        .attr('stroke', '#f59e0b')
        .attr('stroke-width', 2.5)
        .attr('stroke-opacity', 0.7)
        .attr('stroke-dasharray', '4 2');
    }
  }

  // Legend
  svg.append('g')
    .attr('transform', `translate(${margin.left}, ${height - 25})`)
    .append('text')
    .style('font-size', '11px')
    .style('fill', '#6b7280')
    .text('Orange dashed arcs = person\'s movement between institutions over time');
}

// Boot
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', init);
} else {
  init();
}
