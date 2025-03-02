let selectedYear = '2025';
let selectedType = 'ba';
let dataReady=false;

import {BudgetNode} from "./models.js";

gel('selectYearBtn').addEventListener('click', showYearPopup);
gel('confirmYearBtn').addEventListener('click', confirmYear);
gel('cancelYearBtn').addEventListener('click', hideYearPopup);

async function loadData(budgetCsvFile, functionSubfunctionCsvFile) {
    statusUpdate("Loading data...");
    const [budgetResponse, functionSubfunctionResponse] = await Promise.all([
        fetch(budgetCsvFile),
        fetch(functionSubfunctionCsvFile)
    ]);
    if (!budgetResponse.ok) throw new Error(`Failed to load budget CSV: ${budgetResponse.statusText}`);
    if (!functionSubfunctionResponse.ok) throw new Error(`Failed to load function/subfunction CSV: ${functionSubfunctionResponse.statusText}`);
    const budgetText = await budgetResponse.text();
    const functionSubfunctionText = await functionSubfunctionResponse.text();
    return { budgetText, functionSubfunctionText };
}

function processFunctionSubfunctionCSV(csvString) {
    const rows = csvString.split('\n').map(row => row.split(','));
    const functionCodeToName = new Map();
    const subfunctionCodeToName = new Map();

    let currentFunctionCode = null;
    let currentFunctionName = null;
    let currentDeptName = null;
    let lookupMap={};
    rows.forEach(row => {
        let departmentName = row[0].trim() || currentDeptName || "";
        let functionName = row[1]?.trim();
        let functionCode = row[3]?.trim();
        const subfunctionName = row[3]?.trim();
        const subfunctionCode = row[4]?.trim();
        
        if (row[0].trim().length) 
        {
                currentDeptName = row[0].trim();
                departmentName = currentDeptName;
        }
        if (!functionName.length) 
        {
                functionName = "Top";
                functionCode = row[2]?.trim()
        }

        if (!functionName && !functionCode && !subfunctionName && !subfunctionCode) return;
        currentDeptName = departmentName.replace(/"/g,'');
        if (functionName && functionCode) {
            currentFunctionCode = functionCode;
            currentFunctionName = functionName.replace(/"/g, '');
            functionCodeToName.set(currentFunctionCode, [departmentName,currentFunctionName]);
        } else if (subfunctionName && subfunctionCode && currentFunctionCode) {
            subfunctionCodeToName.set(subfunctionCode, subfunctionName.replace(/"/g, ''));
        }
    });

    return { functionCodeToName, subfunctionCodeToName };
}

function storePath(path, keyStore,value){
        let start = keyStore;
        partialPath=[];
        path.forEach( level => {
                partialPath.push(level);
                let next=start[level];
                if (!next) 
                        next = start[level] = {
                                 path: partialPath, 
                                 value:value, 
                                 children:{}, 
                                 name:level};
                else
                        next.value += value;
                start=next.children;
        });

}

 function processBudgetCSV(budgetCsvString, functionSubfunctionCsvString, year, type) {
    const { functionCodeToName, subfunctionCodeToName } = processFunctionSubfunctionCSV(functionSubfunctionCsvString);


    let results={};
    let resultsArr=[];
    let levelKeys={};
     let totalValue = 0;
        Papa.parse(budgetCsvString, {
            header: true,
            skipEmptyLines: true,
            complete: results => {
                results.data.forEach(row => {

                    const mandatory = { name: 'Mandatory', children: [] };
                    const discretionary = { name: 'Discretionary', children: [] };
                    const functionMap = { Mandatory: new Map(), Discretionary: new Map() };
                    const titleCounts = new Map();
                    let mandatoryCount = 0;
                    let discretionaryCount = 0;
                    let mandatoryValue = 0;
                    let discretionaryValue = 0;
                    const mandatoryContributors = [];
                    const discretionaryContributors = [];

                    const tid = row['Treasury Identification Number']?.trim();
                    const tidParse=tid.split('-');
                    const title = row['Title']?.trim();
                    const mandDisc = row['Discretionary or mandatory']?.trim();
                    const majorCat = row['Major spending category']?.trim();
                    const agency = row['Agency']?.trim();
                    const bureau = row['Bureau']?.trim();
                    const func = row['Function']?.trim();
                    const subfunc = row['Subfunction']?.trim();
                    const offBudget = row['Off-budget?'] || "on";
                    const subPath = functionCodeToName.get(func) || [func,func];
                    const subSubPath = subfunctionCodeToName.get(subfunc);
                    const path=[
                                'Budget',
                            mandDisc,
                            offBudget,
                            agency,
                            bureau,
                            majorCat,
                            subPath[0],
                            subPath[1],
                            title,
                            tid
                    ];
                    const value = parseFloat(row[`${selectedYear}-${selectedType}`]) || 0;

                    if (!tid || !title || !agency || !func || !subfunc || isNaN(value) || !mandDisc) return;

                    totalValue += value;
                    const isMandatory = mandDisc.toLowerCase() === 'mandatory';
                    if (isMandatory) {
                        mandatoryCount++;
                        mandatoryValue += Math.abs(value);
                        mandatoryContributors.push({ tid, title, agency, func, subfunc, value: Math.abs(value) });
                    } else {
                        discretionaryCount++;
                        discretionaryValue += Math.abs(value);
                        discretionaryContributors.push({ tid, title, agency, func, subfunc, value: Math.abs(value) });
                    }

                    const key = path.join('/');
                    if (results[key]) 
                            results[key] += value;
                    else
                            results[key] = value;
                    resultsArr.push({
                            key,
                            path,
                            value
                    });
                    BudgetNode.addPathToRoot(path, tid, value);
                });

        }});

    return BudgetNode.getRoot();
}

function formatNumber(value) {
    if (value >= 1_000_000) {
        return `$${(value / 1_000_000).toFixed(1)}T`;
    } else if (value >= 1_000) {
        return `$${(value / 1_000).toFixed(1)}B`;
    } else if (value >= 1) {
        return `$${value.toFixed(1)}M`;
    } else {
        return `$${(value * 1_000).toFixed(0)}K`;
    }
}


// Generate a color palette for your agencies
function generateColors(n) {
    const colors = [];
    for (let i = 0; i < n; i++) {
        const hue = (i * 360 / n) % 360;
        colors.push(`hsl(${hue}, 70%, 50%)`);
    }
    return colors;
}


function renderSunburst(rootNode) {
    // Constants for layout
    const width = 800;           // SVG width and height
    const radius = width / 2;    // Radius of the sunburst
    const agencyDepth = 4;       // Depth where agencies are (adjust if needed)

    // Helper to generate distinct colors for agencies
    function generateColors(n) {
        const colors = [];
        for (let i = 0; i < n; i++) {
            const hue = (i * 360 / n) % 360;
            colors.push(`hsl(${hue}, 70%, 50%)`);
        }
        return colors;
    }

    // Set up the SVG
    const svg = d3.select('#sunburst')
        .html('')                             // Clear any existing content
        .attr('width', width)
        .attr('height', width)
        .append('g')
        .attr('transform', `translate(${width / 2},${width / 2})`);

    // Build the hierarchy from BudgetNode
    const root = d3.hierarchy(rootNode, d => d.children)  // Use children getter
        .sum(d => Math.abs(d.value || 0))                 // Absolute values for full circle
        .sort((a, b) => b.value - a.value);               // Sort for better layout

console.log('Root name:', root.data.name);
if (root.children) {
    console.log('First child name:', root.children[0].data.name);
}

  // Collect unique agency names at agencyDepth for coloring
    const agencies = new Set();
    root.each(d => {
        if (d.depth === agencyDepth) agencies.add(d.data.name);
    });
    const colorPalette = generateColors(agencies.size);
    const color = d3.scaleOrdinal()
        .domain(Array.from(agencies))
        .range(colorPalette);

    // Set up the partition layout
    const partition = d3.partition()
        .size([2 * Math.PI, radius * radius]);
    partition(root);

    // Define the arc generator
    const arc = d3.arc()
        .startAngle(d => d.x0)
        .endAngle(d => d.x1)
        .innerRadius(d => Math.sqrt(d.y0))
        .outerRadius(d => Math.sqrt(d.y1));

function averageColors(colors) {
    let r = 0, g = 0, b = 0;
    colors.forEach(color => {
        const rgb = d3.color(color).rgb();
        r += rgb.r;
        g += rgb.g;
        b += rgb.b;
    });
    const count = colors.length;
    r = Math.round(r / count);
    g = Math.round(g / count);
    b = Math.round(b / count);
    return `rgb(${r},${g},${b})`;
}
function getName(d) {
    if (d.name) return d.name;                    // Direct name (unlikely in D3 hierarchy)
    if (d.data && d.data.name) return d.data.name; // Standard D3 hierarchy
    if (d.data && d.data.data && d.data.data.name) return d.data.data.name; // Nested data
    return 'unknown';                            // Fallback
}

function calcColor(d) {
    if (d.depth >= agencyDepth) {
        const agencyAncestor = d.ancestors().find(a => a.depth === agencyDepth);
        const agencyName = agencyAncestor ? getName(agencyAncestor) : 'default';
        const agencyColor = color(agencyName);
        return d3.interpolate(agencyColor, '#fff')(0.2 * (d.depth - agencyDepth));
    } else {
        const descendantAgencies = d.descendants().filter(desc => desc.depth === agencyDepth);
        if (descendantAgencies.length === 0) return '#ccc';
        const agencyColors = descendantAgencies.map(agency => color(getName(agency)));
        return averageColors(agencyColors);
    }
}

function update() {
    const descendants = root.descendants().filter(d => (d.x1 - d.x0) > 0.05 && d.value > 0);
    const labelData = descendants; // Adjust filtering if needed

    // Paths (with color fix)
    svg.selectAll('path')
        .data(descendants, d => (d.name || 'unknown') + d.depth)
        .join('path')
        .attr('d', arc)
        .attr('fill', calcColor)
        .attr('stroke', '#fff')
        .attr('stroke-width', 1)
        .on('click', clicked);

        // Labels (with error fix)
    svg.selectAll('text')
        .data(labelData, d => (d.name || d.data.name || d.data.data.name || 'unknown') + d.depth)
        .join('text')
        .attr('transform', d => {
            const x = (d.x0 + d.x1) / 2 * 180 / Math.PI;
            const y = (Math.sqrt(d.y0) + Math.sqrt(d.y1)) / 2;
            return `rotate(${x - 90}) translate(${y},0) rotate(${x < 180 ? 0 : 180})`;
        })
        .attr('dy', '0.35em')
        .attr('text-anchor', 'middle')
        .text(d => {
            const name = d.name || d.data.name || d.data.name || 'Unknown';
            return name.length < 20 ? name : name.slice(0, 17) + '...';
        })
        .style('font-size', '12px')
        .style('pointer-events', 'none');
}
    // Click handler for zooming
    function clicked(event, p) {
        const focusedRoot = p === root ? root : d3.hierarchy(p, d => d.children)
            .sum(d => Math.abs(d.value || 0))
            .sort((a, b) => b.value - a.value);
        partition(focusedRoot);

        // Animate the arcs
        svg.selectAll('path')
            .transition()
            .duration(750)
            .attrTween('d', d => {
                const match = focusedRoot.descendants().find(f => f.name === d.data.name && f.depth === d.depth);
                if (!match) return () => arc(d);
                const interpolate = d3.interpolate({ x0: d.x0, x1: d.x1, y0: d.y0, y1: d.y1 }, match);
                return t => arc(interpolate(t));
            });

        // Update the view after animation
        setTimeout(() => update(focusedRoot), 750);
    }

    // Render the initial view
    update(root);
}


function showYearPopup() {
    const popup = document.getElementById('yearPopup');
    const yearSelect = document.getElementById('yearSelect');
    const typeSelect = document.getElementById('typeSelect');
    yearSelect.value = selectedYear;
    typeSelect.value = selectedType;
    popup.style.display = 'block';
}

function confirmYear() {
    const yearSelect = document.getElementById('yearSelect');
    const typeSelect = document.getElementById('typeSelect');
    selectedYear = yearSelect.value;
    selectedType = typeSelect.value;
    hideYearPopup();
    loadAndRender();
}

function hideYearPopup() {
    document.getElementById('yearPopup').style.display = 'none';
}

function statusUpdate(message) {
    //gel('status').html(`<span class="flex items-center text-sm"><img src="/assets/images/loading.svg" class="size-6" alt="Loading...">${message}</span>`);


}

function gel(id) {
        return document.getElementById(id)
}

async function render(budgetText, functionSubfunctionText, selectedYear, selectedType) {
    dataReady=true;    
    statusUpdate("Processing data...");
    const budget = processBudgetCSV(budgetText, functionSubfunctionText, selectedYear, selectedType);
    statusUpdate("Generating Graph...");
    if (!budget || !budget.children || budget.children.length === 0) {
        throw new Error('Invalid hierarchy: No children found');
        }
    renderSunburst(budget);
    statusUpdate("Done...");       
   
}

async function loadAndRender() {
    loadData("CBOs_January_2025_Baseline_Projections.csv", "Budget_Functions_and_Subfunctions.csv")
        .then(({ budgetText, functionSubfunctionText }) => {
        
                render(budgetText, functionSubfunctionText, selectedYear, selectedType);
 
    })
        .catch(error => console.error('Error:', error.message));
}

document.addEventListener('DOMContentLoaded', loadAndRender);