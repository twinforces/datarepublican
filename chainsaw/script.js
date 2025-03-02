let selectedYear = '2025';
let selectedType = 'ba';

document.getElementById('selectYearBtn').addEventListener('click', showYearPopup);
document.getElementById('confirmYearBtn').addEventListener('click', confirmYear);
document.getElementById('cancelYearBtn').addEventListener('click', hideYearPopup);

async function loadData(budgetCsvFile, functionSubfunctionCsvFile) {
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

    console.log('Function mappings:', Array.from(functionCodeToName.entries()));
    console.log('Subfunction mappings:', Array.from(subfunctionCodeToName.entries()));

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

async function processBudgetCSV(budgetCsvString, functionSubfunctionCsvString, year, type) {
    const { functionCodeToName, subfunctionCodeToName } = processFunctionSubfunctionCSV(functionSubfunctionCsvString);


    let results={};
    let resultsArr=[];
    let levelKeys={};
     let totalValue = 0;
   await new Promise((resolve, reject) => {
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
        const offBudget = row['Off-budget?'];
        const subPath = functionCodeToName.get(func) || [func,func];
        const subSubPath = subfunctionCodeToName.get(subfunc);
        const path=[
                mandDisc,
                agency,
                bureau,
                majorCat,
                subPath[0],
                subPath[1],
                title
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
        storePath(path,levelKeys, value);
    });

}});

    return {path: ['Budget'],
            value: totalValue,
            children: levelKeys,
            name: 'Budget'};
});

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

function renderSunburst(jsonData) {
    const width = 800;
    const radius = width / 6;

    const svg = d3.select('#sunburst')
        .html('')
        .attr('width', width)
        .attr('height', width);

    const g = svg.append('g')
        .attr('transform', `translate(${width / 2},${width / 2})`);

    const partition = d3.partition()
        .size([2 * Math.PI, radius * radius]);

    const root = d3.hierarchy(jsonData)
        .eachBefore(d => {
            if (d.depth === 0) d.value = jsonData.value;
            else if (d.data.value !== undefined) d.value = d.data.value;
        })
        .sort((a, b) => b.value - a.value);

    console.log(`Root value: ${root.value}`);
    partition(root);
    console.log(`Root angles: x0: ${root.x0}, x1: ${root.x1}`);

    const arc = d3.arc()
        .startAngle(d => d.x0)
        .endAngle(d => d.x1)
        .innerRadius(d => Math.sqrt(d.y0))
        .outerRadius(d => Math.sqrt(d.y1));

    const color = d3.scaleOrdinal()
        .domain(['Mandatory', 'Discretionary'])
        .range(['#1f77b4', '#ff7f0e']);

    function update(focusNode, abbreviated = false) {
        const descendants = focusNode.descendants().slice(1);

        const pathThreshold = abbreviated ? 0.3 : 0.05; // Relaxed for full display
        const labelThreshold = abbreviated ? 0.3 : 0.05; // Relaxed for full display
        const labelDepthLimit = abbreviated ? 1 : 3; // Increased for full display

        const pathData = descendants.filter(d => (d.x1 - d.x0) > pathThreshold && d.value > 0);

        const path = g.selectAll('path')
            .data(pathData, d => d.ancestors().map(a => a.data.name).reverse().join('-'))
            .join('path')
            .attr('d', arc)
            .attr('fill', d => {
                if (d.depth === 1 && focusNode === root) return color(d.data.name);
                const parentColor = color(d.ancestors().find(a => a.depth === 1 && focusNode === root)?.data.name || 'Mandatory');
                return d3.interpolate(parentColor, '#fff')(0.2 * (d.depth - (focusNode === root ? 1 : 0)));
            })
            .attr('stroke', '#fff')
            .attr('stroke-width', 1)
            .attr('opacity', 1)
            .on('click', clicked);

        path.selectAll('title').remove();
        path.append('title')
            .text(d => `${d.ancestors().map(d => d.data.displayName || d.data.name).reverse().join(' > ')}\n${formatNumber(d.value)}`);

        const labelData = descendants.filter(d => {
            const relativeDepth = d.depth - focusNode.depth;
            const passesFilter = (d.x1 - d.x0) > labelThreshold && d.value > 0 && relativeDepth <= labelDepthLimit;
            if (!passesFilter && relativeDepth <= labelDepthLimit) {
                console.log(`Label filtered out: ${d.data.displayName || d.data.name}, angle: ${(d.x1 - d.x0).toFixed(3)}, depth: ${relativeDepth}`);
            }
            return passesFilter;
        });

        g.selectAll('text')
            .data(labelData, d => d.ancestors().map(a => a.data.name).reverse().join('-'))
            .join('text')
            .attr('transform', d => {
                const x = (d.x0 + d.x1) / 2 * 180 / Math.PI;
                const y = (Math.sqrt(d.y0) + Math.sqrt(d.y1)) / 2;
                return `rotate(${x - 90}) translate(${y},0) rotate(${x < 180 ? 0 : 180})`;
            })
            .attr('dy', '0.35em')
            .attr('text-anchor', 'middle')
            .text(d => {
                const displayName = d.data.displayName || d.data.name;
                return displayName.length < 20 ? displayName : displayName.slice(0, 17) + '...';
            })
            .style('font-size', d => `${Math.min(12, Math.max(8, (d.x1 - d.x0) * 30))}px`)
            .style('pointer-events', 'none')
            .style('opacity', 0)
            .transition()
            .duration(500)
            .style('opacity', 1);
    }

    function clicked(event, p) {
        focus = (p === focus) ? (focus.parent || root) : p;
        const focusedRoot = d3.hierarchy(focus.data, d => d.children)
            .eachBefore(d => {
                if (d.data.value !== undefined) d.value = d.data.value;
            })
            .sort((a, b) => b.value - a.value);
        partition.size([2 * Math.PI, radius * radius])(focusedRoot);

        const focusMap = new Map(focusedRoot.descendants().map(d => {
            const path = d.ancestors().map(a => a.data.name).reverse().join('-');
            return [path, d];
        }));
        const k = radius / Math.sqrt(focusedRoot.y1 || 1);
        const angleRange = focusedRoot.x1 - focusedRoot.x0 || 2 * Math.PI;
        const angleScale = 2 * Math.PI / angleRange;

        const activeDescendants = focusedRoot.descendants().filter(d => d.value > 0);

        const arcTween = d => {
            if (!d.value) return () => arc(d);
            const path = d.ancestors().map(a => a.data.name).reverse().join('-');
            const match = focusMap.get(path) || d;
            const xd = d3.interpolate(d.x0, match.x0 * angleScale);
            const x1d = d3.interpolate(d.x1, match.x1 * angleScale);
            const yd = d3.interpolate(d.y0, match.y0 * k * k);
            const y1d = d3.interpolate(d.y1, match.y1 * k * k);
            return t => arc({ x0: xd(t), x1: x1d(t), y0: yd(t), y1: y1d(t) });
        };

        console.log('Focus:', focus.data.displayName || focus.data.name, 'y1:', focusedRoot.y1, 'descendants:', focusedRoot.descendants().length, 'active:', activeDescendants.length);
        console.log('Focused angles:', focusedRoot.x0, focusedRoot.x1, 'angleRange:', angleRange, 'angleScale:', angleScale);

        const largeSegments = g.selectAll('path')
            .filter(d => d.value > 0 && (d.x1 - d.x0) > 0.3);

        g.selectAll('path')
            .filter(d => d.value > 0 && (d.x1 - d.x0) <= 0.3)
            .transition()
            .duration(200)
            .style('opacity', 0);

        g.selectAll('text')
            .transition()
            .duration(200)
            .style('opacity', 0);

        update(focusedRoot, true);

        const transition = largeSegments.transition()
            .duration(500)
            .delay((d, i) => i * 5)
            .attrTween('d', arcTween);

        Promise.all(transition.end()).then(() => update(focusedRoot, false)).catch(() => {
            setTimeout(() => update(focusedRoot, false), 1000);
        });
    }

    let focus = root;
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

function loadAndRender() {
    loadData("CBOs_January_2025_Baseline_Projections.csv", "Budget_Functions_and_Subfunctions.csv")
        .then(({ budgetText, functionSubfunctionText }) => {
            const jsonData = processBudgetCSV(budgetText, functionSubfunctionText, selectedYear, selectedType);
            if (!jsonData || !jsonData.children || jsonData.children.length === 0) {
                throw new Error('Invalid hierarchy: No children found');
            }
            renderSunburst(jsonData);
        })
        .catch(error => console.error('Error:', error.message));
}

document.addEventListener('DOMContentLoaded', loadAndRender);