// main.js
import {graphScaleUp, graphScaleDown, graphScaleReset, GOV_EIN, Charity, Grant, loadData, scaleValue, formatNumber } from './models.js';

// Filter state
let activeEINs = [];
let activeKeywords = [];

// BFS / data-ready
let dataReady = false;
let panZoomInstance = null;

// Custom graph state
let customGraphEdges = null;
let customTitle = null;

// Additional global variables
let simulation = null;
let nodeElements = null;
let selectedSearchIndex = 0;

// Sankey state
let currentData = { nodes: [], links: [] };
let nodeMap = Charity.charityLookup;
let linkMap = Grant.grantLookup;
let expanded = new Map();
let compacted = new Map();
const TOP_N_INITIAL = 3;
const TOP_N_OUTFLOWS = 5;

// Global references
let svg = null;
let zoom = null;
let topNodes = [];
let expandedOutflows = new Map();

const NODE_WIDTH = 50;
const NODE_PADDING = 10;
const MIN_LINK_HEIGHT = 5;

const colorScale = d3.scaleOrdinal(d3.schemeCategory10);

function updateStatus(message,color='black'){
        $('#status').text(message).css('color', color);

}

$(document).ready(function() {
    if (dataReady)
        parseQueryParams();

    updateStatus("Loading Data...");
    loadData().then(() => {
        dataReady = true;
        generateGraph();
    }).catch(err => {
        console.error(err);
        updateStatus('Failed to load data.', 'red');
    });

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

    $('#downloadBtn').on('click', downloadSVG);

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
    //newSearchResults.addEventListener('click', handleSearchClick);
    newSearchInput.addEventListener('keydown', handleSearchKeydown);

    newClearButton.addEventListener('click', () => {
        newSearchInput.value = '';
        newSearchInput.focus();
        handleSearch({ target: newSearchInput });
    });

    $(window).on('resize', function() {
        if (dataReady) {
            generateGraph();
        }
    });
});

function addEINFromInput() {
    let val = $('#einInput').val().trim();
    val = val.replace(/[-\s]/g, '');
    
    if (!/^\d{9}$/.test(val) && !(val=='001')) {
        alert("EIN must be 9 digits after removing dashes/spaces or 001.");
        return;
    }
    if (!Charity.getCharity(val)) {
        console.warn("EIN not found in charities.csv (still adding).");
    }
    if (!activeEINs.includes(val)) {
        activeEINs.push(val);
    }
    $('#einInput').val('');
    renderActiveEINs();
    Charity.placeNode(val);
    updateQueryParams();
    generateGraph();
}

function removeEIN(ein) {
        removeEINs.push(ein);

}

function renderActiveEINs() {
    const $c = $('#activeEINs');
    $c.empty();
    $('#clearEINsBtn').toggle(activeEINs.length > 0);

    activeEINs.forEach(ein => {
        const $tag = $('<div class="filter-tag flex items-center gap-0.5 rounded border border-blue bg-blue/10 text-blue rounded-md px-2 py-1 text-xs"></div>');
        const $text = $('<span></span>').text(ein.slice(0,2) + '-' + ein.slice(2));
        const $rm = $('<span class="remove-filter opacity-50 hover:opacity-100 size-5 -my-0.5 -mr-1 cursor-pointer"><svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24"><path fill="#000" fill-rule="evenodd" d="M2 12C2 6.477 6.477 2 12 2s10 4.477 10 10-4.477 10-10 10S2 17.523 2 12Zm7.53-3.53a.75.75 0 0 0-1.06 1.06L10.94 12l-2.47 2.47a.75.75 0 1 0 1.06 1.06L12 13.06l2.47 2.47a.75.75 0 1 0 1.06-1.06L13.06 12l2.47-2.47a.75.75 0 0 0-1.06-1.06L12 10.94 9.53 8.47Z" clip-rule="evenodd"/></svg></span>').attr('data-ein', ein);
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
function renderHideEINs() {
    const $c = $('#hideEINs');
    $c.empty();
    $('#clearHideEINsBtn').toggle(hideEINs.length > 0);

    Charity.getHideList().forEach(ein => {
        const $tag = $('<div class="filter-tag flex items-center gap-0.5 rounded border border-blue bg-blue/10 text-blue rounded-md px-2 py-1 text-xs"></div>');
        const $text = $('<span></span>').text(ein.slice(0,2) + '-' + ein.slice(2));
        const $rm = $('<span class="remove-filter opacity-50 hover:opacity-100 size-5 -my-0.5 -mr-1 cursor-pointer"><svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24"><path fill="#000" fill-rule="evenodd" d="M2 12C2 6.477 6.477 2 12 2s10 4.477 10 10-4.477 10-10 10S2 17.523 2 12Zm7.53-3.53a.75.75 0 0 0-1.06 1.06L10.94 12l-2.47 2.47a.75.75 0 1 0 1.06 1.06L12 13.06l2.47 2.47a.75.75 0 1 0 1.06-1.06L13.06 12l2.47-2.47a.75.75 0 0 0-1.06-1.06L12 10.94 9.53 8.47Z" clip-rule="evenodd"/></svg></span>')
                .attr('data-nein', ein);
        $rm.on('click', function() {
            const rem = $(this).attr('data-nein');
            Charity.removeFromHideList(rem);
            renderHideEINs();
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
        const $rm = $('<span class="remove-filter opacity-50 hover:opacity-100 size-5 -my-0.5 -mr-1 cursor-pointer"><svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24"><path fill="#000" fill-rule="evenodd" d="M2 12C2 6.477 6.477 2 12 2s10 4.477 10 10-4.477 10-10 10S2 17.523 2 12Zm7.53-3.53a.75.75 0 0 0-1.06 1.06L10.94 12l-2.47 2.47a.75.75 0 1 0 1.06 1.06L12 13.06l2.47 2.47a.75.75 0 1 0 1.06-1.06L13.06 12l2.47-2.47a.75.75 0 0 0-1.06-1.06L12 10.94 9.53 8.47Z" clip-rule="evenodd"/></svg></span>').attr('data-kw', kw);
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
    activeEINs=params.getAll('ein');
    Charity.setHideList(params.getAll('nein'));
    activeKeywords=params.getAll('keywords');
    

        $('.bfs-only').show();
        $('#instructions').text(
            'Use the search and keywords to filter charities, then BFS expansion is performed automatically.'
        );

        Charity.matchURL(params);
}

function updateQueryParams() {
    if (!customGraphEdges) {
        return;
    }
    const params = Charity.computeURLParams(activeKeywords);
    const newUrl = window.location.pathname + '?' + params.toString();
    window.history.replaceState({}, '', newUrl);
    if (dataReady)
        parseQueryParams();
}

const sortOffset = +0.001;
function compareCharities( a, b)
{
        let sortgrantsInTotalA = a.grantsInTotal;
        let sortgrantsInTotalB = b.grantsInTotal;
        if (a.isOther) sortgrantsInTotalA = a.parent.grantsInTotal+sortOffset;
        if (b.isOther) sortgrantsInTotalB = b.parent.grantsInTotal+sortOffset;
        return  (sortgrantsInTotalA-sortgrantsInTotalB) ||
                (b.grantsTotal-a.grantsTotal) || (b.grantsInTotal - a.grantsInTotal);
        return (b.govt_amt - a.govt_amt) || 
                (b.grantsInTotal+b.grantsTotal - a.grantsInTotal-b.grantsTotal) || 
                        (a.name.localeCompare(b.name));
}

function compareLinks(a,b)
{
        let sortvalueA = a.value;
        let sortvalueB = b.value;
        if (a.isOther) {
                if (a.target.isOther)
                        sortvalueA = a.target.parent.otherDown.otherGrant.value+sortOffset;
                if (a.source.isOther)
                        sortvalueA = a.source.parent.otherUp.otherGrant.value+sortOffset;
        }
      if (b.isOther) {
                if (b.target.isOther)
                        sortvalueA = b.target.parent.otherDown.otherGrant.value-+sortOffset;
                if (b.source.isOther)
                        sortvalueA = b.source.parent.otherUp.otherGrant.value+sortOffset;
        }
       //return (a.value-b.value);
        return (sortvalueB-sortvalueA);

}

function generateGraph() {
    console.log('1. Starting graph generation');
    if (!dataReady) {
        alert("Data not loaded yet. Please wait.");
        return;
    }

    $('#loading').show();
    console.log('2. Loading icon shown');
    $('#graph-container svg').remove();

    const container = document.getElementById('graph-container');
    const width = container.offsetWidth;
    const height = container.offsetHeight || window.innerHeight * 0.7;
    console.log('4. Container dimensions calculated');

    svg = d3.select('#graph-container')
        .append('svg')
        .attr('id', 'graph')
        .attr('width', '100%')
        .attr('height', '100%')
        .style('display', 'block')
        .style('background', '#fff');
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
        .nodeId(d => d.id )
        .nodeWidth(NODE_WIDTH)
        .nodePadding(NODE_PADDING)
        .linkSort(compareLinks)
        .nodeId(d => d.id)
        .nodeAlign(d3.sankeyCenter)
        .nodeSort(compareCharities)
        .size([width - 100, height - 100]);

    parseQueryParams();
    if (!customGraphEdges && activeEINs.length === 0) {
        const usGov = Charity.getCharity(GOV_EIN);
        updateStatus('placing US Government');
        Charity.placeNode(GOV_EIN);
        updateStatus(`USG placed adding top ${TOP_N_INITIAL} roots`);
        Charity.getRootCharities().slice(0,TOP_N_INITIAL).forEach(c=> {
                Charity.placeNode(c.id);
        });
        customGraphEdges = Charity.visibleCharities();
        
    }

    renderFocusedSankey(g, sankey, svg, width, height, activeEINs.length ? activeEINs : [GOV_EIN]);

    document.getElementById('zoomIn').onclick = () => svg.transition().duration(300).call(zoom.scaleBy, 1.3);
    document.getElementById('zoomOut').onclick = () => svg.transition().duration(300).call(zoom.scaleBy, 0.7);
    document.getElementById('zoomFit').onclick = () => {
        const bounds = g.node().getBBox();
        if (!isFinite(bounds.width) || bounds.width <= 0 || !isFinite(bounds.height) || bounds.height <= 0) return;
        const dx = bounds.x;
        const dy = bounds.y;
        const scale = 0.8 / Math.max(bounds.width / width, bounds.height / height);
        svg.transition()
            .duration(750)
            .call(zoom.transform, d3.zoomIdentity
                .translate(width/2, height/2)
                .scale(scale)
                .translate(-dx - bounds.width/2, -dy - bounds.height/2));
    };
    document.getElementById('scaleUp').onclick = () => doScaleUp();
    document.getElementById('scaleDown').onclick = () => doScaleDown();
    document.getElementById('scaleReset').onclick = () => doScaleReset();

    setTimeout(() => {
        const bounds = g.node().getBBox();
        if (!isFinite(bounds.width) || bounds.width <= 0 || !isFinite(bounds.height) || bounds.height <= 0) {
            console.error("Invalid bounds for zoom:", bounds);
            return;
        }
        const dx = bounds.x;
        const dy = bounds.y;
        const scale = 0.8 / Math.max(bounds.width / width, bounds.height / height);
        svg.transition()
            .duration(750)
            .call(zoom.transform, d3.zoomIdentity
                .translate(width/2, height/2)
                .scale(scale)
                .translate(-dx - bounds.width/2, -dy - bounds.height/2));
    }, 1000);

    $('#loading').hide();
}

function doScaleUp()
{
        graphScaleUp();
        generateGraph();
}
function doScaleDown()
{
        graphScaleDown();
        generateGraph();
}
function doScaleReset()
{
        graphScaleReset();
        generateGraph()
}



function generateUniqueId(prefix = "gradient") {
    return `${prefix}-${Math.random().toString(36).substr(2, 9)}`;
}

function calculateScale(graph, width, height) {
    const nodes = graph.nodes;
    if (!nodes.length) {
        return 1;
    }

    let minX = Infinity, maxX = -Infinity;
    let minY = Infinity, maxY = -Infinity;

    nodes.forEach(node => {
        minX = Math.min(minX, node.x0);
        maxX = Math.max(maxX, node.x1);
        minY = Math.min(minY, node.y0);
        maxY = Math.max(maxY, node.y1);
    });

    const layoutWidth = Math.max(maxX - minX, 1);
    const layoutHeight = Math.max(maxY - minY, 1);

    const scaleX = width / layoutWidth;
    const scaleY = height / layoutHeight;
    return Math.min(scaleX, scaleY);
}

function generateTrapezoidPath(d) {
    const midY = (d.y0 + d.y1) / 2;
    const y0In = midY - d.inflowHeight / 2;
    const y1In = midY + d.inflowHeight / 2;
    const y0Out = midY - d.outflowHeight / 2;
    const y1Out = midY + d.outflowHeight / 2;
    return `M${d.x0},${y0In} L${d.x0},${y1In} L${d.x1},${y1Out} L${d.x1},${y0Out} Z`;
}

function generateOctagonPath(d) {
    const radius = d.inflowHeight / 2 || 10;
    const cx = d.x0;
    const cy = (d.y0 + d.y1) / 2;
    const r = radius;
    const s = radius * Math.SQRT2 / 2;

    return `M${cx + r},${cy} L${cx + s},${cy + s} L${cx},${cy + r} L${cx - s},${cy + s} L${cx - r},${cy} L${cx - s},${cy - s} L${cx},${cy - r} L${cx + s},${cy - s} Z`;
}

function generatePlusPath(d) {
    const radius = NODE_WIDTH/2; //d.inflowHeight / 2 || 10; // Circle radius from inflowHeight, min 10
    const armWidth = radius * 0.8; // Skinny arms (20% of radius)
    let cx = d.x0; // Absolute center at d.x0
    if (!d.isRight)
        cx= d.x1; //other side for upstream
    const cy = (d.y0 + d.y1) / 2; // Absolute vertical center

    // Circle path (filled)
    const circlePath = `
        M${cx},${cy - radius}
        A${radius},${radius} 0 1 1 ${cx},${cy + radius}
        A${radius},${radius} 0 1 1 ${cx},${cy - radius}
        Z`;

    // Skinny plus sign path (lines)
    const plusPath = `
        M${cx - armWidth},${cy}
        H${cx + armWidth}
        M${cx},${cy - armWidth}
        V${cy + armWidth}
    `;

    const fullPath = `${circlePath} ${plusPath}`;
    return fullPath;
}

function computeLinkY(node, linkIndex, links, heightKey, isSourceSide) {
    // Sort links: "Other" links go to the bottom, others by value (descending)
    const sortedLinks = [...links].sort(compareLinks);
    
    // Compute the cumulative height up to this link
    const cumulativeHeight = d3.sum(sortedLinks.slice(0, linkIndex), l => l.width);
    
    // Compute the center Y of the node
    const centerY = (node.y0 + node.y1) / 2;
    
    // Get the inflow/outflow height
    const height = node[heightKey] || 0;
    
    // Get the link's stroke-width
    const linkHeight = sortedLinks[linkIndex].width || 0;
    
    // For the source side (right edge), start from the top of the right edge (centerY - height/2)
    // For the target side (left edge), start from the bottom of the left edge (y1)
    const startY = isSourceSide? (centerY - height / 2) : (centerY + height / 2);
    
    // Compute the top of the segment:
    // - Source side: Stack downward from startY
    // - Target side: Stack upward from startY (bottom)
    const segmentTop = isSourceSide ? (startY + cumulativeHeight) : (startY - height + cumulativeHeight);
    
    // For the source side (outflows), return the top of the segment (stroke-width extends downward)
    // For the target side (inflows), center the path for visual alignment (stroke-width extends symmetrically)
    const y = (segmentTop + (linkHeight / 2));
    
    return y;
}

function sankeyLinkHorizontalTrapezoid(curvature = 0.5) {
    return function(link) {
        // Source position (right edge of source node)
        const source = link.source;
        const originalSourceLinks = [...source.sourceLinks];
        const outflowIndex = source.sourceLinks.sort(compareLinks).indexOf(link);
        const sourceY = computeLinkY(source, outflowIndex, source.sourceLinks, 'outflowHeight', true);
        const sourceX = source.x1;

        // Target position (left edge of target node)
        const target = link.target;
        const originalTargetLinks = [...target.targetLinks];
        const inflowIndex = target.targetLinks.sort(compareLinks).indexOf(link);
        const targetY = computeLinkY(target, inflowIndex, target.targetLinks, 'inflowHeight', false);
        const targetX = target.x0;

        // Restore original order to avoid side effects
        source.sourceLinks = originalSourceLinks;
        target.targetLinks = originalTargetLinks;

        // Compute control points for cubic Bézier curve
        const dx = targetX - sourceX;
        const cp1X = sourceX + dx * curvature;
        const cp1Y = sourceY;
        const cp2X = targetX - dx * curvature;
        const cp2Y = targetY;

        // Generate the path
        return `M${sourceX},${sourceY} C${cp1X},${cp1Y} ${cp2X},${cp2Y} ${targetX},${targetY}`;
    };
}

// force expantion nodess to the bottom of the parent
function calculateOtherPosition(node,scale, height)
{
        calculateRegularPosition(node.parent, scale, height) // make sure parent is calculated
        const width = node.x1-node.x0;
        const height2 = node.y1- node.y0;
    // calculate trap corners
    const midY = (node.parent.y0 + node.parent.y1) / 2;
    const y0In = midY - node.parent.inflowHeight / 2;
    const y1In = midY + node.parent.inflowHeight / 2;
    const y0Out = midY - node.parent.outflowHeight / 2;
    const y1Out = midY + node.parent.outflowHeight / 2;
       if (node.isRight)
        {
                node.x0= node.parent.x1+NODE_WIDTH*2;
                node.x1 = node.parent.x1+NODE_WIDTH;   
                node.y0= y0Out - height2;
                node.y1= y1Out - height2;      
          }
        else
        {
                node.x0= node.parent.x0-NODE_WIDTH;
                node.x1 = node.parent.x0-2*NODE_WIDTH;        
                node.y0= y0In - height2;
                node.y1= y1In - height2;      
        }
        calculateRegularPosition(node, scale, height) // make sure parent is calculated

}
function calculateRegularPosition(node, scale, height)
{
       let scaleFactor = 100; // placeholder for 0,0
        const sankeyHeight = node.y1 - node.y0;
        if (node.grantsInLogTotal > node.grantsLogTotal)
                scaleFactor = sankeyHeight/node.grantsInLogTotal; // we know its greater so must be not zero
        else
                scaleFactor = sankeyHeight/node.grantsLogTotal;
        node.outflowHeight =  Math.min(sankeyHeight, node.grantsLogTotal * scaleFactor);
        node.inflowHeight = Math.min(sankeyHeight, node.grantsInLogTotal * scaleFactor);
        if (node.grantsLogTotal === 0) {
            node.inflowHeight = sankeyHeight;
            node.outflowHeight = 5;
        }
        if (node.grantsLogInTotal === 0){
            node.inflowHeight = 5;
            node.outflowHeight = sankeyHeight;
        }

        if (!isFinite(node.outflowHeight) || !isFinite(node.inflowHeight)) {
            console.error(`Invalid heights for ${node.filer_ein}: outflow=${node.outflowHeight}, inflow=${node.inflowHeight}`);
            node.outflowHeight = 50;
            node.inflowHeight = 50;
        }

        node.x0Original = node.x0;
        node.x1Original = node.x1;
        node.y0Original = node.y0;
        node.y1Original = node.y1;
}

function calculateNodePositions(nodes, scale, height) {
    nodes.forEach(d => {
        if (d.isOther)
        {
                calculateOtherPosition(d, scale, height);

        }
        else
        {
                calculateRegularPosition(d, scale, height);
        }
 
    });
}

function normalizeStrokeWidths(sankey) {
    const nodes = sankey.nodes;
    nodes.forEach(node => {
        // Normalize outflow stroke-widths
        const totalOutflowWidth = d3.sum(node.sourceLinks, l => l.width);
        const outflowHeight = node.outflowHeight || 0;
        if (totalOutflowWidth > 0 && outflowHeight > 0) {
            const scaleFactor = outflowHeight / totalOutflowWidth;
            node.sourceLinks.forEach(link => {
                link.normalizedWidth = link.width * scaleFactor; // Store in a custom property
            });
        }
        // Normalize inflow stroke-widths
        const totalInflowWidth = d3.sum(node.targetLinks, l => l.width);
        const inflowHeight = node.inflowHeight || 0;
        if (totalInflowWidth > 0 && inflowHeight > 0) {
            const scaleFactor = inflowHeight / totalInflowWidth;
            node.targetLinks.forEach(link => {
                link.normalizedWidth = link.width * scaleFactor; // Store in a custom property
            });
        }
    });
}

function renderFocusedSankey(g, sankey, svgRef, width, height, nodeIds) {

     $('#downloadBtn').hide();
   if (nodeIds) nodeIds.forEach(nid => Charity.placeNode(nid));
 
    // Brute-force build currentData from visible nodes and grants
    currentData = Charity.buildSankeyData();

    const graph = sankey(currentData);
    

    const scale = calculateScale(graph, width, height);
    calculateNodePositions(graph.nodes, scale, height);
    normalizeStrokeWidths(graph);

    graph.nodes.forEach(n => {
        if (!isFinite(n.x0) || !isFinite(n.y0) || !isFinite(n.x1) || !isFinite(n.y1)) {
            console.error(`NaN position for node ${n.filer_ein}:`, { id: n.filer_ein, name: n.name, x0: n.x0, y0: n.y0 });
        }
    });

    g.selectAll("*").remove();

    graph.links.forEach((link, i) => {
        link.gradientId = generateUniqueId("gradient");
    });

    const defs = svgRef.append("defs");
    const gradients = defs.selectAll("linearGradient.dynamic")
        .data(graph.links)
        .enter()
        .append("linearGradient")
        .attr("id", d => d.gradientId)
        .attr("gradientUnits", "userSpaceOnUse")
        .attr("x1", d => d.source.x1 * scale)
        .attr("x2", d => d.target.x0 * scale);

    gradients.append("stop")
        .attr("offset", "0%")
        .attr("stop-color", d => colorScale(d.source.id));

    gradients.append("stop")
        .attr("offset", "100%")
        .attr("stop-color", d => colorScale(d.target.id));

    const masterGroup = g.append("g")
        .attr("class", "graph-group")
        .attr("transform", `scale(${scale})`);
        
   

    const link = masterGroup.append("g")
        .attr("fill", "none")
        .attr("stroke-opacity", 1)
        .style("mix-blend-mode", "multiply")
        .selectAll(".link")
        .data(graph.links)
        .join("path")
        .attr("d", sankeyLinkHorizontalTrapezoid())
        .style("stroke", d => d.target.isOther ? "#ccc" : `url(#${d.gradientId})`)
        .style("stroke-opacity", "0.3")
        .attr("stroke-width", d => d.width)
        .on('click', function(event, d) {
            event.stopPropagation();
            handlePathClick(event,d);
            renderFocusedSankey(g, sankey, svgRef, width, height, null);
        });

    link.each(function(d) {
        if (d3.select(this).style("stroke") === "none") {
            d3.select(this).style("stroke", colorScale(d.source.id));
        }
    });

    link.append("title")
        .text(d => d.target.isOther 
            ? `${d.source.name} → ...\n$${formatNumber(d.amt)}` 
            : `${d.source.name} → ${d.target.name}\n$${formatNumber(d.amt)}`);

    const nodeGroup = masterGroup.append('g').attr('class', 'nodes');
    nodeElements = nodeGroup.selectAll('g')
        .data(graph.nodes)
        .join('g')
        .attr('class', d => {
            if (d.isOther) return 'node others';
            if (d.willShrink) return 'node shrink';
            if (!d.grants || !d.grants.length) return 'node no-grants';
            return 'node expand';
        })
        .attr('data-id', d => d.id);

    nodeElements.each(function(d) {
        const sel = d3.select(this);
        if (d.isOther === true) {
            sel.append("path")
                .attr("d", generatePlusPath(d))
                .attr("fill", "#ccc")
                .attr("fill-rule", "evenodd") // Circle filled, plus as hole
                .attr("stroke", "#000");
        } else if (!d.grants || !d.grants.length) { // terminal nodes
            sel.append("path")
                .attr("d", generateOctagonPath(d))
                .attr("fill", d => colorScale(d.id))
                .attr("stroke", "#000");
        } else {  // typical, grants in, grants out
            sel.append("path")
                .attr("d", generateTrapezoidPath(d))
                .attr("fill", d => colorScale(d.id))
                .attr("stroke", "#000");
        }
    });

    function nodeClick(event, d) {
            const selectedNodeId = d.id;
        event.stopPropagation();
        if (event.controlKey) {
            generateGraph();
        } else if (d.isOther === true) {
                const result=d.handleClick(event);
                updateQueryParams(); // get new counts for the visible EINs
                renderFocusedSankey(g, sankey, svgRef, width, height, null);
        } else {
            console.log(`Expanding node: ${d.filer_ein}`);
            if (!d.handleClick(event)) //handle click returns false for nodes we hide
            {
                Charity.addToHideList(d.id);
                renderHideEINs()            
                updateQueryParams();
            }
            else
            {
                updateQueryParams();
                renderHideEINs();
            }
            renderFocusedSankey(g, sankey, svgRef, width, height, null);
        }
    }
    
    function handlePathClick(e,d) {
    
        if (e.altKey)
        {
                return d.tunnelGrant();
        
        }
        if (d.filer.isOther) 
        {
                d.filer.handleGrantClick(e, d);
        } else if (d.grantee.isOther) {
                d.grantee.handleGrantClick(e, d);
        }
        else {
                d.filer.handleClick(e, d);
                d.grantee.handleClick(e, d);
        }
    
    }

    nodeElements.on('click', nodeClick);

    nodeElements.append("title")
        .text(d => {
            const inflow = d.origIn;
            const outflow = d.origOut;
            const top = d.toolTipText();
            const loopback = d.loopbackgrants.reduce((sum, g) => sum + g.amt, 0);
            if (loopback) return `${top}\nLoop: $${formatNumber(loopback)}`;
            return top;
        });

    masterGroup.append("g")
        .selectAll()
        .data(graph.nodes)
        .join("text")
        .attr("x", d => d.x0Original < sankey.nodeWidth() / 2 ? d.x1Original + 6 : d.x0Original - 6)
        .attr("y", d => ((d.y1Original + d.y0Original) / 2) * scale)
        .attr("dy", "0.35em")
        .attr("text-anchor", d => d.x0Original < sankey.nodeWidth() / 2 ? "start" : "end")
        .text(d => d.name)
        .on('click', function(event, d) {
            const selectedNodeId=d.id;
            event.stopPropagation();
            if (event.altKey) {
                generateGraph();
            } else if (d.isOther === true) {
                const sourceId = d.parent_ein;
                console.log(`Text expanding "OTHER" for ${sourceId}`);
                d.handleClick(event);
                renderFocusedSankey(g, sankey, svgRef, width, height, [selectedNodeId]);
            } else {
                console.log(`Text expanding node: ${d.filer_ein}`);
                d.handleClick(event);
                renderFocusedSankey(g, sankey, svgRef, width, height, [selectedNodeId]);
            }
        });
    Charity.cleanAfterRender(currentData); // restore grants to their original values
    $('#downloadBtn').show();

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

    const matches = Object.values(Charity.charityLookup)
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

function zoomToFitNodes(node1, node2, svgWidth, svgHeight) {
    const node1El = nodeElements.filter(n => n.id === node1.id).node();
    const node2El = nodeElements.filter(n => n.id === node2.id).node();
    
    if (!node1El || !node2El) return;
    
    const box1 = node1El.getBBox();
    const box2 = node2El.getBBox();
    
    const x1 = Math.min(node1.x0 - box1.width/2, node2.x0 - box2.width/2);
    const y1 = Math.min(node1.y0 - box1.height/2, node2.y0 - box2.height/2);
    const x2 = Math.max(node1.x1 + box1.width/2, node2.x1 + box2.width/2);
    const y2 = Math.max(node1.y1 + box1.height/2, node2.y1 + box2.height/2);
    
    const padding = 50;
    const bounds = {
        x: x1 - padding,
        y: y1 - padding,
        width: (x2 - x1) + (padding * 2),
        height: (y2 - y1) + (padding * 2)
    };
    
    const scale = 0.9 / Math.max(bounds.width / svgWidth, bounds.height / svgHeight);
    
    svg.transition()
        .duration(750)
        .call(zoom.transform,
            d3.zoomIdentity
                .translate(svgWidth/2, svgHeight/2)
                .scale(scale)
                .translate(-(bounds.x + bounds.width/2), -(bounds.y + bounds.height/2))
        );
}

const extraStyle = `.node.others { fill: #ccc; cursor: plus; }
                    .node { fill: #999; }
                    .node.expand {cursor: plus}
                    .node.shrink {cursor: minus}
                    .link { stroke: #ccc; stroke-opacity: 0.5; }
                    #graph { background: #fff !important; }
                    text { fill: #000; }
                    svg { background: #fff !important; }`;
d3.select("head").append("style").text(extraStyle);