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
    const functionCodeToName = new Map();
    const subfunctionCodeToName = new Map();

    let currentFunctionCode = null;
    let currentFunctionName = null;
    let currentDeptName = null;
    let currentDepCode = null;
    let lookupMap={};
    
     Papa.parse(csvString, {
            header:true,
            skipEmptyLines: true,
            complete: results => {
                results.data.forEach(row => {
                
        let departmentName = row['DepartmentName'] || currentDeptName || "";
        let functionName = row['SubFunction']?.trim();
        let depCode = row['DepCode']  || currentDepCode || "";
        let subCode = row['SubCode']  || currentDepCode || "999";
        currentDeptName=departmentName;
        currentDepCode = depCode;
        if (!functionName.length) 
        {
                functionName = departmentName;
        }
        if (!departmentName && !! !functionName) return;

        currentDeptName = departmentName.replace(/"/g,'');
        functionCodeToName.set(depCode,departmentName);
        subfunctionCodeToName.set(subCode,functionName);
        });
    }});

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
     let rowNumber=0;
     
     // counters so we get an idea
     const titleAgg ={};
     const catAgg={};
     const agencyAgg={};
     const bureauAgg={};
     const funcAgg={};
      const subfuncAgg={};
     const subPathAgg={};
     const subSubAgg={};
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
                    titleAgg[title] =1;
                    const mandDisc = row['Discretionary or mandatory']?.trim();
                    const majorCat = row['Major spending category']?.trim();
                     catAgg[majorCat]=1;
                    let agency = row['Agency']?.trim();
                     agencyAgg[agency]=1;
                    let bureau = row['Bureau']?.trim();
                    bureau=bureau.replace(/Office of /,'')
                     bureauAgg[bureau]=1;
                    const func = row['Function']?.trim();
                     funcAgg[func]=1;
                    const subfunc = row['Subfunction']?.trim();
                     subfuncAgg[subfunc]=1;
                    const offBudget = row['Off-budget?'] || "on";
                    const subPath = functionCodeToName.get(func) || [func,func];
                     subPathAgg[subPath]=1;
                    const subSubPath = subfunctionCodeToName.get(subfunc);
                     subSubAgg[subSubPath]=1;
                    const tidSplit = tid.split(/-/);
                    agency = agency.split(/--/)[0]; //DOD has to be different
                    agency = agency.replace(/Department of (the )*/,' '); // less wordy
                    const path=[
                                'Budget',
                            majorCat,
                            subPath,
                            agency,
                            subSubPath,
                            bureau,
                            mandDisc,
                            title,
                            offBudget,
                            ++rowNumber
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
                    resultsArr.push({
                            key,
                            path,
                            value
                    });
                    BudgetNode.addPathToRoot(path, tid, value);
                  });

        }});
        
     console.log(`titleAgg ${Object.keys(titleAgg).length} = ${Object.keys(titleAgg)}`);
     console.log(`catAgg ${Object.keys(catAgg).length} = ${Object.keys(catAgg)}`);
     console.log(`agencyAgg ${Object.keys(agencyAgg).length} = ${Object.keys(agencyAgg)}`);
     console.log(`bureauAgg ${Object.keys(bureauAgg).length} = ${Object.keys(bureauAgg)}`);
     console.log(`funcAgg {$Object.keys(funcAgg).length} = ${Object.keys(funcAgg)}`);
     console.log(`subfuncAgg ${Object.keys(subfuncAgg).length} = ${Object.keys(subfuncAgg)}`);
     console.log(`subPathAgg ${Object.keys(subPathAgg).length} = ${Object.keys(subPathAgg)}`);
     console.log(`subSubAgg ${Object.keys(subSubAgg).length} = ${Object.keys(subSubAgg)}`);

    BudgetNode.setupModel(); // sets up color
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

function getName(d) {
        let start = d;
        let recCount=0;
        while (start && !start.name && start.data)
        {
                start=start.data; // recurse down
                if (start.name)
                    return start.name;
                recCount++; // depth;
        
        }
        return (start && start.name) || `unknown ${recCount}`;
}
function getColor(d) {
        let start = d;
        let recCount=0;
        while (start && !start.color && start.data)
        {
                start=start.data; // recurse down
                if (start.color)
                    return start.color;
                recCount++; // depth;
        
        }
        return (start && start.color) || "pink";
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

function renderSunburst(rootNode) {
    const width = 800;
    const radius = width / 2;

    // Initialize SVG
    const svg = d3.select('#sunburst')
        .html('')
        .attr('width', width)
        .attr('height', width)
        .append('g')
        .attr('transform', `translate(${width / 2},${width / 2})`);

    // Build hierarchy
    const root = d3.hierarchy(rootNode, d => d.children)
        .sum(d => d.value || 0)
        .sort((a, b) => b.value - a.value);

    // Define partition layout
    const partition = d3.partition()
        .size([2 * Math.PI, radius * radius]);
    partition(root);

    // Define arc generator
    const arc = d3.arc()
        .startAngle(d => d.x0)
        .endAngle(d => d.x1)
        .innerRadius(d => Math.sqrt(d.y0))
        .outerRadius(d => Math.sqrt(d.y1));

    // Update function to render the visualization
    function update(focus) {
        const descendants = focus.descendants().filter(d => d.depth > 0 && d.value > 0);

        // Render paths (arcs)
      svg.selectAll("path")
          .data(root.descendants().filter(d => d.value > 0))
          .enter()
          .append("path")
          .attr("d", d3.arc()
            .startAngle(d => d.x0)
            .endAngle(d => d.x1)
            .innerRadius(d => Math.sqrt(d.y0))
            .outerRadius(d => Math.sqrt(d.y1)))
          .attr("fill", d => d.data.color)
          .append("title")
              .text(d => `${d.data.id}: ${formatNumber(d.value)}`);

        const fdescendants = focus.descendants().filter(d => d.depth > 0 && (d.x1 - d.x0) > 0.05 && d.value > 0);
        // Render labels
        svg.selectAll('text')
            .data(fdescendants, d => d.data.path.join('/') || 'root') // Same unique key
            .join('text')
            .attr('transform', d => {
                const x = (d.x0 + d.x1) / 2 * 180 / Math.PI;
                const y = (Math.sqrt(d.y0) + Math.sqrt(d.y1)) / 2;
                return `rotate(${x - 90}) translate(${y},0) rotate(${x < 180 ? 0 : 180})`;
            })
            .attr('dy', '0.35em')
            .attr('text-anchor', 'middle')
            .text(d => {
                const name = getName(d) || 'Unknown';
                return name.length < 20 ? name : `${name}`.slice(0, 17) + '...';
            })
            .style('font-size', '12px')
            .style('pointer-events', 'none');
    }

    // Click handler
    function clicked(event, p) {
        // If clicking the current root, reset to original root; otherwise, focus on clicked node
        const focusedRoot = p === root ? root : d3.hierarchy(p.data, d => d.children)
            .sum(d => Math.abs(d.value || 0))
            .sort((a, b) => b.value - a.value);
        partition(focusedRoot);

        // Transition paths
        svg.selectAll('path')
            .transition()
            .duration(750)
            .attrTween('d', d => {
                // Find matching node in new hierarchy by path
                const match = focusedRoot.descendants().find(f => f.data.path.join('/') === d.data.path.join('/'));
                if (!match) return () => arc(d); // Keep current arc if no match
                const interpolate = d3.interpolate({ x0: d.x0, x1: d.x1, y0: d.y0, y1: d.y1 }, match);
                return t => arc(interpolate(t));
            });

        // Update visualization after transition
        setTimeout(() => update(focusedRoot), 750);
    }

    // Initial render
    update(root);
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