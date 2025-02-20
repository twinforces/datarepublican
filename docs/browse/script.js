// Full data from CSV
let charities = {};             // EIN -> { name, receipt_amt, govt_amt, contrib_amt, grant_amt }
let grants = {};                // "filer~grantee" -> totalGrant
let totalCharitiesCount = 0;    // total charities read
let totalGrantsCount = 0;       // total edges read from CSV

// Filter state
let activeEINs = [];
let activeKeywords = [];

// BFS / data-ready
let dataReady = false;
let panZoomInstance = null;

// Custom graph state
let customGraphEdges = null;    // array of objects { filer, grantee, amt }
let customTitle = null;         // optional custom title from the query param

// Additional global variables
let simulation = null;
let nodeElements = null;
let selectedSearchIndex = 0;    // Track selected search result

// Sankey state (MOVED TO GLOBAL SCOPE - FIX)
let currentData = { nodes: [], links: [] };
let nodeMap = null;             // Will be EIN -> charity object
let linkMap = null;             // Will be "filer~grantee" -> grant object
let expandedOutflows = new Map(); // Tracks displayed outflows per source
let outflows = new Map();       // EIN -> total outflow
const TOP_N_INITIAL = 5;        // Top nodes to start with
const TOP_N_OUTFLOWS = 5;       // Outflows per expansion

// Global SVG reference for zoom (FIXED: Added to maintain scope)
let svg = null;
let zoom = null; // ADDED: Declare globally at the top of the file (line 51)

$(document).ready(function() {
    // Parse query params first
    parseQueryParams();

    // Load data once
    loadData().then(() => {
        dataReady = true;
        $('#status').text('Data loaded.').css('color', 'black');
        generateGraph();
    }).catch(err => {
        console.error(err);
        $('#status').text('Failed to load data.').css('color', 'red');
    });

    // EIN handling
    $('#addEinBtn').on('click', addEINFromInput);
    $('#einInput').on('keypress', e => {
        if (e.key === 'Enter') addEINFromInput();
    });
    $('#clearEINsBtn').on('click', () => {
        activeEINs = [];
        renderActiveEINs();
        updateQueryParams();
        generateGraph();
    });

    // Keywords
    $('#addFilterBtn').on('click', addKeywordFromInput);
    $('#keywordInput').on('keypress', e => {
        if (e.key === 'Enter') addKeywordFromInput();
    });
    $('#clearFiltersBtn').on('click', () => {
        activeKeywords = [];
        renderActiveKeywords();
        updateQueryParams();
        generateGraph();
    });

    // Download SVG
    $('#downloadBtn').on('click', downloadSVG);

    // Expand/collapse "How it works" (BFS-only)
    $('#howItWorksBtn').on('click', function() {
        const $list = $('#howItWorksList');
        const $btn = $(this);

        if ($list.height() === 0) {
            $list.css('height', 'auto');
            const autoHeight = $list.height();
            $list.height(0);
            $list.height(autoHeight);
            $btn.text('Hide details');
        } else {
            $list.height(0);
            $btn.text('How it works');
        }
    });

    // Add search handlers
    const searchInput = document.getElementById('searchInput');
    const searchResults = document.getElementById('searchResults');
    const clearButton = document.getElementById('clearSearch');

    const newSearchInput = searchInput.cloneNode(true);
    const newSearchResults = searchResults.cloneNode(true);
    const newClearButton = clearButton.cloneNode(true);

    searchInput.parentNode.replaceChild(newSearchInput, searchInput);
    searchResults.parentNode.replaceChild(newSearchResults, searchResults);
    clearButton.parentNode.replaceChild(newClearButton, clearButton);

    newSearchInput.addEventListener('input', handleSearch);
    newSearchInput.addEventListener('blur', handleSearchBlur);
    newSearchResults.addEventListener('click', handleSearchClick);
    newSearchInput.addEventListener('keydown', handleSearchKeydown);

    newClearButton.addEventListener('click', () => {
        newSearchInput.value = '';
        newSearchInput.focus();
        handleSearch({ target: newSearchInput });
    });

    // Add resize event listener
    $(window).on('resize', function() {
        if (dataReady) {
            generateGraph();
        }
    });
});

function addEINFromInput() {
    let val = $('#einInput').val().trim();
    val = val.replace(/[-\s]/g, ''); // Remove hyphens and spaces first
    
    if (!/^\d{9}$/.test(val)) {
        alert("EIN must be 9 digits after removing dashes/spaces.");
        return;
    }
    if (!charities[val]) {
        console.warn("EIN not found in charities.csv (still adding).");
    }
    if (!activeEINs.includes(val)) {
        activeEINs.push(val);
    }
    $('#einInput').val('');
    renderActiveEINs();
    updateQueryParams();
    generateGraph();
}

function renderActiveEINs() {
    const $c = $('#activeEINs');
    $c.empty();
    $('#clearEINsBtn').toggle(activeEINs.length > 0);

    activeEINs.forEach(ein => {
        const $tag = $('<div class="filter-tag flex items-center gap-0.5 rounded border border-blue bg-blue/10 text-blue rounded-md px-2 py-1 text-xs"></div>');
        const $text = $('<span></span>').text(ein.slice(0,2) + '-' + ein.slice(2));
        const $rm = $('<span class="remove-filter opacity-50 hover:opacity-100 size-5 -my-0.5 -mr-1 cursor-pointer">{% include close.html %}</span>').attr('data-ein', ein);
        $rm.on('click', function() {
            const rem = $(this).attr('data-ein');
            activeEINs = activeEINs.filter(x => x !== rem);
            renderActiveEINs();
            updateQueryParams();
            generateGraph();
        });
        $tag.append($text).append($rm);
        $c.append($tag);
    });
}

function addKeywordFromInput() {
    const kw = $('#keywordInput').val().trim();
    if (kw.length > 0) {
        activeKeywords.push(kw.toLowerCase());
        $('#keywordInput').val('');
        renderActiveKeywords();
        updateQueryParams();
        generateGraph();
    }
}

function renderActiveKeywords() {
    const $c = $('#activeFilters');
    $c.empty();
    $('#clearFiltersBtn').toggle(activeKeywords.length > 0);

    activeKeywords.forEach(kw => {
        const $tag = $('<div class="filter-tag flex items-center gap-0.5 rounded border border-blue bg-blue/10 text-blue rounded-md px-2 py-1 text-xs"></div>');
        const $text = $('<span></span>').text(kw);
        const $rm = $('<span class="remove-filter opacity-50 hover:opacity-100 size-5 -my-0.5 -mr-1 cursor-pointer">{% include close.html %}</span>').attr('data-kw', kw);
        $rm.on('click', function() {
            const rem = $(this).attr('data-kw');
            activeKeywords = activeKeywords.filter(x => x !== rem);
            renderActiveKeywords();
            updateQueryParams();
            generateGraph();
        });
        $tag.append($text).append($rm);
        $c.append($tag);
    });
}

function downloadSVG() {
    const svgEl = document.querySelector('#graph-container svg');
    if (!svgEl) {
        alert('No SVG to download yet.');
        return;
    }
    const svgData = new XMLSerializer().serializeToString(svgEl);
    const blob = new Blob([svgData], { type: 'image/svg+xml;charset=utf-8' });
    const url = URL.createObjectURL(blob);

    const link = document.createElement('a');
    link.href = url;
    link.download = 'charity_graph.svg';
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    URL.revokeObjectURL(url);
}

function parseQueryParams() {
    const params = new URLSearchParams(window.location.search);
    customTitle = params.get('title') || null;

    const customParam = params.get('custom_graph');
    if (customParam) {
        const trimmed = customParam.replace(/;+$/, '');
        const segments = trimmed.split(';');
        let edgeList = [];

        segments.forEach(s => {
            const parts = s.split(',');
            if (parts.length !== 3) return;
            let [filer, grantee, amt] = parts.map(x => x.trim());
            filer = filer.replace(/[-\s]/g, '');
            grantee = grantee.replace(/[-\s]/g, '');
            const num = parseInt(amt, 10);
            if (!isNaN(num)) {
                edgeList.push({ filer, grantee, amt: num });
            }
        });
        customGraphEdges = edgeList;

        $('.bfs-only').hide();
        $('#excluded-info').hide();

        const addendum = `
            <br/><br/>
            <p style="font-weight: bold; color: red; background: yellow; text-align: center; padding: 20px; border: 5px solid black;">
                🚨🚨🚨 <strong>WARNING! <a href="https://www.dictionary.com/browse/disclaimer" title="Link to definition if you don't know the word." style="color: inherit; text-decoration: underline;">DISCLAIMER</a>! PAY ATTENTION! HAVE SOME READING <a href="https://www.dictionary.com/browse/comprehension" title="Look it up if you must." style="color: inherit; text-decoration: underline;">COMPREHENSION</a>! 📢📢📢</strong> 🚨🚨🚨
            </p>
            <p style="color: white; background: black; padding: 15px; text-align: center; border: 3px dashed red;">
                <strong>FUNDING IS <a href="https://www.dictionary.com/browse/fungible" title="Link to definition if you don't know the word." style="color: inherit; text-decoration: underline;">FUNGIBLE</a>!!!</strong> That means <span style="color: yellow; text-transform: uppercase;">USAID DOLLARS DO NOT <a href="https://www.dictionary.com/browse/literally" title="Link to definition if you don't know the word." style="color: inherit; text-decoration: underline;">LITERALLY</a> FLOW INTO THESE NGOS!!!</span> 
            </p>
            <p style="color: black; background: lime; padding: 10px; border: 5px dotted blue; text-align: center;">
                💰💰 Instead, the money <strong>MOVES</strong> through MULTIPLE LAYERS! 💰💰 <br/>
                Various entities handle it, shuffle it around, and redistribute it! 
            </p>
            <p style="color: purple; background: orange; font-weight: bold; text-align: center; padding: 20px; border: 5px solid pink;">
                So instead of <a href="https://www.dictionary.com/browse/obsess" title="Link to definition if you don't know the word." style="color: inherit; text-decoration: underline;">obsessing</a> over individual grants and NGOs, OPEN YOUR EYES 👀 to the 
                <span style="text-decoration: underline;">BROADER PATTERN of FUNDING <a href="https://www.dictionary.com/browse/distribution" title="Link to definition if you don't know the word." style="color: inherit; text-decoration: underline;">DISTRIBUTION</a> and <a href="https://www.dictionary.com/browse/influence" title="Link to definition if you don't know the word." style="color: inherit; text-decoration: underline;">INFLUENCE</a>!</span> <br/>
                🚨🚨 And YES—layers of <strong style="color: red;"><a href="https://www.dictionary.com/browse/accountability" title="Link to definition if you don't know the word." style="color: inherit; text-decoration: underline;">UNACCOUNTABILITY</a></strong> exist! 🚨🚨
            </p>
            <p style="color: black; background: cyan; text-align: center; padding: 15px; border: 5px double red;">
                🤔🤔 <strong>WHO'S TRULY <a href="https://www.dictionary.com/browse/dependent" title="Link to definition if you don't know the word." style="color: inherit; text-decoration: underline;">DEPENDENT</a> ON USAID?!</strong> 🤔🤔 <br/>
                <span style="color: red;">The ones who DON’T want it to be shut down! 🔥🔥🔥</span> <br/>
                <strong style="color: blue; text-transform: uppercase;">THIS IS COMMON SENSE!!!</strong> 🚨🚨🚨
            </p>
            <p style="font-weight: bold; text-align: center; color: white; background: red; padding: 25px; border: 10px solid black; text-transform: uppercase;">
                THIS IS A TOOL NOT A VERDICT! YOU HAVE BEEN WARNED! 🔥🔥🔥
            </p>
        `.trim();

        try {
            if (customTitle) {
                $('#instructions').html(`Displaying <strong>${customTitle}</strong>${addendum}`);
            } else {
                $('#instructions').html(`Displaying exact graph.${addendum}`);
            }
        } catch (e) {
            console.error('Error setting instructions:', e);
            $('#instructions').text('Error displaying instructions.');
        }
    } else {
        const einParam = params.get('eins');
        if (einParam) {
            let list = einParam.split(',');
            list.forEach(e => {
                let v = e.replace(/[-\s]/g, '');
                if (/^\d{9}$/.test(v)) {
                    if (!activeEINs.includes(v)) {
                        activeEINs.push(v);
                    }
                }
            });
        }
        const kwParam = params.get('keywords');
        if (kwParam) {
            let kws = kwParam.split(',');
            kws.forEach(k => {
                k = k.trim().toLowerCase();
                if (k && !activeKeywords.includes(k)) {
                    activeKeywords.push(k);
                }
            });
        }

        $('.bfs-only').show();
        $('#instructions').text(
            'Use the search and keywords to filter charities, then BFS expansion is performed automatically.'
        );

        renderActiveEINs();
        renderActiveKeywords();
    }
}

function updateQueryParams() {
    if (customGraphEdges) {
        return;
    }
    const params = new URLSearchParams();
    if (activeEINs.length > 0) {
        params.set('eins', activeEINs.join(','));
    }
    if (activeKeywords.length > 0) {
        params.set('keywords', activeKeywords.join(','));
    }
    const newUrl = window.location.pathname + '?' + params.toString();
    window.history.replaceState({}, '', newUrl);
}

function formatNumber(num) {
    return num.toString().replace(/\B(?=(\d{3})+(?!\d))/g, ",");
}

async function loadData() {
    $('#status').html('<span class="flex items-center text-sm"><img src="/assets/images/loading.svg" class="size-6" alt="Loading..."> Loading data...</span>');

    const charitiesZipBuf = await fetch('../expose/charities.csv.zip').then(r => r.arrayBuffer());
    const charitiesZip = await JSZip.loadAsync(charitiesZipBuf);
    const charitiesCsvString = await charitiesZip.file('charities_truncated.csv').async('string');

    await new Promise((resolve, reject) => {
        Papa.parse(charitiesCsvString, {
            header: true,
            skipEmptyLines: true,
            complete: results => {
                results.data.forEach(row => {
                    const ein = (row['filer_ein'] || '').trim();
                    if (!ein) return;
                    let rAmt = parseInt((row['receipt_amt'] || '0').trim(), 10);
                    if (isNaN(rAmt)) rAmt = 0;
                    charities[ein] = {
                        filer_ein: ein,
                        name: (row['filer_name'] || '').trim(),
                        xml_name: row['xml_name'],
                        receipt_amt: rAmt,
                        govt_amt: parseInt((row['govt_amt'] || '0').trim(), 10) || 0,
                        contrib_amt: parseInt((row['contrib_amt'] || '0').trim(), 10) || 0,
                        grant_amt: 0,
                        grant_in_index: 0,
                        grant_out_index: 0,
                        grant_count: 0
                    };
                });
                totalCharitiesCount = Object.keys(charities).length;
                resolve();
            },
            error: err => reject(err)
        });
    });

    const grantsZipBuf = await fetch('../expose/grants.csv.zip').then(r => r.arrayBuffer());
    const grantsZip = await JSZip.loadAsync(grantsZipBuf);
    const grantsCsvString = await grantsZip.file('grants_truncated.csv').async('string');

    await new Promise((resolve, reject) => {
        Papa.parse(grantsCsvString, {
            header: true,
            skipEmptyLines: true,
            complete: results => {
                let localEdges = {};
                let count = 0;
                results.data.forEach(row => {
                    const filer = (row['filer_ein'] || '').trim();
                    const grantee = (row['grant_ein'] || '').trim();
                    let amt = parseInt((row['grant_amt'] || '0').trim(), 10);
                    if (isNaN(amt)) amt = 0;
                    count++;
                    if (charities[filer] && charities[grantee]) {
                        const key = filer + '~' + grantee;
                        if (!localEdges[key]) localEdges[key] = { rawAmt: 0, relOutAmt: 0, relInAmt: 0 };
                        localEdges[key].rawAmt += amt;
                        charities[filer].grant_amt += amt;
                        charities[filer].grant_count += 1;
                        charities[filer].grant_out_index += 1;
                        charities[grantee].grant_in_index += 1;
                        localEdges[key].relOutAmt += amt / (charities[grantee].govt_amt + charities[grantee].contrib_amt);
                        localEdges[key].relInAmt += amt / (charities[filer].govt_amt + charities[filer].contrib_amt);
                        localEdges[key].filer_ein = filer;
                        localEdges[key].grantee_ein = grantee;
                        localEdges[key].filer = charities[filer];
                        localEdges[key].grantee = charities[grantee];
                    }
                });
                grants = localEdges;
                totalGrantsCount = count;
                resolve();
            },
            error: err => reject(err)
        });
    });
    console.log(totalCharitiesCount, "501c3s loaded");
    console.log(totalGrantsCount, "grants loaded");

    // Initialize Sankey maps after data load
    nodeMap = charities;
    linkMap = grants;
}

function generateGraph() {
    console.log('1. Starting graph generation');
    if (!dataReady) {
        alert("Data not loaded yet. Please wait.");
        return;
    }

    $('#loading').show();
    console.log('2. Loading icon shown');
    $('#graph-container svg').empty();
    console.log('3. Old graph cleared');

    const container = document.getElementById('graph-container');
    const width = container.offsetWidth;
    const height = container.offsetHeight || window.innerHeight * 0.7;
    console.log('4. Container dimensions calculated');

    // Ensure #graph exists in DOM (FIXED: Use #graph-container directly)
    svg = d3.select('#graph-container')
        .append('svg')
        .attr('id', 'graph')
        .attr('width', '100%')
        .attr('height', '100%')
        .style('display', 'block');
    console.log('5. SVG created');

    zoom = d3.zoom()
        .scaleExtent([0.1, 4])
        .on('zoom', (event) => {
            g.attr('transform', event.transform);
        });

    svg.call(zoom);

    const g = svg.append('g')
        .attr("transform", "translate(50, 50)");

    const sankey = d3.sankey()
        .nodeId(d => d.filer_ein)
        .nodeWidth(30) // Wider nodes for readability
        .nodePadding(30) // More space between nodes
        .extent([[0, 0], [width - 100, height - 100]]);

    // Reset Sankey state
    currentData = { nodes: [], links: [] };
    expandedOutflows.clear();
    outflows.clear();

    // Compute total outflows
    Object.entries(grants).forEach(([key, grant]) => {
        outflows.set(grant.filer_ein, (outflows.get(grant.filer_ein) || 0) + grant.rawAmt);
    });

    // Initialize with top N nodes
    const topNodes = Object.entries(charities)
        .map(([ein, charity]) => ({ filer_ein: ein, outflow: outflows.get(ein) || 0 }))
        .sort((a, b) => b.outflow - a.outflow)
        .slice(0, TOP_N_INITIAL)
        .map(n => charities[n.filer_ein]);

    topNodes.forEach(n => expandNode(n.filer_ein, true));

    renderSankey(g, sankey, svg);
    $('#loading').hide();
    console.log('Graph generation complete');
}

function expandNode(nodeId, isInitial = false) {
    if (!currentData.nodes.some(n => n.filer_ein === nodeId)) {
        currentData.nodes.push(nodeMap[nodeId]);
    }

    const allLinks = Object.entries(linkMap)
        .filter(([key, grant]) => grant.filer_ein === nodeId)
        .map(([key, grant]) => ({
            source: grant.filer_ein,
            target: grant.grantee_ein,
            value: grant.rawAmt
        }))
        .sort((a, b) => b.value - a.value);

    const displayedLinks = expandedOutflows.get(nodeId) || [];
    const remainingLinks = allLinks.filter(l => !displayedLinks.some(d => d.source === l.source && d.target === l.target));
    const newLinks = isInitial ? remainingLinks.slice(0, TOP_N_OUTFLOWS) : remainingLinks.slice(0, Math.min(TOP_N_OUTFLOWS, remainingLinks.length));
    const othersLinks = remainingLinks.slice(newLinks.length);

    const loopTracker = new Map();

    newLinks.forEach(l => {
        let targetId = l.target;
        if (currentData.nodes.some(n => n.filer_ein === l.target && n.filer_ein !== `${nodeId} (Others)`)) {
            const instance = (loopTracker.get(l.target) || 0) + 1;
            targetId = `${l.target} (Loop ${instance})`;
            loopTracker.set(l.target, instance);
            const newNode = { ...nodeMap[l.target], filer_ein: targetId };
            currentData.nodes.push(newNode);
            nodeMap[targetId] = newNode;
        } else if (!currentData.nodes.some(n => n.filer_ein === l.target)) {
            currentData.nodes.push(nodeMap[l.target]);
        }
        currentData.links.push({ source: l.source, target: targetId, value: l.value });
        displayedLinks.push(l);
    });

    expandedOutflows.set(nodeId, displayedLinks);

    const othersId = `${nodeId} (Others)`;
    if (othersLinks.length > 0 && !isInitial) {
        const othersValue = othersLinks.reduce((sum, l) => sum + l.value, 0);
        if (!currentData.nodes.some(n => n.filer_ein === othersId)) {
            currentData.nodes.push({ filer_ein: othersId, name: "Others", grant_amt: 0 });
        } else {
            currentData.links = currentData.links.filter(l => l.target !== othersId);
        }
        currentData.links.push({ source: nodeId, target: othersId, value: othersValue });
    } else {
        currentData.nodes = currentData.nodes.filter(n => n.filer_ein !== othersId);
        currentData.links = currentData.links.filter(l => l.target !== othersId);
    }
}

function expandOthers(sourceId) {
    expandNode(sourceId);
}

function renderSankey(g, sankey, svgRef) {
    const graph = sankey(JSON.parse(JSON.stringify(currentData)));

    g.selectAll("*").remove();

    // Draw links with improved styling
    const link = g.append("g")
        .attr("class", "links")
        .selectAll("path")
        .data(graph.links)
        .enter()
        .append("path")
        .attr("d", d3.sankeyLinkHorizontal())
        .attr("stroke-width", d => Math.max(2, d.width)) // Minimum width for visibility
        .attr("stroke", d => d.target.filer_ein.includes("(Loop)") ? "#ff4444" : "#ccc")
        .attr("stroke-opacity", 0.6);

    // Draw nodes with improved styling
    const node = g.append("g")
        .attr("class", "nodes")
        .selectAll("rect")
        .data(graph.nodes)
        .enter()
        .append("rect")
        .attr("x", d => d.x0)
        .attr("y", d => d.y0)
        .attr("height", d => Math.max(20, d.y1 - d.y0)) // Minimum height for readability
        .attr("width", d => d.x1 - d.x0)
        .attr("class", d => d.filer_ein.includes("(Loop)") ? "node loop" : d.filer_ein.includes("(Others)") ? "node others" : "node")
        .on("click", (event, d) => {
            if (d.filer_ein.includes("(Others)")) {
                expandOthers(d.filer_ein.split(" (Others)")[0]);
            } else {
                expandNode(d.filer_ein);
            }
            renderSankey(g, sankey, svgRef); // Pass svgRef for zoom
        });

    // Draw labels with improved wrapping and truncation
    g.selectAll("text")
        .data(graph.nodes)
        .enter()
        .append("text")
        .attr("x", d => d.x0 + (d.x1 - d.x0) / 2)
        .attr("y", d => d.y0 - 5)
        .attr("text-anchor", "middle")
        .text(d => {
            const maxLength = 20; // Truncate long names
            return d.name.length > maxLength ? d.name.substring(0, maxLength - 3) + "..." : d.name || d.filer_ein;
        })
        .style("font-size", "12px")
        .call(wrapText, 100); // Wrap text if too long

    // Tooltips
    node.append("title")
        .text(d => `${d.name || d.filer_ein}\nOutflow: ${formatNumber(outflows.get(d.filer_ein) || 0)}\nValue: ${formatNumber(d.grant_amt || 0)}`);

    // Initial zoom out for better overview (FIXED: Use svgRef)
    if (svgRef) {
        svgRef.call(zoom.transform, d3.zoomIdentity.scale(0.5));
    }
}

function wrapText(text, width) {
    text.each(function() {
        const text = d3.select(this);
        const words = text.text().split(/\s+/).reverse();
        let word;
        let line = [];
        let lineHeight = 1.1; // ems
        let y = text.attr("y");
        let tspan = text.text(null).append("tspan").attr("x", text.attr("x")).attr("y", y);

        while (word = words.pop()) {
            line.push(word);
            tspan.text(line.join(" "));
            if (tspan.node().getComputedTextLength() > width) {
                line.pop();
                tspan.text(line.join(" "));
                line = [word];
                tspan = text.append("tspan")
                    .attr("x", text.attr("x"))
                    .attr("y", y)
                    .attr("dy", lineHeight + "em")
                    .text(word);
            }
        }
    });
}

function getTextWidth(text, font) {
    const canvas = document.createElement('canvas');
    const context = canvas.getContext('2d');
    context.font = font;
    return context.measureText(text).width;
}

function handleSearch(e) {
    const value = e.target.value.toLowerCase();
    const searchResults = document.getElementById('searchResults');
    const clearButton = document.getElementById('clearSearch');
    
    if (!value) {
        searchResults.classList.add('hidden');
        clearButton.classList.add('hidden');
        return;
    }

    clearButton.classList.remove('hidden');

    const matches = Object.values(charities)
        .filter(d => d.name.toLowerCase().includes(value) || d.filer_ein.includes(value))
        .slice(0, 5);

    if (matches.length > 0) {
        searchResults.innerHTML = matches
            .map((d, index) => `
                <div 
                    class="p-2 cursor-pointer ${index === 0 ? 'bg-blue/10' : ''} hover:bg-gray-100" 
                    data-ein="${d.filer_ein}"
                    data-index="${index}"
                    onmouseenter="handleSearchResultHover(${index})"
                >
                    ${d.name}
                </div>
            `)
            .join('');
        searchResults.classList.remove('hidden');
        selectedSearchIndex = 0;
        const firstResult = searchResults.querySelector('[data-index="0"]');
        if (firstResult) {
            firstResult.classList.add('bg-blue/10');
            firstResult.classList.remove('hover:bg-gray-100');
        }
    } else {
        searchResults.classList.add('hidden');
        selectedSearchIndex = -1;
    }
}

function handleSearchBlur() {
    const searchInput = document.getElementById('searchInput');
    if (!searchInput.value) {
        // Reset highlighting if needed
    }
}

function handleSearchClick(e) {
    const ein = e.target.dataset.ein;
    if (!ein) return;

    const searchInput = document.getElementById('searchInput');
    searchInput.value = charities[ein].name;
    document.getElementById('searchResults').classList.add('hidden');

    expandNode(ein);
    generateGraph();
}

function handleSearchKeydown(e) {
    const searchResults = document.getElementById('searchResults');
    if (searchResults.classList.contains('hidden')) return;
    
    const results = searchResults.querySelectorAll('[data-index]');
    const maxIndex = results.length - 1;

    if (maxIndex < 0) {
        selectedSearchIndex = -1;
        return;
    }

    switch (e.key) {
        case 'ArrowDown':
            e.preventDefault();
            selectedSearchIndex = Math.min(selectedSearchIndex + 1, maxIndex);
            updateSearchSelection(results);
            break;
        case 'ArrowUp':
            e.preventDefault();
            selectedSearchIndex = Math.max(selectedSearchIndex - 1, 0);
            updateSearchSelection(results);
            break;
        case 'Enter':
            e.preventDefault();
            if (selectedSearchIndex >= 0) {
                const selectedResult = results[selectedSearchIndex];
                if (selectedResult) {
                    handleSearchClick({ target: selectedResult });
                }
            }
            break;
        case 'Escape':
            e.preventDefault();
            searchResults.classList.add('hidden');
            e.target.blur();
            break;
    }
}

function updateSearchSelection(results) {
    results.forEach((result, index) => {
        if (index === selectedSearchIndex) {
            result.classList.add('bg-blue/10');
            result.classList.remove('hover:bg-gray-100');
            result.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
        } else {
            result.classList.remove('bg-blue/10');
            result.classList.add('hover:bg-gray-100');
        }
    });
}

function handleSearchResultHover(index) {
    selectedSearchIndex = index;
    updateSearchSelection(document.querySelectorAll('[data-index]'));
}

const extraStyle = `.node.others { fill: #ccc; cursor: pointer; }
                    .node.loop { fill: #ff9999; }
                    .link.loop { stroke: #ff4444; }`;
d3.select("head").append("style").text(extraStyle);