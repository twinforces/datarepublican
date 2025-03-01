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

$(document).ready(function() {
    parseQueryParams();

    loadData().then(() => {
        dataReady = true;
        $('#status').text('Data loaded.').css('color', 'black');
        generateGraph();
    }).catch(err => {
        console.error(err);
        $('#status').text('Failed to load data.').css('color', 'red');
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
    updateQueryParams();
    Charity.placeNode(val);
    generateGraph();
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
    if (customParam) {
        const trimmed = customParam.replace(/;+$/, '');
        const segments = trimmed.split('&');
        let edgeList = [];
        let search = "";
        
        segments.forEach(segment => {
            if (segment.startsWith("search=")) {
                search = segment.replace("search=", "");
            }
            if (segment.startsWith("ein=")) {
                edgeList.push(segment.replace("ein=", ""));
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

        //Object.values(Charity.charityLookup).forEach(charity => charity.isVisible = false);
        //Object.values(Grant.grantLookup).forEach(grant => grant.isVisible = false);
        customGraphEdges.forEach(edge => {
            let filer = Charity.getCharity(edge.filer);
            let grantee = Charity.getCharity(edge.grantee);
            if (!filer) {
                filer = new Charity({ ein: edge.filer, name: `Unknown (${edge.filer})` });
            }
            if (!grantee) {
                grantee = new Charity({ ein: edge.grantee, name: `Unknown (${edge.grantee})` });
            }
            filer.isVisible = true;
            grantee.isVisible = true;
            const grant = Grant.getGrant(`${edge.filer}~${edge.grantee}`);
            if (!grant) {
                new Grant({
                    filer_ein: edge.filer,
                    grantee_ein: edge.grantee,
                    amt: edge.amt,
                    isVisible: true
                });
            } else {
                grant.isVisible = true;
            }
        });
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

        activeEINs.forEach(ein => {
            const charity = Charity.getCharity(ein);
            if (charity) {
                Charity.placeNode(ein);
            }
        });
    }
}

function updateQueryParams() {
    if (customGraphEdges) {
        return;
    }
    const params = new URLSearchParams();
    params.set('ein', activeEINs);
    params.set('search', activeKeywords);
    const newUrl = window.location.pathname + '?' + params.toString();
    window.history.replaceState({}, '', newUrl);
}


function compareCharities( a, b)
{
        return (b.govt_amt - a.govt_amt) || 
                (b.grantsInTotal+b.grantsTotal - a.grantsInTotal-b.grantsTotal) || 
                        (a.name.localeCompare(b.name));
}

function compareLinks(a,b)
{
        //sort other links to bottom
        if (a.isOther) return 1;
        if (b.isOther) return -1;
        //return (a.index-b.index);
        return (b.value-a.value);

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
        .linkSort(linkCompare)
        .nodeId(d => d.id)
        .nodeAlign(d3.sankeyCenter)
        .nodeSort(compareCharities)
        .size([width - 100, height - 100]);

    if (!customGraphEdges && activeEINs.length === 0) {
        const usGov = Charity.getCharity(GOV_EIN);
        Charity.placeNode(GOV_EIN);
        Charity.getRootCharities().slice(0,TOP_N_INITIAL).forEach(c=> {
                Charity.placeNode(c.id);
        });
        customGraphEdges = Charity.visibleCharities();
        
    }

    renderFocusedSankey(g, sankey, svg, width, height, activeEINs[0] || GOV_EIN);

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
    const radius = d.inflowHeight / 2 || 10; // Circle radius from inflowHeight, min 10
    const armWidth = radius * 0.8; // Skinny arms (20% of radius)
    const cx = d.x0; // Absolute center at d.x0
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
function linkCompare(a,b) {

       const aIsOther = a.isOther || false;
        const bIsOther = b.isOther || false;
        if (aIsOther && !bIsOther) return 1; // "Other" goes to the bottom
        if (!aIsOther && bIsOther) return -1;
        return b.value - a.value; // Sort by value (descending)
}
function sortLinks(links, getNode) {
    return links.sort(linkCompare);
}

function sankeyLinkHorizontalTrapezoid(curvature = 0.5) {
    return function(link) {
        // Source position (right edge of source node)
        const source = link.source;
        // Sort sourceLinks: "Other" links go to the bottom, others by value (descending)
        const originalSourceLinks = [...source.sourceLinks];
        sortLinks(source.sourceLinks, link => link.source);
        const outflowIndex = source.sourceLinks.indexOf(link);
        const cumulativeOutflowHeight = d3.sum(source.sourceLinks.slice(0, outflowIndex), l => l.width);
        const sourceCenterY = (source.y0 + source.y1) / 2;
        const outflowHeight = source.outflowHeight || 0;
        const sourceLinkHeight = link.width || 0;
        // Start from the top of the right edge (centerY - outflowHeight/2) and stack downward
        const sourceTopY = sourceCenterY - (outflowHeight / 2);
        const sourceY = sourceTopY + cumulativeOutflowHeight + (sourceLinkHeight / 2);
        const sourceX = source.x1;

        // Target position (left edge of target node)
        const target = link.target;
        // Sort targetLinks: "Other" links go to the bottom, others by value (descending)
        const originalTargetLinks = [...target.targetLinks];
        sortLinks(target.targetLinks, link => link.target);
        const inflowIndex = target.targetLinks.indexOf(link);
        const cumulativeInflowHeight = d3.sum(target.targetLinks.slice(0, inflowIndex), l => l.width);
        const targetBottomY = target.y1; // Start from the bottom
        const targetLinkHeight = link.width || 0;
        const targetY = targetBottomY - cumulativeInflowHeight - (targetLinkHeight / 2);
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

function calculateNodePositions(nodes, scale, height) {
    nodes.forEach(d => {

        let scaleFactor = 100; // placeholder for 0,0
        const sankeyHeight = Math.max(MIN_LINK_HEIGHT, d.y1 - d.y0);
        if (d.logGrantsInTotal > d.logGrantsTotal)
                scaleFactor = sankeyHeight/d.logGrantsInTotal; // we know its greater so must be not zero
        else
                scaleFactor = sankeyHeight/d.logGrantsTotal;
        d.outflowHeight = Math.max(MIN_LINK_HEIGHT, Math.min(sankeyHeight, d.logGrantsTotal * scaleFactor));
        d.inflowHeight = Math.max(MIN_LINK_HEIGHT, Math.min(sankeyHeight, d.logGrantsInTotal * scaleFactor));
        if (d.grantsTotal === 0) {
            d.inflowHeight = sankeyHeight;
            d.outflowHeight = 0;
        }

        if (!isFinite(d.outflowHeight) || !isFinite(d.inflowHeight)) {
            console.error(`Invalid heights for ${d.filer_ein}: outflow=${d.outflowHeight}, inflow=${d.inflowHeight}`);
            d.outflowHeight = 50;
            d.inflowHeight = 50;
        }

        d.x0Original = d.x0;
        d.x1Original = d.x1;
        d.y0Original = d.y0;
        d.y1Original = d.y1;
    });
}

function renderFocusedSankey(g, sankey, svgRef, width, height, selectedNodeId) {
    const selectedNode = Charity.getCharity(selectedNodeId);
    if (!selectedNode) {
        console.error(`No node found for ${selectedNodeId}`);
        return;
    }

    // Brute-force build currentData from visible nodes and grants
    currentData = Charity.buildSankeyData();

    console.log(`Pre-sankey for ${selectedNodeId}: nodes=${currentData.nodes.length}, links=${currentData.links.length}`);

    const graph = sankey(currentData);
    const scale = calculateScale(graph, width, height);
    calculateNodePositions(graph.nodes, scale, height);

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
            renderFocusedSankey(g, sankey, svgRef, width, height, selectedNodeId);
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
        event.stopPropagation();
        if (event.controlKey) {
            generateGraph();
        } else if (d.isOther === true) {
            const sourceId = d.id;
            d.handleClick(event);
            renderFocusedSankey(g, sankey, svgRef, width, height, selectedNodeId);
        } else {
            console.log(`Expanding node: ${d.filer_ein}`);
            d.handleClick(event);
            renderFocusedSankey(g, sankey, svgRef, width, height, selectedNodeId);
        }
    }
    
    function handlePathClick(e,d) {
    
        if (e.altKey)
        {
                return d.tunnelGrant();
        
        }
        if (d.filer.isOther) 
        {
                d.filer.handleGrantClick(e, d)
        } else if (d.grantee.isOther) {
                d.grantee.handleGrantClick(e, d);
        }
        else {
                d.filer.handleGrantClick(e, d);
                d.grantee.handleGrantClick(e, d);
        }
    
    }

    nodeElements.on('click', nodeClick);

    nodeElements.append("title")
        .text(d => {
            const inflow = d.origIn;
            const outflow = d.origOut;
            const top = `${d.name || d.id}\nEIN:${d.id}\nInflow: $${formatNumber(inflow)}\nOutflow: $${formatNumber(outflow)}`;
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
            event.stopPropagation();
            if (event.altKey) {
                generateGraph();
            } else if (d.isOther === true) {
                const sourceId = d.parent_ein;
                console.log(`Text expanding "OTHER" for ${sourceId}`);
                d.handleClick(event);
                renderFocusedSankey(g, sankey, svgRef, width, height, selectedNodeId);
            } else {
                console.log(`Text expanding node: ${d.filer_ein}`);
                d.handleClick(event);
                renderFocusedSankey(g, sankey, svgRef, width, height, selectedNodeId);
            }
        });

    console.log(`Post-render for ${selectedNodeId}: nodes=${graph.nodes.length}, links=${graph.links.length}, renderedLinks=${link.size()}`);
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