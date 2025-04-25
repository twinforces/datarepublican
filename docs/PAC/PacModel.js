// Model: PAC (Node)
class PAC {
  constructor(ein, name) {
    this.ein = ein;
    this.name = name || `PAC_${ein}`;
  }
}

// Model: Donor (Node)
class Donor {
  constructor(ein, name) {
    this.ein = ein || `DONOR_${Math.random().toString(36).slice(2)}`; // Fallback unique ID
    this.name = name || `Donor_${this.ein}`;
  }
}

// Model: Grant (Edge)
class Grant {
  constructor(source_ein, target_ein, amount) {
    this.source_ein = source_ein;
    this.target_ein = target_ein;
    this.amount = parseFloat(amount) || 0;
  }
}

// ViewModel
class SankeyViewModel {
  constructor() {
    this.pacs = new Map();    // EIN -> PAC
    this.donors = new Map();  // EIN -> Donor
    this.grants = [];         // All edges
  }

  // Load PACs from index.csv
  async loadPACs(csvUrl) {
    const data = await d3.csv(csvUrl);
    data.forEach(row => {
      const ein = row.ein; // Adjust based on index.csv headers
      const name = row.name || row.organization_name;
      if (ein) this.pacs.set(ein, new PAC(ein, name));
    });
  }

  // Load Donor-to-PAC grants from receipts.csv
  async loadReceipts(csvUrl) {
    const data = await d3.csv(csvUrl);
    data.forEach(row => {
      const donor_ein = row.donor_ein || row.ein_1; // Adjust field names
      const pac_ein = row.ein; // PAC receiving the money
      const amount = row.amount;
      const donor_name = row.donor_name || row.contributor_name;
      if (pac_ein && amount) {
        if (!this.donors.has(donor_ein)) this.donors.set(donor_ein, new Donor(donor_ein, donor_name));
        if (!this.pacs.has(pac_ein)) this.pacs.set(pac_ein, new PAC(pac_ein));
        this.grants.push(new Grant(donor_ein, pac_ein, amount));
      }
    });
  }

  // Load PAC-to-Recipient grants from expenditures.csv
  async loadExpenditures(csvUrl) {
    const data = await d3.csv(csvUrl);
    data.forEach(row => {
      const source_ein = row.ein; // PAC EIN
      const target_ein = row.recipient_ein || `REC_${this.grants.length}`;
      const amount = row.amount;
      const recipient_name = row.recipient_name;
      if (source_ein && amount) {
        if (!this.pacs.has(source_ein)) this.pacs.set(source_ein, new PAC(source_ein));
        if (!this.pacs.has(target_ein)) this.pacs.set(target_ein, new PAC(target_ein, recipient_name));
        this.grants.push(new Grant(source_ein, target_ein, amount));
      }
    });
  }

  // Prepare Sankey data
  getSankeyData() {
    const nodes = [
      ...Array.from(this.pacs.values()).map(pac => ({ id: pac.ein, name: pac.name })),
      ...Array.from(this.donors.values()).map(donor => ({ id: donor.ein, name: donor.name }))
    ];
    const links = this.grants.map(grant => ({
      source: grant.source_ein,
      target: grant.target_ein,
      value: grant.amount
    }));
    return { nodes, links };
  }
}

// Example usage (call this from your HTML)
async function renderSankey() {
  const vm = new SankeyViewModel();
  await vm.loadPACs('index.csv');
  await vm.loadReceipts('receipts.csv');
  await vm.loadExpenditures('expenditures.csv');

  const sankeyData = vm.getSankeyData();
  const width = 1200, height = 600;

  const svg = d3.select('#chart')
    .append('svg')
    .attr('width', width)
    .attr('height', height);

  const sankey = d3.sankey()
    .nodeId(d => d.id)
    .nodeWidth(15)
    .nodePadding(10)
    .extent([[1, 1], [width - 1, height - 6]]);

  const { nodes, links } = sankey({
    nodes: sankeyData.nodes.map(d => Object.assign({}, d)),
    links: sankeyData.links.map(d => Object.assign({}, d))
  });

  svg.append('g')
    .selectAll('rect')
    .data(nodes)
    .enter()
    .append('rect')
    .attr('x', d => d.x0)
    .attr('y', d => d.y0)
    .attr('height', d => d.y1 - d.y0)
    .attr('width', d => d.x1 - d.x0)
    .attr('fill', '#69b3a2')
    .append('title')
    .text(d => `${d.name}\n${d.value.toLocaleString()}`);

  svg.append('g')
    .attr('fill', 'none')
    .selectAll('path')
    .data(links)
    .enter()
    .append('path')
    .attr('d', d3.sankeyLinkHorizontal())
    .attr('stroke', '#000')
    .attr('stroke-width', d => Math.max(1, d.width))
    .attr('stroke-opacity', 0.5)
    .append('title')
    .text(d => `${d.source.name} → ${d.target.name}\n${d.value.toLocaleString()}`);

  svg.append('g')
    .selectAll('text')
    .data(nodes)
    .enter()
    .append('text')
    .attr('x', d => d.x0 - 6)
    .attr('y', d => (d.y1 + d.y0) / 2)
    .attr('dy', '0.35em')
    .attr('text-anchor', 'end')
    .text(d => d.name)
    .filter(d => d.x0 < width / 2)
    .attr('x', d => d.x1 + 6)
    .attr('text-anchor', 'start');
}

// Export for external use if needed
window.SankeyViewModel = SankeyViewModel;
window.renderSankey = renderSankey;