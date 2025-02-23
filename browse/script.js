// Full data from CSV
let charities = {};             // EIN -> { name, receipt_amt, govt_amt, contrib_amt, grant_amt, grants: [] }
let grants = {};                // "filer~grantee" -> { rawAmt, relOutAmt, relInAmt, filer_ein, grantee_ein, filer, grantee }
let circularGrants = {};                // "filer~grantee" -> { rawAmt, relOutAmt, relInAmt, filer_ein, grantee_ein, filer, grantee }
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
let expanded = new Map();       // EIN -> total outflow
let compacted = new Map();       // EIN -> total outflow
const TOP_N_INITIAL = 5;        // Top nodes to start with
const TOP_N_OUTFLOWS = 5;       // Outflows per expansion

// Global references
let svg = null;
let zoom = null;
let topNodes = [];             // MADE GLOBAL: For coloring or logic access
// Add this near other global variables
let expandedOutflows = new Map(); // EIN -> array of expanded grant objects

const NODE_WIDTH = 24;
const NODE_PADDING = 10;

const POWER_LAW = .3; // scaling factor

// Color scale for unique node colors (based on EIN or name)
const colorScale = d3.scaleOrdinal(d3.schemeCategory10); // 10 distinct colors, repeatable

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
    val = val.replace(/[-\s]/g, '');
    
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
    expandNode(val, false, true); // Add to activeEINs
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
    if (num >= 1e12) return (num / 1e12).toFixed(1) + 'T'; // Trillions
    if (num >= 1e9) return (num / 1e9).toFixed(1) + 'B'; // Billions
    if (num >= 1e6) return (num / 1e6).toFixed(1) + 'M'; // Millions
    if (num >= 1e3) return (num / 1e3).toFixed(1) + 'K'; // Thousands
    return (num == null) ? "N/A" : num.toString(); // Less than 1,000
}

function calculateLogValue(rawAmt) {
    // PTW3, too stteep return Math.log2(rawAmt + 1);
    return Math.pow(rawAmt, POWER_LAW);
}

function logPropBuilderRawAmt(obj) {

        Object.defineProperty(obj, 'logRawAmt', {
            get: function() {
                return calculateLogValue(this.rawAmt);
            },
            enumerable: true,
            configurable: true
        });
}
function logPropBuilderGrantAmt(obj) {

        Object.defineProperty(obj, 'logGrantAmt', {
            get: function() {
                return calculateLogValue(this.grant_amt);
            },
            enumerable: true,
            configurable: true
        });
}
function logPropBuilderValue(obj) {

        Object.defineProperty(obj, 'value', {
            get: function() {
                return calculateLogValue(this.rawValue);
            },
            enumerable: true,
            configurable: true
        });
}

function sumPropBuilderGrantsIn(obj) {

       Object.defineProperty(obj, 'grantsInTotal', {
            get: function() {
                return this.grantsIn.reduce((total,g) => total+g.rawAmt,0);
            },
            enumerable: true,
            configurable: true
        });

}

function sumPropBuilderGrantsTotal(obj) {

       Object.defineProperty(obj, 'grantsTotal', {
            get: function() {
                return this.grants.reduce((total,g) => total+g.rawAmt,0);
            },
            enumerable: true,
            configurable: true
        });

}

function logPropBuilderGrantsIn(obj) {

       Object.defineProperty(obj, 'logGrantsInTotal', {
            get: function() {
                return calculateLogValue(this.grantsInTotal);
            },
            enumerable: true,
            configurable: true
        });

}

function logPropBuilderGrantsTotal(obj) {

       Object.defineProperty(obj, 'logGrantsTotal', {
            get: function() {
                return calculateLogValue(this.grantsTotal);
            },
            enumerable: true,
            configurable: true
        });

}

function applyCharityProps(obj){

        logPropBuilderRawAmt(obj);
        logPropBuilderGrantAmt(obj);
        sumPropBuilderGrantsIn(obj);
        sumPropBuilderGrantsTotal(obj);
        logPropBuilderGrantsIn(obj);
        logPropBuilderGrantsTotal(obj);
        return obj; 
}

function applyGrantProps(obj) 
{
        logPropBuilderValue(obj);
        return obj;

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
                    // Define charity with logGrantAmt as a getter
                    charities[ein] = applyCharityProps({
                        id: ein,
                        filer_ein: ein,
                        name: (row['filer_name'] || '').trim(),
                        xml_name: row['xml_name'],
                        receipt_amt: rAmt,
                        govt_amt: parseInt((row['govt_amt'] || '0').trim(), 10) || 0,
                        contrib_amt: parseInt((row['contrib_amt'] || '0').trim(), 10) || 0,
                        grant_amt: 0,
                        grant_in_index: 0,
                        grant_out_index: 0,
                        grant_count: 0,
                        grants: [], // Array to store grants for this filer
                        grantsIn: [],
                        loopbacks: []
                    });
                    // Define logGrantAmt as a getter
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
       const gov_ein="001";
       // government is the root, build a virtual charity for it
       charities[gov_ein] = applyCharityProps({
         id: gov_ein,
         filer_ein:gov_ein,
         name: "US Government",
         xml_name: "The Beast",
         govt_amt: 0,
         contib_amt: 4.6e12,
         grant_amt: 0,
         grant_in_index: 0,
         grant_out_index: 0,
         grant_count: 0,
         grants: [], // Array to store grants for this filer
         grantsIn: []
       });
       // Define logGrantAmt as a getter for the government
       let govGrants=0;
       let govTotal=0;
       let localEdges = {};
       Object.values(charities).forEach( c => { 
           if (c.govt_amt > 0 ) {
                    const filer = gov_ein
                    const grantee = c.id;
                    let amt = c.govt_amt;
                    if (isNaN(amt)) amt = 0;
                    govGrants++;
                    const key = filer + '~' + grantee;
                    if (!localEdges[key]) {
                        localEdges[key] = applyGrantProps({ 
                            id: `${filer}~${grantee}`,
                            rawAmt: 0, 
                            relOutAmt: 0, 
                            relInAmt: 0, 
                            filer_ein: filer, 
                            grantee_ein: grantee, 
                            loopback: 0,
                            filer: charities[gov_ein], 
                            grantee: charities[grantee] 
                        });
                        // Define logRawAmt as a getter
                     logPropBuilderRawAmt(localEdges[key]);
                    }
                    localEdges[key].rawAmt += amt;
                    charities[filer].grant_amt += amt;
                    govTotal += amt;
                    charities[filer].grant_count += 1;
                    charities[filer].grant_out_index += 1;
                    charities[grantee].grant_in_index += 1;
                    // Store grant in filer's grants array
                    charities[filer].grants.push(localEdges[key]);
                    charities[grantee].grantsIn.push(localEdges[key])
                }
           });
       charities[gov_ein].grant_amt = govTotal;
       console.log(govGrants," Implied Government Grants Generated");
       console.log(formatNumber(govTotal)," Gov Total");
       Papa.parse(grantsCsvString, {
            header: true,
            skipEmptyLines: true,
            complete: results => {
                let count = 0;
                results.data.forEach(row => {
                    const filer = (row['filer_ein'] || '').trim();
                    const grantee = (row['grant_ein'] || '').trim();
                    let amt = parseInt((row['grant_amt'] || '0').trim(), 10);
                    if (isNaN(amt)) amt = 0;
                    count++;
                    if (charities[filer] && charities[grantee] && (filer != grantee)) { // ignore self grants
                        const key = filer + '~' + grantee;
                        if (!localEdges[key]) {
                            localEdges[key] = applyGrantProps({ 
                                id: `${filer}~${grantee}`,
                                circular: false, 
                                rawAmt: 0, 
                                relOutAmt: 0, 
                                relInAmt: 0, 
                                filer_ein: filer, 
                                grantee_ein: grantee, 
                                filer: charities[filer], 
                                grantee: charities[grantee] 
                            });
                            // Define logRawAmt as a getter
                        }
                        localEdges[key].rawAmt += amt;
                        charities[filer].grant_amt += amt;
                        charities[filer].grant_count += 1;
                        charities[filer].grant_out_index += 1;
                        charities[grantee].grant_in_index += 1;
                        // Store grant in filer's grants array
                        charities[filer].grants.push(localEdges[key]);
                        charities[grantee].grantsIn.push(localEdges[key])
                    } else {
                        if (filer != grantee)
                            console.warn(`Missing charity for EIN: ${filer} or ${grantee}`);
                    }
                });
                grants = localEdges;
                totalGrantsCount = count;
                resolve();
            },
            error: err => reject(err)
        });
    });
    badGrants = findCircularGrants();
    
    badCharsCounter = new Set();
    badGrants.forEach(g => {
        badCharsCounter.add(g.grantee.id);
        g.filer.loopbacks.push(g);
        g.grantee.loopbacks.push(g);
        g.grantee.loopback = g.grantee.grants.filter(g=> g.isCycle).reduce((total,g) => total+g.rawAmt,0);
        g.grantee.grants = g.grantee.grants.filter(g => !g.isCycle);
        g.grantee.grant_amt = g.grantee.grants.reduce((total, grant) => total + grant.rawAmt, 0);
        
        //console.log(`removed circular grant ${g.id} from ${g.filer.name}->${g.grantee.name}`);
    });
    // No need to set logGrantAmt here, as it's now a getter for all charities
    console.log(totalCharitiesCount, "501c3s loaded");
    console.log(totalGrantsCount, "grants loaded");

    // Initialize Sankey maps after data load
    nodeMap = charities;
    linkMap = grants;
}

function findCircularGrants() {
    const visited = new Set();       // Fully processed charity IDs
    const onStack = new Set();       // Charity IDs in the current DFS path
    const cycleGrants = new Set();   // Grant objects forming cycles
    const stack = [];                // Stack: [charity, grantIterator, parentGrant]

    // Iterate over all charities in the global object
    for (const startId in charities) {
        if (visited.has(startId)) continue;

        const startCharity = charities[startId];
        stack.push([
            startCharity,
            (startCharity.grants || [])[Symbol.iterator](),
            null // No parent grant for the root
        ]);

        while (stack.length > 0) {
            let [charity, grantIter, parentGrant] = stack[stack.length - 1];
            const charityId = charity.id;

            if (!visited.has(charityId)) {
                visited.add(charityId);
                onStack.add(charityId);
            }

            // Get the next grant
            const iterResult = grantIter.next();
            if (!iterResult.done) {
                const grant = iterResult.value;
                const grantee = grant.grantee; // Direct reference to the grantee charity
                const granteeId = grantee.id;

                if (onStack.has(granteeId)) {
                    // Cycle detected: mark the current grant
                    cycleGrants.add(grant);
                } else if (!visited.has(granteeId)) {
                    // Explore the grantee next
                    stack.push([
                        grantee,
                        (grantee.grants || [])[Symbol.iterator](),
                        grant // The grant that led here
                    ]);
                }
            } else {
                // No more grants; backtrack
                stack.pop();
                onStack.delete(charityId);
            }
        }
    }

    // Mark the grants in the global charities object
    charitiesWithBadGrants=0;
    for (const charityId in charities) {
        const charity = charities[charityId];
        charity.grants.forEach(grant => {
            grant.isCycle = cycleGrants.has(grant);
            charitiesWithBadGrants++;
        });
    }
    console.log(`${charitiesWithBadGrants} charities had bad grants`);
    console.log(`${cycleGrants.size} bad grants`);
    return cycleGrants; // Optional: return Set of grant objects in cycles
}
// Output: CharityC's grant to CharityA will be marked as isCycle: true

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
        .nodeId(d => d.filer_ein || d.id)
        .nodeWidth(50)
        .nodePadding(80)
        .extent([[0, 0], [width - 200, height - 100]]);

    currentData = { nodes: [], links: [] };
    expandedOutflows.clear(); // Reset expansion state

    // Start with "US Government" as the initial focus
    renderFocusedSankey(g, sankey, svg, width, height, "001");

    document.getElementById('zoomIn').onclick = () => {
        svg.transition().duration(300).call(zoom.scaleBy, 1.3);
    };

    document.getElementById('zoomOut').onclick = () => {
        svg.transition().duration(300).call(zoom.scaleBy, 0.7);
    };

    document.getElementById('zoomFit').onclick = () => {
        const bounds = g.node().getBBox();
        const dx = bounds.x;
        const dy = bounds.y;
        const scale = 0.8 / Math.max(bounds.width / width, bounds.height / height);
        svg.transition()
            .duration(750)
            .call(zoom.transform,
                d3.zoomIdentity
                    .translate(width/2, height/2)
                    .scale(scale)
                    .translate(-dx - bounds.width/2, -dy - bounds.height/2));
    };

    setTimeout(() => {
        const bounds = g.node().getBBox();
        const dx = bounds.x;
        const dy = bounds.y;
        const scale = 0.8 / Math.max(bounds.width / width, bounds.height / height);
        svg.transition()
            .duration(750)
            .call(zoom.transform,
                d3.zoomIdentity
                    .translate(width/2, height/2)
                    .scale(scale)
                    .translate(-dx - bounds.width/2, -dy - bounds.height/2));
    }, 1000);

    $('#loading').hide();
    console.log('Graph generation complete');
}

function expandNode(nodeId, isInitial = false, addToActiveEINs = false) {
    const node = charities[nodeId];
    if (!node) {
        currentData.nodes.push(applyCharityProps({ 
            id: nodeId, 
            filer_ein: nodeId, 
            name: `Unknown (${nodeId})`, 
            grant_amt: 0,
            grants: [], 
            grantsIn: []
            
        }));
        if (!nodeMap.hasOwnProperty(nodeId)) nodeMap[nodeId] = currentData.nodes[currentData.nodes.length - 1];
        return;
    }

    if (!currentData.nodes.some(n => n.filer_ein === nodeId)) {
        currentData.nodes.push(node);
    }

    const grantsForNode = node.grants || [];
    const allLinks = node.grants.sort((a, b) => b.rawValue - a.rawValue);

    let displayedLinks = expandedOutflows.get(nodeId) || [];
    const remainingLinks = allLinks.filter(l => 
        !displayedLinks.some(d => d.target === l.target)
    );

    const newLinks = remainingLinks.slice(0, TOP_N_OUTFLOWS);
    const othersLinks = remainingLinks.slice(newLinks.length);

    newLinks.forEach(l => {
        if (!currentData.nodes.some(n => n.filer_ein === l.target)) {
            currentData.nodes.push(applyCharityProps(charities[l.target]));
        }
        if (!currentData.links.some(link => link.source === l.source && link.target === l.target)) {
            const l=applyGrantProps({ 
                source: l.source, 
                target: l.target, 
                value: l.value, 
                rawValue: l.rawValue,
                filer: l.filer,
                grantee: l.grantee
            });
           currentData.links.push(l);
        }
        displayedLinks.push(l);
    });

    expandedOutflows.set(nodeId, displayedLinks);

    const othersId = `${nodeId}-others-${generateUniqueId()}`;
    if (othersLinks.length > 0 && !isInitial) {
        const othersValue = othersLinks.reduce((sum, l) => sum + l.rawValue, 0);
        const logOthersValue = calculateLogRawAmt(othersValue); // Use function
        const existingOthers = currentData.nodes.find(n => n.filer_ein === othersId);
        
        if (!existingOthers) {
            const c=applyCharityProps({ 
                id: othersId,
                filer_ein: othersId, 
                name: "...",
                grant_amt: othersValue,
                parent_ein: nodeId,
                grants: [], 
                grantsIn: []
            });

           currentData.nodes.push(c);
           
        } else {
        existingOthers.grant_amt = othersValue;
           applyCharityProps(existingOthers);
        }

        const othersLink = currentData.links.find(l => l.source === nodeId && l.target === othersId);
        if (!othersLink) {
            let c=applyCharityProps({ 
                id: othersId,
                filer_ein: othersId, 
                name: "...",
                grant_amt: othersValue,
                parent_ein: nodeId,
                grants: [], 
                grantsIn: []
            });
            currentData.links.push(c);
        } else {
            logPropBuilderValue(othersLink);
            othersLink.rawValue = othersValue;
        }
    } else {
        currentData.nodes = currentData.nodes.filter(n => !n.filer_ein.startsWith(`${nodeId}-others-`));
        currentData.links = currentData.links.filter(l => !l.target.startsWith(`${nodeId}-others-`));
    }

    if (addToActiveEINs && !activeEINs.includes(nodeId) && nodeMap.hasOwnProperty(nodeId)) {
        activeEINs.push(nodeId);
        renderActiveEINs();
        updateQueryParams();
    }
}

function expandOthers(sourceId) {
    const node = charities[sourceId];
    if (!node) return;

    const grantsForNode = node.grants || [];
    const allLinks = grantsForNode.map(grant => {
    
        const g= {
                source: grant.filer_ein,
                target: grant.grantee_ein,
                filer: grant.filer,
                grantee: grant.grantee,
                value: grant.logRawAmt , // Use function
                rawValue: grant.rawAmt
        };
        logPropBuilderValue(g);
        return g;        
    
    }).sort((a, b) => b.rawValue - a.rawValue);

    let displayedLinks = expandedOutflows.get(sourceId) || [];
    const remainingLinks = allLinks.filter(l => 
        !displayedLinks.some(d => d.target === l.target)
    );

    const newLinks = remainingLinks.slice(0, TOP_N_OUTFLOWS);
    newLinks.forEach(l => displayedLinks.push(l));
    expandedOutflows.set(sourceId, displayedLinks);

    const container = document.getElementById('graph-container');
    const width = container.offsetWidth;
    const height = container.offsetHeight || window.innerHeight * 0.7;

    renderFocusedSankey(svg.select('g'), d3.sankey()
        .nodeId(d => d.filer_ein || d.id)
        .nodeWidth(NODE_WIDTH)
        .nodeWidth(NODE_PADDING)
        .extent([[0, 0], [width, height]]), svg, width, height, sourceId);
}

function handleSearchClick(e) {
    const ein = e.target.dataset.ein;
    if (!ein) return;

    const searchInput = document.getElementById('searchInput');
    searchInput.value = charities[ein].name;
    document.getElementById('searchResults').classList.add('hidden');

    expandNode(ein, false, true); // Add to activeEINs
    const container = document.getElementById('graph-container');
    const width = container.offsetWidth;
    const height = container.offsetHeight || window.innerHeight * 0.7;
    renderFocusedSankey(svg.select('g'), d3.sankey()
        .nodeId(d => d.filer_ein || d.id)
        .nodeWidth(50)
        .nodePadding(80)
        .extent([[0, 0], [width - 200, height - 100]]), svg, width, height);
}
function generateUniqueId(prefix = "gradient") {
    return `${prefix}-${Math.random().toString(36).substr(2, 9)}`; // Short, random ID
}

function calculateScale(graph, width, height) {
    // Calculate the bounding box of the Sankey layout
    const nodes = graph.nodes;
    const links = graph.links;

    if (!nodes.length || !links.length) {
        return 1; // Default scale if no data
    }

    // Find the min and max x and y coordinates of nodes and links
    let minX = Infinity, maxX = -Infinity;
    let minY = Infinity, maxY = -Infinity;

    nodes.forEach(node => {
        minX = Math.min(minX, node.x0);
        maxX = Math.max(maxX, node.x1);
        minY = Math.min(minY, node.y0);
        maxY = Math.max(maxY, node.y1);
    });

    links.forEach(link => {
        const path = d3.sankeyLinkHorizontal()(link);
        const points = path.match(/[ML][\d.]+,[\d.]+/g).map(p => p.slice(1).split(',').map(Number));
        points.forEach(([x, y]) => {
            minX = Math.min(minX, x);
            maxX = Math.max(maxX, x);
            minY = Math.min(minY, y);
            maxY = Math.max(maxY, y);
        });
    });

    // Calculate the layout dimensions
    const layoutWidth = maxX - minX;
    const layoutHeight = maxY - minY;

    // Ensure non-zero dimensions to avoid division by zero
    const actualWidth = Math.max(layoutWidth, 1);
    const actualHeight = Math.max(layoutHeight, 1);

    // Calculate scale to fit within SVG while maintaining aspect ratio
    const scaleX = width / actualWidth;
    const scaleY = height / actualHeight;
    const scale = Math.min(scaleX, scaleY, 1); // Ensure scale doesn't exceed 1 (no zoom-in)

    return scale;
}

function generateTrapezoidPath(d) {

  const midY = (d.y0 + d.y1) / 2;
  const y0In = midY - d.inflowHeight / 2;  // Left = inflow (0 for USG)
  const y1In = midY + d.inflowHeight / 2;
  const y0Out = midY - d.outflowHeight / 2; // Right = outflow (367 for USG)
  const y1Out = midY + d.outflowHeight / 2;
  return `M${d.x0},${y0In} L${d.x0},${y1In} L${d.x1},${y1Out} L${d.x1},${y0Out} Z`;

}

function calculateScale(graph, width, height) {
    // Calculate the bounding box of the Sankey layout
    const nodes = graph.nodes;
    const links = graph.links;

    if (!nodes.length || !links.length) {
        return 1; // Default scale if no data
    }

    // Find the min and max x and y coordinates of nodes and links
    let minX = Infinity, maxX = -Infinity;
    let minY = Infinity, maxY = -Infinity;

    nodes.forEach(node => {
        minX = Math.min(minX, node.x0);
        maxX = Math.max(maxX, node.x1);
        minY = Math.min(minY, node.y0);
        maxY = Math.max(maxY, node.y1);
    });

    links.forEach(link => {
        const path = d3.sankeyLinkHorizontal()(link);
        const points = path.match(/[ML][\d.]+,[\d.]+/g).map(p => p.slice(1).split(',').map(Number));
        points.forEach(([x, y]) => {
            minX = Math.min(minX, x);
            maxX = Math.max(maxX, x);
            minY = Math.min(minY, y);
            maxY = Math.max(maxY, y);
        });
    });

    // Calculate the layout dimensions, ensuring non-zero values
    const layoutWidth = Math.max(maxX - minX, 1);
    const layoutHeight = Math.max(maxY - minY, 1);

    // Calculate scale to fit within SVG while maintaining aspect ratio, no cap at 1
    const scaleX = width / layoutWidth;
    const scaleY = height / layoutHeight;
    const scale = Math.min(scaleX, scaleY); // Use the smaller scale to fit, no upper limit

    return scale;
}

function calculateNodePositions(nodes, kyHeight, scale, height) {
  nodes.forEach(d => {
   const sourceData = d.sourceLinks.length > 0 ? d.sourceLinks : (d.grantsIn || []);
    d.logGrantsInTotal = calculateLogValue(d3.sum(sourceData, l => l.value || 0));
    
    const targetData = d.targetLinks.length > 0 ? d.targetLinks : (d.grants || []);
    d.logGrantsTotal = calculateLogValue(d3.sum(targetData, g => g.amount || g.value || 0));

    const sankeyHeight = d.y1 - d.y0;
    d.outflowHeight = Math.min(sankeyHeight, d.logGrantsTotal * kyHeight);
    const inflowScaleFactor = d.logGrantsInTotal / (d.logGrantsTotal || 1);
    d.inflowHeight = d.outflowHeight * inflowScaleFactor;
    if (d.inflowHeight > sankeyHeight) {
      d.inflowHeight = sankeyHeight;
      d.outflowHeight = sankeyHeight / inflowScaleFactor;
    }

    d.x0Original = d.x0;
    d.x1Original = d.x1;
    d.y0Original = d.y0;
    d.y1Original = d.y1;
   });
}


function renderFocusedSankey(g, sankey, svgRef, width, height, selectedNodeId) {
    const selectedNode = charities[selectedNodeId];
    if (!selectedNode) return;

    currentData = { nodes: [], links: [] };
    currentData.nodes.push(selectedNode);

    // Debug: Check selectedNode data
    console.log("Selected Node:", selectedNodeId, selectedNode);
    console.log("Grant Amount (raw):", selectedNode.grant_amt);

    // Inflows
    (selectedNode.grantsIn || []).forEach(grant => {
        if (!currentData.nodes.some(n => n.filer_ein === grant.filer_ein)) {
            currentData.nodes.push(grant.filer);
        }
        // Debug: Check grant data
        const rawAmt = grant.rawAmt || 0;
        //console.log("Grant In:", grant.filer_ein, "Raw Amount:", rawAmt);
        currentData.links.push({
            source: grant.filer_ein,
            target: selectedNodeId,
            value: grant.logRawAmt, // Use getter
            rawValue: rawAmt, // Keep raw value for tooltips
            filer: grant.filer,
            grantee: selectedNode
        });
    });

    // Outflows
    const displayedLinks = expandedOutflows.get(selectedNodeId) || [];
    const allOutLinks = (selectedNode.grants || []).map(grant => {
        // Debug: Check grant data
        const rawAmt = grant.rawAmt || 0;
        return {
            source: grant.filer_ein,
            target: grant.grantee_ein,
            filer: grant.filer,
            grantee: grant.grantee,
            value: grant.logRawAmt, // Use getter
            rawValue: rawAmt // Keep raw value for tooltips
        };
    }).sort((a, b) => b.rawValue - a.rawValue);
    const remainingOutLinks = allOutLinks.filter(l => !displayedLinks.some(d => d.target === l.target));
    const newOutLinks = remainingOutLinks.slice(0, TOP_N_OUTFLOWS);
    const othersOutLinks = remainingOutLinks.slice(newOutLinks.length);

    newOutLinks.forEach(l => {
        if (!currentData.nodes.some(n => n.filer_ein === l.target)) {
            currentData.nodes.push(charities[l.target]);
        }
        currentData.links.push(l);
        displayedLinks.push(l);
    });

    if (othersOutLinks.length > 0) {
        const othersId = `${selectedNodeId}-others-${generateUniqueId()}`;
        const othersValue = othersOutLinks.reduce((sum, l) => sum + l.rawValue, 0);
             const   c=applyCharityProps({
            id: othersId,
            filer_ein: othersId,
            name: "...",
            grant_amt: othersValue,
            parent_ein: selectedNodeId,
            grants:[],
            grantsIn:[]
        });
        currentData.nodes.push(c);
        const l=applyGrantProps({
            source: selectedNodeId,
            target: othersId,
            rawValue: othersValue,
            filer: selectedNode,
            grantee: null
        });
        currentData.links.push(l);
    }

    expandedOutflows.set(selectedNodeId, displayedLinks);

    currentData.nodes.sort((a, b) => {
        if (a.filer_ein === selectedNodeId) return -1;
        if (b.filer_ein === selectedNodeId) return 1;
        if (a.filer_ein.includes('-others-') && !b.filer_ein.includes('-others-')) return 1;
        if (!a.filer_ein.includes('-others-') && b.filer_ein.includes('-others-')) return -1;
        return 0;
    });

    // Configure D3 Sankey for proper node spacing and centering
    sankey.nodeWidth(NODE_WIDTH) // Set node width to 15 pixels
        .nodeWidth(NODE_PADDING) // Set padding between nodes to 10 pixels
        .extent([[0, 0], [width, height]]) // Define the layout extent
        .nodeAlign(d3.sankeyCenter); // Center nodes vertically

    // First pass: Compute initial layout
    const graph = sankey(currentData);
    const color = d3.scaleOrdinal(d3.schemeCategory10);

    // Dynamically calculate scale based on layout and SVG dimensions
    const scale = calculateScale(graph, width, height);

    // Dynamically calculate maximum log values for scaling
    const maxLogGrant = d3.max(currentData.nodes, d => d.logGrantAmt);
    const maxLogRaw = d3.max(currentData.links, d => d.value);

    // Calculate maximum nodes per column for height scaling (D3 Sankey logic)
    const maxNodesPerColumn = d3.max(graph.nodes, d => d.layer === undefined ? 0 : d.layer) + 1 || 1;
    const availableHeight = height - (sankey.nodePadding() * (maxNodesPerColumn - 1));
    const totalLogGrant = d3.sum(currentData.nodes, d => d.logGrantAmt);
    const kyHeight = availableHeight / (totalLogGrant || 1); // D3 Sankey’s ky for heights

    // Calculate available width for link scaling (D3 Sankey logic)
    const availableWidth = width - sankey.nodeWidth();
    const totalLogRaw = d3.sum(currentData.links, d => d.value);
    const kyWidth = availableWidth / (totalLogRaw || 1); // D3 Sankey’s ky for widths, ensuring ~45 pixels for $11.1B untransformed

    // Calculate node positions before logging or rendering
    calculateNodePositions(graph.nodes, kyHeight, scale, height);

    // Log node details after positions are calculated, including original positions
    graph.nodes.forEach(d => {
        console.log("Node:", d.name, "Grant Amount (raw):", d.grant_amt || 0, "x0:", d.x0Original, "x1:", d.x1Original, "y0:", d.y0Original, "y1:", d.y1Original);
    });

    // Explicitly set y0/y1 for selected node based on inflows/outflows, ensuring y0Out/y1Out are used
    if (selectedNode) {
        const inflowLinks = graph.links.filter(l => l.target.id === selectedNodeId);
        if (inflowLinks.length > 0) {
            selectedNode.y0 = selectedNode.y0In || selectedNode.y0;
            selectedNode.y1 = selectedNode.y1In || selectedNode.y1;
        }
        const outflowLinks = graph.links.filter(l => l.source.id === selectedNodeId);
        if (outflowLinks.length > 0) {
            selectedNode.y0 = selectedNode.y0Out || selectedNode.y0;
            selectedNode.y1 = selectedNode.y1Out || selectedNode.y1;
        }
    }

    // Recompute link positions with adjusted y0/y1
    sankey.update(graph); // Update link coordinates based on new y0/y1

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
        .attr("stop-color", d => color(d.source.id));

    gradients.append("stop")
        .attr("offset", "100%")
        .attr("stop-color", d => color(d.target.id));

    const masterGroup = g.append("g")
        .attr("class", "graph-group")
        .attr("transform", `scale(${scale})`); // Apply single dynamic scale

    const nodeGroup = masterGroup.append('g').attr('class', 'nodes');
    const nodeElements = nodeGroup.selectAll('g')
        .data(graph.nodes)
        .join('g')
        .attr('class', d => {
            if (d.filer_ein.includes('-others-')) return 'node others';
            if (!d.grants || d.grants.length === 0) return 'node no-grants';
            return 'node';
        })
        .attr('data-id', d => d.id);

    nodeElements.each(function(d) {
        const sel = d3.select(this);
        if (d.filer_ein.includes('-others-') || (!d.grants || d.grants.length === 0)) {
            sel.append("circle")
                .attr("cx", d => (d.x0 + d.x1) / 2)
                .attr("cy", d => (d.y0 + d.y1) / 2)
                .attr("r", Math.min(10,Math.max(2, (d.logGrantAmt)))) // Scale radius, minimum 2 pixels, adjust for transform
                .attr("fill", d => color(d.id))
                .attr("stroke", "#000");
        } else {
            sel.append("path")
                .attr("d", d => generateTrapezoidPath(d))
                .attr("fill", d => color(d.id))
                .attr("stroke", "#000");
        }
    });
    
    function nodeClick(event,d) {
        event.stopPropagation();
        if (event.altKey) {
            generateGraph();
        } else if (d.filer_ein.includes('-others-')) {
            const sourceId = d.parent_ein;
            expandOthers(sourceId);
        } else {
            console.log("Switching to node:", d.filer_ein);
            renderFocusedSankey(g, sankey, svgRef, width, height, d.filer_ein);
        }
    }

    nodeElements.on('click', nodeClick);

    nodeElements.append("title")
        .text(d => {
                const top = `${d.name || d.id}\nInflow: $${formatNumber((d.grantsIn || []).reduce((sum, g) => sum + g.rawAmt, 0))}\nOutflow: $${formatNumber(d.grant_amt || 0)}`;
                if (d.loopback) return `{top}\nLoop: $${formatNumber(d.loopback || 0)}`;
                return top
        });

    const link = masterGroup.append("g")
        .attr("fill", "none")
        .attr("stroke-opacity", 1)
        .style("mix-blend-mode", "multiply")
        .selectAll(".link")
        .data(graph.links)
        .join("path")
        .attr("d", d3.sankeyLinkHorizontal())
        .style("stroke", d => d.target.filer_ein.includes('-others-') ? "#ccc" : `url(#${d.gradientId})`)
        .style("stroke-opacity", "0.3")
        .attr("stroke-width", d => Math.max(1, d.width || 1))
        .on('click', function(event, d) {
            event.stopPropagation();
            if (event.shiftKey)
            {
                nodeClick(event,d.source);
            }
            else
            {
                nodeClick(event,d.target);
            }
            zoomToFitNodes(d.source, d.target, width, height);
        });

    link.each(function(d) {
        if (d3.select(this).style("stroke") === "none") {
            d3.select(this).style("stroke", color(d.source.id));
        }
    });

    link.append("title")
        .text(d => d.target.filer_ein.includes('-others-') 
            ? `${d.source.name} → ...\n$${formatNumber(d.rawValue)}` 
            : `${d.source.name} → ${d.target.name}\n$${formatNumber(d.rawValue)}`);

    masterGroup.append("g")
        .selectAll()
        .data(graph.nodes)
        .join("text")
        .attr("x", d => (d.x0Original < sankey.nodeWidth() / 2 ? d.x1Original + 6 : d.x0Original - 6) * scale)
        .attr("y", d => ((d.y1Original + d.y0Original) / 2) * scale)
        .attr("dy", "0.35em")
        .attr("text-anchor", d => d.x0Original < sankey.nodeWidth() / 2 ? "start" : "end")
        .text(d => d.name)
        .on('click', function(event, d) {
            event.stopPropagation();
            if (event.altKey) {
                generateGraph();
            } else if (d.filer_ein.includes('-others-')) {
                const sourceId = d.parent_ein;
                expandOthers(sourceId);
            } else {
                console.log("Text click switching to node:", d.filer_ein);
                renderFocusedSankey(g, sankey, svgRef, width, height, d.filer_ein);
            }
        });

    const zoom = d3.zoom()
        .scaleExtent([0.1, 4])
        .on('zoom', (event) => {
            masterGroup.attr('transform', `scale(${scale * event.transform.k}) translate(${event.transform.x / scale},${event.transform.y / scale})`);
        });

    svgRef.call(zoom);
}

function expandNode(nodeId, isInitial = false, addToActiveEINs = false) {
    const node = charities[nodeId];
    if (!node) {
        currentData.nodes.push({ 
            id: nodeId, 
            filer_ein: nodeId, 
            name: `Unknown (${nodeId})`, 
            grant_amt: 0 
        });
        if (!nodeMap.hasOwnProperty(nodeId)) nodeMap[nodeId] = currentData.nodes[currentData.nodes.length - 1];
        return;
    }

    if (!currentData.nodes.some(n => n.filer_ein === nodeId)) {
        currentData.nodes.push(node);
    }

    const grantsForNode = node.grants || [];
    const allLinks = grantsForNode.map(grant => ({
        source: grant.filer_ein,
        target: grant.grantee_ein,
        filer: grant.filer,
        grantee: grant.grantee,
        value: grant.logRawAmt, // Use log-scaled accessor for widths
        rawValue: grant.rawAmt // Keep raw value for tooltips and linear scaling
    })).sort((a, b) => b.rawValue - a.rawValue);

    let displayedLinks = expandedOutflows.get(nodeId) || [];
    const remainingLinks = allLinks.filter(l => 
        !displayedLinks.some(d => d.target === l.target)
    );

    const newLinks = remainingLinks.slice(0, TOP_N_OUTFLOWS);
    const othersLinks = remainingLinks.slice(newLinks.length);

    newLinks.forEach(l => {
        if (!currentData.nodes.some(n => n.filer_ein === l.target)) {
            currentData.nodes.push(charities[l.target]);
        }
        if (!currentData.links.some(link => link.source === l.source && link.target === l.target)) {
            currentData.links.push({ 
                source: l.source, 
                target: l.target, 
                value: l.value, // Use log-scaled value
                rawValue: l.rawValue, // Keep raw value
                filer: l.filer,
                grantee: l.grantee
            });
        }
        displayedLinks.push(l);
    });

    expandedOutflows.set(nodeId, displayedLinks);

    const othersId = `${nodeId}-others-${generateUniqueId()}`;
    if (othersLinks.length > 0 && !isInitial) {
        const othersValue = othersLinks.reduce((sum, l) => sum + l.rawValue, 0);
        const existingOthers = currentData.nodes.find(n => n.filer_ein === othersId);
        
        if (!existingOthers) {
            const n=applyCharityProps({ 
                id: othersId,
                filer_ein: othersId, 
                name: "...",
                grant_amt: othersValue,
                parent_ein: nodeId,
                grants:[],
                grantsIn:[]
            });
            currentData.nodes.push(n);
        } else {
            existingOthers.grant_amt = othersValue;
            applyCharityProps(existingOthers);
        }

        const othersLink = currentData.links.find(l => l.source === nodeId && l.target === othersId);
        if (!othersLink) {
                const l=applyGrantProps({ 
                        source: nodeId, 
                        target: othersId, 
                        rawValue: othersValue,
                        filer: charities[nodeId],
                        grantee: null
                });        
                currentData.links.push(l);
        } else {
                logPropBuilderValue(othersLink);
            othersLink.rawValue = othersValue;
        }
    } else {
        currentData.nodes = currentData.nodes.filter(n => !n.filer_ein.startsWith(`${nodeId}-others-`));
        currentData.links = currentData.links.filter(l => !l.target.startsWith(`${nodeId}-others-`));
    }

    if (addToActiveEINs && !activeEINs.includes(nodeId) && nodeMap.hasOwnProperty(nodeId)) {
        activeEINs.push(nodeId);
        renderActiveEINs();
        updateQueryParams();
    }
}

function expandOthers(sourceId) {
    const node = charities[sourceId];
    if (!node) return;

    const grantsForNode = node.grants || [];
    const allLinks = grantsForNode.map(grant => ({
        source: grant.filer_ein,
        target: grant.grantee_ein,
        filer: grant.filer,
        grantee: grant.grantee,
        value: grant.logRawAmt, // Use log-scaled accessor
        rawValue: grant.rawAmt // Keep raw value
    })).sort((a, b) => b.rawValue - a.rawValue);

    let displayedLinks = expandedOutflows.get(sourceId) || [];
    const remainingLinks = allLinks.filter(l => 
        !displayedLinks.some(d => d.target === l.target)
    );

    const newLinks = remainingLinks.slice(0, TOP_N_OUTFLOWS);
    newLinks.forEach(l => displayedLinks.push(l));
    expandedOutflows.set(sourceId, displayedLinks);

    const container = document.getElementById('graph-container');
    const width = container.offsetWidth;
    const height = container.offsetHeight || window.innerHeight * 0.7;

    renderFocusedSankey(svg.select('g'), d3.sankey()
        .nodeId(d => d.filer_ein || d.id)
        .nodeWidth(NODE_WIDTH)
        .nodeWidth(NODE_PADDING)
        .extent([[0, 0], [width, height]]), svg, width, height, sourceId);
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

const extraStyle = `.node.others { fill: #ccc; cursor: pointer; }
                    .node { fill: #999; } // Default node color (light gray, overridden by unique colors)
                    .link { stroke: #ccc; stroke-opacity: 0.5; } // Light gray ribbons, 50% opacity
                    #graph { background: #fff !important; } // Ensure white background, override any dark themes
                    text { fill: #000; } // Ensure black text for labels
                    svg { background: #fff !important; } // Double-check SVG background`;
d3.select("head").append("style").text(extraStyle);
