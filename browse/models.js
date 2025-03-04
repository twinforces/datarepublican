 let POWER_LAW = 3;
 
 const START_REVEAL = 5;
 const MIN_REVEAL = 2;
 const NEXT_REVEAL = 1;
 const NEXT_REVEAL_MAX = 15;
 const GOV_EIN = '001';
 const MAX_NODES = 100;
 let GOV_NODE = null; // for easier debugging
 
 function graphScaleDown()
 {
        POWER_LAW++;
                
 }
 
 function graphScaleUp()
 {
        POWER_LAW--;
        if (POWER_LAW < 1) POWER_LAW=1;
                
 }
 
 function graphScaleReset()
 {
        POWER_LAW=3;
 }


 function scaleValue(amt) {
    return Math.pow(amt, 1/POWER_LAW); // 2rd root, 4th root, 5tth root, etc.
}

 function formatNumber(num) {
    if (num >= 1e12) return (num / 1e12).toFixed(1) + 'T';
    if (num >= 1e9) return (num / 1e9).toFixed(1) + 'B';
    if (num >= 1e6) return (num / 1e6).toFixed(1) + 'M';
    if (num >= 1e3) return (num / 1e3).toFixed(1) + 'K';
    return (num == null) ? "N/A" : num.toString();
}

 class Charity {
    static charityLookup = {};
    static hideList={};

    static getCharity(ein) {
        const parts = ein.split(':');
        return Charity.charityLookup[parts[0]];
    }

    static registerCharity(ein, c) {
        Charity.charityLookup[ein] = c;
    }
    static visibleCharities() {
        return Object.values(Charity.charityLookup).filter(g => g.isVisible);
    }
   static invisibleCharities() {
        return Object.values(Charity.charityLookup).filter(g => !g.isVisible);
    }

    static getCharityCount() {
        return Object.keys(Charity.charityLookup).length;
    }
    
    
    /**
       Why: Maintain the hide list, like a good looked View Model
    */
    static addToHideList(ein) {
        Charity.hideList[ein]=1;
    }
    
    static removeFromHideList(ein) {
        delete Charity.hideList[ein];
        Charity.getCharity(ein).show();
    }
    
    static getHideList() {
        return Object.keys(Charity.hideList);
    }
    
    static setHideList(list)
    {
        Charity.hideList = {};
        list.forEach(ein=> Charity.addToHideList(ein));
        return Charity.getHideList();
    }
    
    static shouldHide(ein) {
        return (Charity.hideList[ein]);
    }

    constructor({ 
        ein, 
        name, 
        xml_name, 
        govt_amt = 0, 
        contrib_amt = 0, 
        receipt_amt = 0,
        isVisible = false,
        isOther=false,
        grants = [], 
        grantsIn = [], 
        loopbackgrants = [], 
        loopforwards = [],
        otherUp = null,
        isOrganized=false,
        otherdown=null
    }) {
        this.id = ein;
        this.ein = ein;
        this.filer_ein = ein;
        this.name = name;
        this.xml_name = xml_name;
        this.receipt_amt = receipt_amt;
        this.govt_amt = govt_amt;
        this.contrib_amt = contrib_amt;
        // getter! this.grantsTotal = 0;
        // getter! this.grantsInTotal = 0;
        this.grants = grants;
        this.grantsIn = grantsIn;
        this.loopbackgrants = loopbackgrants;
        this.loopforwardgrants = loopforwards;
        this._isVisible = isVisible;
        this.isOrganized = isOrganized;
        this.isGov = false;
        this.isOther = isOther;
        this.expanded = false;
        this._valueCache={};
        this.sourceLinks=[]; //work around for sankey issue;
        this.targetLinks=[]; //work around for sankey issue;
        Charity.registerCharity(ein, this);
    }
        
    get isVisible() {
        return this._isVisible;
    
    }
    
    set isVisible(value) {
        if (this._isVisible != value)
        {
                this._isVisible = value;
                this.isOrganized = false; // force some recalc
                if (this.otherDown)
                {
                        this.otherDown.isVisible=value;
                        //this.otherDown.otherGrant.isVisible=value;
                }
                if (this.otherUp)
                {       
                        this.otherUp.isVisible=value;
                        //this.otherUp.otherGrant.isVisible=value;
                }
        }
    }
    
    // a terminal charity doesn't have any outflows.
    get isTerminal() {
        return (this.grants.length == 0);
    }
    
    // a root charity doesn't have any inflows
   get isRoot() {
        return (this.grantsIn.length == 0);
    }
     
    // useful for overall scale
    get logGrantsTotal() {
        const cacheKey = `logGrantTotal-${POWER_LAW}`;
        if (this._valueCache[cacheKey])
                return this._valueCache[cacheKey];
        return this._valueCache[cacheKey]=scaleValue(this.grantsTotal);
    }

    get logGrantsInTotal() {
        const cacheKey = `logGrantsInTotal-${POWER_LAW}`;
        if (this._valueCache[cacheKey])
                return this._valueCache[cacheKey];
        return this._valueCache[cacheKey]=scaleValue(this.grantsInTotal);
    }

    //useful for the sankey which doesn't know its values are scaled
    //ok, so the problems is that sqrt(a)+sqrt(b) != sqrt(a+b)
    // so what we have to do is return the size based on the visible grants only
    // unless there aren't any, in which case logGrantsTotal is fine as a placeholder.
    // this means the trapezoid will change as flows are revealed, but they'll match the trap
    get grantsLogTotal() {
        const vgrants = this.visibleGrants;
        const cacheKey = `grantsLogTotal-${POWER_LAW}-${vgrants.length}`;
        if (this._valueCache[cacheKey])
                return this._valueCache[cacheKey];
        if (vgrants.length)            
                return this._valueCache[cacheKey]= vgrants.reduce((total, g) => total + g.value, 0);
        return this.logGrantsTotal;
    }

    get grantsInLogTotal() {
        const vgrants = this.visibleGrantsIn;
        const cacheKey = `grantsInLogTotal-${POWER_LAW}-${vgrants.length}`;
        if (this._valueCache[cacheKey])
                return this._valueCache[cacheKey];
        if (vgrants.length)            
                return this._valueCache[cacheKey]= vgrants.reduce((total, g) => total + g.value, 0);
        return this.logGrantsInTotal;
    }

    get grantsTotal() {
        const cacheKey = `grantsTotal`;
        if (this._valueCache[cacheKey])
                return this._valueCache[cacheKey];
        return this._valueCache[cacheKey]=this.grants.reduce((total, g) => total + g.amt, 0);
    }

    get grantsInTotal() {
        const cacheKey = `grantsInTotal`;
        if (this._valueCache[cacheKey])
                return this._valueCache[cacheKey];
        return this._valueCache[cacheKey]=this.grantsIn.reduce((total, g) => total + g.amt, 0);
    }

    get visibleGrantsTotal() {
        const cacheKey = `visibleGrantsTotal`;
       if (this._valueCache[cacheKey])
                return this._valueCache[cacheKey];
        return this._valueCache[cacheKey]=this.visibleGrants.reduce((total, g) => total + g.amt, 0);
    }

    get invisibleGrantsTotal() {
        const cacheKey = `invisibleGrantsTotal`;
        if (this._valueCache[cacheKey])
                return this._valueCache[cacheKey];
        return this._valueCache[cacheKey]=this.invisibleGrants.reduce((total, g) => total + g.amt, 0);
    }

    get loopbackTotal() {
        const cacheKey = `loopbackTotal`;
        if (this._valueCache[cacheKey])
                return this._valueCache[cacheKey];
        return this._valueCache[cacheKey]=this.loopbackgrants.reduce((total, g) => total + g.amt, 0);
    }

    get loopForwardTotal() {
        const cacheKey = `loopforwardTotal`;
        if (this._valueCache[cacheKey])
                return this._valueCache[cacheKey];
        return this._valueCache[cacheKey]= this.loopforwards.reduce((total, g) => total + g.amt, 0);
    }

    get visibleGrants() {
        const cacheKey = `visibleGrants`;
        if (this._valueCache[cacheKey])
                return this._valueCache[cacheKey];
        return this._valueCache[cacheKey]=  this.grants.filter(g => g.isVisible).sort((a,b)=>b.amt-a.amt);
    }

    get invisibleGrants() {
        const cacheKey = `invisibleGrants`;
        if (this._valueCache[cacheKey])
                return this._valueCache[cacheKey];
        return this._valueCache[cacheKey]=   this.grants.filter(g => !g.isVisible);
    }
   get visibleGrantsIn() {
        const cacheKey = `visibleGrantsIn`;
        if (this._valueCache[cacheKey])
                return this._valueCache[cacheKey];
        return this._valueCache[cacheKey]=  this.grantsIn.filter(g => g.isVisible).sort((a,b)=>b.amt-a.amt);
    }

    get invisibleGrantsIn() {
        const cacheKey = `invisibleGrantsIn`;
        if (this._valueCache[cacheKey])
                return this._valueCache[cacheKey];
        return this._valueCache[cacheKey]=   this.grantsIn.filter(g => !g.isVisible);
    }
    
    set isOrganized(value) {
    
        if (this._isOrganized != value)
        {
                this._valueCache={}; //clear cache
        }
        this._isOrganized = value;
    }

    addGrant(grant) {
        if (grant instanceof Grant) {
            this.grants.push(grant);
            this.isOrganized = false;
        } else {
            console.log("Error: Can only add Grant objects.");
        }
    }

    addGrantIn(grant) {
        if (grant instanceof Grant) {
            this.grantsIn.push(grant);
            this.isOrganized = false;
        } else {
            console.log("Error: Can only add Grant objects.");
        }
    }

    static addGrant(g) {
        const filer = Charity.charityLookup[g.filer_ein];
        const grantee = Charity.charityLookup[g.grantee_ein];
        if (filer && grantee) {
            filer.addGrant(g);
            grantee.addGrantIn(g);
        }
    }

    removeGrant(grant) {
        const index = this.grants.indexOf(grant);
        if (index !== -1) {
            this.grants.splice(index, 1);
            this.isOrganized = false;
        }
    }
    removeGrantIn(grant) {
        const index = this.grantsIn.indexOf(grant);
        if (index !== -1) {
            this.grantsIn.splice(index, 1);
            this.isOrganized = false;
        }
    }


    /*
        make sure grants are organized in descending order
    */
    organize() {
        if (!this.isOrganized) {
            this.grants = this.grants.sort((a, b) => b.amt - a.amt);
            this.grantsIn = this.grantsIn.sort((a, b) => b.amt - a.amt);
            this.isOrganized = true;
        }
    }


    /*
        purge a circular grant
    */
    static circularGrant(g) {
        g.grantee.circleGrant(g);
        g.filer.circleGrant(g);
        Grant.unregisterGrant(g); // forget about it
    }

    /**
        purging a cicular grant from both sides
    */
    circleGrant(g) {
        if (g.filer == this)
        {
                this.loopbackgrants.push(g);
                this.removeGrant(g);
        }
        if (g.grantee == this)
        {
                this.loopforwardgrants.push(g);
                this.removeGrantIn(g);
        }
        this.isOrganized = false;
        this.isCircular = true;
    }

    get grantsTotalString() { 
        return formatNumber(this.grantsTotal);
    }

    get invisibleTotalString() { 
        return formatNumber(this.invisibleGrantsTotal);
    }

    /** Factory for the laodData */
    static buildCharityFromRow(row) {
        const ein = (row['filer_ein'] || '').trim();
        if (!ein) return;
        let rAmt = parseInt((row['receipt_amt'] || '0').trim(), 10);
        if (isNaN(rAmt)) rAmt = 0;
        return new Charity({
            ein: ein,
            name: (row['filer_name'] || '').trim(),
            xml_name: row['xml_name'],
            receipt_amt: rAmt,
            govt_amt: parseInt((row['govt_amt'] || '0').trim(), 10) || 0,
            contrib_amt: parseInt((row['contrib_amt'] || '0').trim(), 10) || 0,
        });
    }

    /**
      the Government is implied by the data  
    */
    static buildGovCharity() {
 
     updateStatus(`...Building US Govt from ${Charity.getCharityCount()}...`);

        const gov_ein = GOV_EIN;
        const gov_proto = {
            ein: gov_ein,
            filer_ein: gov_ein,
            name: "US Government",
            xml_name: "The Beast",
            contrib_amt: 4.6e12,
        };
        // have to build before grants so they can wire themselves in!
        const govChar = new Charity(gov_proto);
        let govGrants = 0;
        let govTotal = 0;
        Object.values(Charity.charityLookup).forEach(c => {
            if (c.govt_amt > 0) {
                const filer = gov_ein;
                const grantee = c.id;
                let amt = c.govt_amt;
                if (isNaN(amt)) amt = 0;
                govGrants++;
                const g = new Grant({
                    filer_ein: filer,
                    amt: amt,
                    grantee_ein: grantee,
                });
                govTotal += amt;
            }
        });

        updateStatus(`...Gov Charity complete, ${govChar.grants.length} generated, ${formatNumber(govTotal)}`)
        console.log(`${govGrants} Implied Government Grants Generated`);
        console.log(`Gov Total: ${formatNumber(govTotal)}`);
        console.log(`USG grants count: ${govChar.grants.length}, sample:`, 
            govChar.grants.slice(0, 5).map(g => ({ 
                target: g.grantee_ein, 
                amt: formatNumber(g.amt) 
            })));
        GOV_NODE = govChar;
        return govChar;
    }

   // check grants such that a gives to b, who gives to a
   simpleCircular() {
   
        const simpleCircles = this.grants.filter( 
                g1=> g1.grantee.grants.filter( 
                        g2=> g2.grantee == this).length);
        return simpleCircles;
        
        
    }
    /*
      for now, we simply hide any grant that circles back into a charities upstream
    */
 static async findCircularGrants() {


        function formatSimpleNumber(num) {
            return num.toLocaleString();
        }

    // Initialize data structures
    const visited = new Set();
    const onStack = new Set();
    const cycleGrants = new Set();
    let badTotal = 0;
    let charitiesWithBadGrants = 0;
    let obviousCirclesCount = 0;

    // Step 1: Handle obvious circular grants (fast pre-check)
    Object.values(Charity.charityLookup).forEach(charity => {
        const obviousCircles = charity.simpleCircular(); // Assumes this method exists
        obviousCirclesCount += obviousCircles.length;
        if (obviousCircles.length) charitiesWithBadGrants++;
        obviousCircles.forEach(grant => {
            grant.isCircular = true;
            cycleGrants.add(grant);
        });
    });
    console.log(`${obviousCirclesCount} obvious circular grants found`);
    updateStatus(`${obviousCirclesCount} obvious circular grants found`);

    // Step 2: Deep cycle detection with chunking
    updateStatus("...Finding Deep Loopback Grants...");

    // Calculate total grants for progress tracking
    const totalGrants = Object.values(Charity.charityLookup).reduce(
        (sum, charity) => sum + charity.grants.length,
        0
    );
    let processedGrants = 0;
    const CHUNK_SIZE = 1000; // Number of grants per chunk; adjust as needed

    // Process each unvisited charity as a starting point
    for (const [startId, startCharity] of Object.entries(Charity.charityLookup)) {
        if (visited.has(startId)) continue;

        // Initialize stack for DFS
        let stack = [{ charity: startCharity, grantIndex: 0, parentGrant: null }];

        while (stack.length > 0) {
            let grantCounter = 0;

            // Process up to CHUNK_SIZE grants
            while (stack.length > 0 && grantCounter < CHUNK_SIZE) {
                const top = stack[stack.length - 1];
                const { charity, grantIndex } = top;
                const grants = charity.grants || [];

                if (grantIndex < grants.length) {
                    const grant = grants[grantIndex];
                    const grantee = grant.grantee;
                    const granteeId = grantee.id;

                    top.grantIndex++; // Move to next grant in next iteration

                    if (onStack.has(granteeId)) {
                        // Cycle detected
                        cycleGrants.add(grant);
                        grant.isCircular = true;
                    } else if (!visited.has(granteeId)) {
                        // Explore new node
                        visited.add(granteeId);
                        onStack.add(granteeId);
                        stack.push({ charity: grantee, grantIndex: 0, parentGrant: grant });
                    }
                } else {
                    // Done with this charity’s grants
                    stack.pop();
                    onStack.delete(charity.id);
                }

                grantCounter++;
                processedGrants++;
            }

            // If more work remains, yield and update progress
            if (stack.length > 0) {
                updateStatus(
                    `Processing: ${Math.round((processedGrants / totalGrants) * 100)}% complete`
                );
                await new Promise(resolve => setTimeout(resolve, 0));
            }
        }
    }

    // Step 3: Final stats and cleanup
    Object.values(Charity.charityLookup).forEach(charity => {
        let hasBadGrants = false;
        charity.grants.forEach(grant => {
            if (cycleGrants.has(grant)) {
                hasBadGrants = true;
                badTotal += grant.amt; // Assumes grant.amt is the amount
            }
        });
        if (hasBadGrants) charitiesWithBadGrants++;
    });

    // Final update
    updateStatus(
        `$${formatSimpleNumber(badTotal)} of deep loopbacks removed, ${cycleGrants.size} in ${charitiesWithBadGrants} charities`
    );
    console.log(`${charitiesWithBadGrants} charities had circular grants`);
    console.log(`${cycleGrants.size} circular grants (including obvious)`);
    Object.values(Charity.charityLookup).forEach(c => c.organize()); // Assumes this method exists

    return cycleGrants;
}

    

    
    // charities with no grants in are "root"
    static getRootCharities()
    {
        return Object.values(Charity.charityLookup)
                .filter(c => c.isRoot && !c.govt_amt && !c.isTerminal)
                .filter(c => !Charity.shouldHide(c.id))
                .sort((a,b)=> (b.grantsTotal - a.grantsTotal));
    }
    
    // are the gramts downstrea visible?
    get hasVisibleGrants() {
        return this.visibleGrants.length > 0 && this.visibleGrants.some(g => g.isVisible);
    }    
    
    
    handleClick(e, count=-1) {
    if (e.altKey) {
        return this.tunnelNode(e);
    }
    if (e.metaKey || this.isTerminal) {
        console.log(`Hiding ${this.id} ${this.name}`);
        this.hide();
        return false;
    }
    if (this.expanded || e.shiftKey) {
        console.log(`Shrinking ${this.id} ${this.name}`);
        this.shrink(e);
        return false;
    }
    else {
        console.log(`Expanding ${this.id} ${this.name}`);
        this.expandDown(e, -1);
        this.expandUp(e, -1);
        return true;
    }
}    
       // grants that get clicked hide their path and their destination node
        handleGrantClick(event,g) {
                g.isVisible=false;
                g.grantee.recurseHide();
        }
        
        get origOut()
        {
                if (this.otherDown) return this._origOut;
                return this.grantsTotal;
        }
        get origIn()
        {
                if (this.otherUp) return this._origIn;
                return this.grantsInTotal;
        }
        
        buildDownstream()
        {
                if (this.otherDown)
                {
                        throw('duplicate Other');
                }
                if (!this.isTerminal)
                {
                        this._origOut = this.grantsTotal; // remememer for later
                        this.otherDown = new DownstreamOther({parent: this});
                }

        }

    expandDown(e,count) {
        if (!this.otherDown)
                this.buildDownstream();
        else
                this.otherDown.handleClick(e,count); 
    }
    
    buildUpstream()
    {
        if (this.otherUp){
        
                        throw('duplicate Other Up');

        }
        if (!this.isRoot)
        {
                this._origIn = this.grantsInTotal;
                this.otherUp = new UpstreamOther({parent: this});
        
        }

    }
    
    expandUp(e,count) {
        if (!this.otherUp)
                this.buildUpstream();
        else
                this.otherUp.handleClick(e,count); 
    
    }
    shrink(e) {
        let start = this.grants.length-NEXT_REVEAL;
        let end = start+NEXT_REVEAL;
        if (start < 0) start = 0;
        this.grants.slice(start, end).forEach( g =>{
                this.handleGrantClick(e,g);
        });
    
    }
    
    /*when we get turned off, we have to flow down to other nodes*/
    recurseHide() {
        if (this.isVisible)
        {
                this.isVisible=false;
                this.grants.forEach(g => {
                        g.isVisible=false;
                        g.grantee.recurseHide();
                });
        
        }
    
    }
    
    /* recurse upwards through nodes we're hiding to hide flows*/
    recurseUpHide() {
        if (this.isVisible)
        {
                this.isVisible=false;
                this.grantsIn.forEach(g => {
                        g.isVisible=false;
                        g.filer.recurseUpHide();
                });
       }
     
    }
    
    show() {
        this.isVisible=true;
        Charity.removeFromHideList(this.id);
    }
    
    hide() {
        this.isVisible=false;
        Charity.addToHideList(this.id);
    }
    
    /**
    
        So we want to be able to capture the state of the graph, so we need
        to report our node state as a string with our EIN.
    */
    URLPiece() {
        if (!this.isVisible || this.isOther || (!this.otherUp && !this.otherDown)) return null;
        return `${this.ein}:${this.grantsIn.length}:${this.grants.length}`;
    }
    
    /**
        we we match if any of the words in the search stricng match our EIN or our
        name
    */
     searchMatch(keywords) {
     
    const lowerStr = this.name.toLowerCase();
    return keywords
        .map(kw => kw.trim().toLowerCase())
        .filter(kw => kw !== '')
        .some(kw => lowerStr.includes(kw));
    }
    
    static matchKeys(keywords) {
        return Object.values(Charity.charityLookup).filter(c=> c.searchMatch(keywords));
    } 
    
    /* Given a list of URLs and a search string we compute the net URL*/
    static computeURLParams (keywords) {
        const params = new URLSearchParams();
        let visibleMap = {};
        Charity.visibleCharities().forEach( c => { // URL starts with EIN of everything visible
                const p =c.URLPiece();
                        if (p) {
                                visibleMap[c.ein]=p;
                        }
                },[]);
        
        // but we remove thing that come up vis search. 
        Charity.matchKeys(keywords).forEach(c => {
                if (c.isVisible) 
                        delete visibleMap[c.ein];
                });
        
        Charity.getHideList().forEach(ein => delete visibleMap[ein]);
        Object.values(visibleMap).forEach(e=> params.append('ein', e));
        Charity.getHideList().forEach(e=> params.append('nein', e));
        keywords.forEach(k=> params.append('keywords',k));
        return params;
    }
    
    /* given a URL and a search list, which nodes are visible */
    static matchURL(params= new URLSearchParams(window.location.search))
    {
    
        const URLList = params.getAll('ein');
        const keywords = params.getAll('keywords');
        Charity.setHideList(params.getAll('nein'));
        URLList.forEach(ein => {
                const id = ein.split(':')[0];
                if (!Charity.shouldHide(id) && ! Charity.getCharity(ein)?.isVisible)
                        Charity.placeNode(ein);
                
        });
        Charity.getHideList().forEach(ein=> {
                const c = Charity.getCharity(ein)
                if (c)
                {
                        c.isVisible=false;
        }       }
        
        );
        if (keywords.length)
        {
                Charity.invisibleCharities().forEach( c =>{
                        if (!Charity.shouldHide(c.id))
                        {
                                if (c.searchMatch(keywords))
                                        Charity.placeNode(c.ein);
                
                        }
        
                });
        }
    }
    
    tunnelNode() {
        //mark every other node and grant invisible
        Object.values(Charity.charityLookup).forEach( c => c.isVisible=false);
        Object.values(Grant.grantLookup).forEach( g => g.isVisible=false );
        // add just us to chart
        Charity.placeNode(this.id);
        return true;
    }
    
    place(upCount= -1, downCount=-1) {
        // show ourselves and generate the other notes
        this.expandDown({},downCount);
        this.expandUp({}, upCount);
        this.organize();
        this.expanded=true;
        this.isVisible=true;
        
        
    }
    
    /* show node and appropriate number of grants*/
    static placeNode( startEIN, simple=false) {
    
        const splits = startEIN.split(/:/);
        const c = Charity.getCharity(splits[0]);
        if (c) {
        
                if(!c.isVisible)
                        c.place(splits[1],splits[2]);
                
        }
        else
        {
        
                console.log(`Couldn't place ${startEIN}'`);

        }
        return c;
    
    }
    
    static cleanAfterRender(data)
    {
      //rendering screws up source and target, so reset them.
        data.links.forEach(link => {link.resetSourceTarget()});
 
    }
    
        static buildSankeyData() {
            const data = { nodes: [], links: [] };
            Charity.matchURL();
            data.links = Grant.visibleGrants(); // All visible grants
            data.nodes = Charity.visibleCharities(); // All visible nodes
            if (data.nodes.length > MAX_NODES)
            {
                updateStatus("Too Many Nodes, reducing");
                Charity.visibleCharities()
                .sort((a,b) =>
                        (b.grantTotal+b.grantInTotal)-
                        (a.grantTotal-b.grantInTotal)
                ).slice(MAX_NODES).forEach(c=> c.isVisible=false);
                data.nodes = Charity.visibleCharities();
                data.links = Grant.visibleGrants();
             }
    
             console.log(`Nodes: ${data.nodes.length}, Links: ${data.links.length}`);
            // Optional: Check for duplicate links
            const linkIds = new Set(data.links.map(link => `${link.source}~${link.target}`));
            if (linkIds.size !== data.links.length) {
                console.warn("Duplicate links detected!");
            }
            return data;
        }

}
class DownstreamOther extends Charity {
    constructor({parent, count=START_REVEAL}) {
        super({       
            ein: `${parent.ein}-Down`, 
            name: `More...`, 
            xml_name: `${parent.xml_name}-Down`, 
            isVisible: true,
            isOther: true,
            isOrganized: false,
            grantsIn: parent.grants.filter(g => !g.shouldHide() && !g.isOther).slice(count),
            grants: [] // Enforce no outflows
        });
 
        if (this.grantsIn.length == 0) {
            this.isVisible = false;
        }
        this.parent = parent;
        this.parent.otherDown = this;
        this.grantsIn.forEach(g => {
            g.isVisible = false;
            parent.removeGrant(g);
            g.stashDown(this.parent,this);
        });
        this.otherGrant = new Grant({
            filer_ein: this.parent.ein,
            grantee_ein: this.ein,
            amt: this.grantsInTotal,
            isOther: true,
            isOtherDest: this
        });               
        this.organize();
        this.otherGrant.isVisible = this.isVisible;
        this.parent.grants.forEach(g => g.isVisible = true);
        this.grantsIn = this.grantsIn.filter(g => g !== this.otherGrant);
    }

    handleClick(e, count) {
        let targetCount = NEXT_REVEAL;
        if (e.shiftKey) targetCount = NEXT_REVEAL_MAX;
        let candidates = this.grantsIn.filter(g => 
                {
            return (!g.shouldHide() && !g.isOther )
        });
            
        if (count > -1) {
            targetCount = candidates.length - count;
            if (targetCount < 1) targetCount = 1;
        }
        if (targetCount > candidates.length) targetCount = candidates.length;
        let revealGrants = candidates.slice(0, targetCount);
        
        revealGrants.forEach(g => {
            g.unstashDown(this.parent,this);
            g.grantee.isVisible = true;
            this.parent.addGrant(g);
            this.removeGrantIn(g);
        });       
        if (!this.grantsIn.length) {
            this.otherGrant.isVisible = false;
        }
    }

    handleGrantClick(g, event) {
        g.isVisible = false;
        g.grantee.recurseHide();
        this.addGrant(g);
        this.parent.removeGrant(g);        
    }
    
    get isVisible() {
        const visible = this.parent.isVisible && (this.grantsIn.length);
        return visible;
    }
    set isVisible(v) {
        super.isVisible = v;
    }
}

class UpstreamOther extends Charity {
    constructor({parent, count=START_REVEAL}) {
        super({       
            ein: `${parent.ein}-Up`, 
            name: `More...`, 
            xml_name: `${parent.xml_name}-Down`, 
            isVisible: true,
            isOther: true,
            isOrgnaized: false,
            grants: parent.grantsIn.filter(g => !g.shouldHide() && !g.isOther).slice(count),
            grantsIn: [] // Enforce no inflows
        });
         if (this.grants.length == 0) {
            this.isVisible = false;
        }
        this.parent = parent;
        this.parent.otherUp = this;
        this.grants.forEach(g => {
            g.isVisible = false;
            g.stashUp(this.parent,this);
            parent.removeGrantIn(g);
        });
        this.otherGrant = new Grant({
            filer_ein: this.ein,
            grantee_ein: this.parent.ein,
            filer: this,
            grantee: this.parent,
            amt: this.grantsTotal,
            isOther: true,
            isOtherDest: this
        });         
        this.organize();
        this.otherGrant.isVisible = true;
        this.parent.grantsIn.forEach(g => g.isVisible = true);
    }

handleClick(e, count) { 
    let targetCount = NEXT_REVEAL;
    if (e.shiftKey) targetCount = NEXT_REVEAL_MAX;
    let candidates = this.grants.filter(g => !g.shouldHide() && !g.isOther);
    if (count > -1) { 
        targetCount = candidates.length - count;
        if (count < 1) targetCount = 1;
    }
    if (targetCount > candidates.length) targetCount = candidates.length;
    let revealGrants = candidates.slice(0, targetCount);
    
    revealGrants.forEach(g => {
        g.filer.isVisible = true;
        g.unstashUp(this.parent, this);
        this.parent.addGrantIn(g);
        this.removeGrant(g);  // Corrected to remove from this.grants
    });       
    if (!this.grants.length) {  // Corrected to check this.grants
        this.otherGrant.isVisible = false;  // Optional, see note
    }
}

    handleGrantClick(g) {
        g.isVisible = false;
        g.filer.recurseUpHide();
        this.addGrantIn(g);
        this.parent.removeGrantIn(g);        
    }
    
    get isVisible() {
        const visible = this.parent.isVisible && (this.grants.length);
        return visible;
    }
    set isVisible(v) {
        super.isVisible = v;
    }
}

/**
        Class to hold a grant, aka a flow between two charities
*/
 class Grant {
    static grantLookup = {};

    static getGrant(id) {
        const g = Grant.grantLookup[id];
//        if (!g) {
//                console.log(`Couldn't find Grant ${id}`);
//        }
        return g;
    }

    static registerGrant(g) {
        Grant.grantLookup[g.id] = g;
    }
    static unregisterGrant(g) {
        delete Grant.grantLookup[g.id];
    }
    
    static visibleGrants() {
    // Extract just the IDs during flatMap
    const allGrantIds = Charity.visibleCharities()
        .filter(c => !c.isOther)
        .flatMap(c => [
        ...c.visibleGrants.map(g => g.id),
        ...c.visibleGrantsIn.map(g => g.id)
    ]);

    // Deduplicate the IDs
    const uniqueGrantIds = [...new Set(allGrantIds)];

    // Map back to full Grant objects
    return uniqueGrantIds.map(gid => Grant.getGrant(gid));
    }
    
    static checkGrantMatch(filer_ein, grantee_ein) {
        return filer_ein !== grantee_ein && 
               Charity.getCharity(filer_ein) && 
               Charity.getCharity(grantee_ein);
    }

    static loadGrantRow(row) {
        const filer = (row['filer_ein'] || '').trim();
        const grantee = (row['grant_ein'] || '').trim();
        let amt = parseInt((row['grant_amt'] || '0').trim(), 10);
        if (isNaN(amt)) amt = 0;
        if (Grant.checkGrantMatch(filer, grantee)) {
            const id = Grant.grantIDBuilder(filer, grantee);
            const g = Grant.getGrant(id);
            if (g) {
                g.addAmt(amt);
                return g;
            } else {
                return new Grant({
                    filer_ein: filer,
                    grantee_ein: grantee,
                    amt: amt
                });
            }
        } else if (filer !== grantee) {
            console.warn(`Missing charity for EIN: ${filer} or ${grantee}`);
        }
        return null;
    }
    
     shouldHide()
    {
        return (!Charity.shouldHide(this.ein) && (Charity.shouldHide(this.ein)))
    
    }
    
    resetSourceTarget()
    {
        this._source=null;
        this.sourceLinks=[];
        this._target=null;
        this.targetLinks=[];
    }
    /* next few accessors implement the sankey API*/
    get source() { // name alias for sankey
        return this._source || this.filer_ein;
    }
    
    set source(s)
    {
        this._source = s;
    }
    
    get target() {
        return this._target || this.grantee_ein; //name alias for sankey
    }
    
    set target(t) {
        this._target = t;
   }
    
    get value() {
        return scaleValue(this.amt);
    }

    addAmt(amt) {
        this.amt += amt;
    }

    get scaledAmt() {
        return scaleValue(this.amt);
    }
    
    static grantIDBuilder(filer_ein, grantee_ein)
    {
        return `${filer_ein}~${grantee_ein}`;
    }

    constructor({ filer_ein, grantee_ein, amt = 0, isCircular = false,
             isVisible = false, 
             isOther = false,
             isOtherDest
     }) {
        this.registered=false;
        this.amt = amt;
        this.filer_ein = filer_ein;
        this.grantee_ein = grantee_ein;
        this.filer = Charity.getCharity(filer_ein);
        this.grantee = Charity.getCharity(grantee_ein);
        Charity.addGrant(this);
        this.registered=true;
        this._isCircular = isCircular;
        this.isOther = isOther;
        this.isOtherDest = isOtherDest;
        this.isVisible = isVisible;
        this.buildId();
  }

    get relativeInAmount() {
        return this.amt / (this.filer.grantsTotal + 1);
    }

    get relativeAmount() {
        return this.amt / (this.grantee.grantsTotal + 1);
    }

    toString() {
        return `${this.id} ${formatNumber(this.amt)} (${this.value})`;
    }

    disorganize() {
        this.filer.isOrganized = false;
        this.grantee.isOrganized = false;
    }
    
    get isVisible() {
        if (this.isCircular) return false; // never show circular grants
        if (this.isOther)
                return this.isOtherDest.isVisible;
        return this.filer.isVisible && this.grantee.isVisible; // safest to compute this
    }

    set isVisible(v) {
        if (v != this._isVisible)
        {
                this._isVisible = v;
                // if we're visible, we have to have somewhere to draw from/to.
                  //this.filer.isVisible = v;
                  //this.grantee.isVisible = v;
                this.disorganize();
        
        }
    }

    set isCircular(value) {
        if (value !== this.isCircular && this.registered) { // avoid race condition
            Charity.circularGrant(this);
        }
        this._isCircular = value;
    }
    buildId()
    {
        this.filer_ein=this.filer.ein;
        this.grantee_ein=this.grantee.ein;
        this.id=Grant.grantIDBuilder(this.filer_ein,this.grantee_ein);
        Grant.registerGrant(this);
        return this.id;
    }
   // special methods for dealing with "other" nodes that have 
   // we have a grant a->b, but we are introducing a new node o
   // grants turns into a->o, so we store b in this.s
   // to reverse we set it back to a-> this.s
// In Grant class
stashDown(from, to) {
    Grant.unregisterGrant(this);
    this.stash = this.grantee;
    this.grantee = to;
    this.grantee_ein = to.ein; // Update grantee_ein
    this.buildId();
}

unstashDown(from, to) {
    this.grantee = this.stash;
    this.grantee_ein = this.stash.ein; // Update grantee_ein
    this.buildId();
}

stashUp(from, to) {
    Grant.unregisterGrant(this);
    this.stash = this.filer;
    this.filer = to;
    this.filer_ein = to.ein; // Update filer_ein
    this.buildId();
}

unstashUp(from, to) {
    this.filer = this.stash;
    this.filer_ein = this.stash.ein; // Update filer_ein
    this.buildId();
}
    
     tunnelGrant() {
       //mark every other node and grant invisible
        Object.values(Charity.charityLookup).forEach( c => c.isVisible=false);
        Object.values(Grant.grantLookup).forEach( g => g.isVisible=false );
        // add ourselves back, plus the nodes we point to
        this.isVisible=true;
        Charity.placeNode(this.filer_ein);
        Charity.placeNode(this.grantee_ein);
    
    }
        
}
function updateStatus(message, color='black')
{
        $('#status')
        .html(`<span class="flex items-center text-sm"><img src="/assets/images/loading.svg" class="size-6" alt="Loading...">${message}.</span>`);
}
 async function loadData() {
    updateStatus("...Loading Data...");

    const charitiesZipBuf = await fetch('../expose/charities.csv.zip').then(r => r.arrayBuffer());
    const charitiesZip = await JSZip.loadAsync(charitiesZipBuf);
    const charitiesCsvString = await charitiesZip.file('charities_truncated.csv').async('string');

    await new Promise((resolve, reject) => {
    updateStatus("...Parsing Charities...");
        Papa.parse(charitiesCsvString, {
            header: true,
            skipEmptyLines: true,
            complete: results => {
                results.data.forEach(row => {
                    Charity.buildCharityFromRow(row);
                });
                $('#status').html(`<span class="flex items-center text-sm"><img src="/assets/images/loading.svg" class="size-6" alt="Loading..."> Building Gov Master...${Charity.getCharityCount()}</span>`);
                Charity.buildGovCharity();
                resolve();
            },
            error: err => reject(err)
        });
    });

    const grantsZipBuf = await fetch('../expose/grants.csv.zip').then(r => r.arrayBuffer());
    const grantsZip = await JSZip.loadAsync(grantsZipBuf);
    const grantsCsvString = await grantsZip.file('grants_truncated.csv').async('string');
    let totalGrantsCount = 0;
    let totalGrantsRows = 0;

    await new Promise((resolve, reject) => {
    updateStatus("...Parsing Grants...");
        Papa.parse(grantsCsvString, {
            header: true,
            skipEmptyLines: true,
            complete: results => {
                results.data.forEach(row => {
                    totalGrantsRows++;
                    if (Grant.loadGrantRow(row)) {
                        totalGrantsCount++;
                    }
                });
                resolve();
            },
            error: err => reject(err)
        });
    });

    console.log(`Total Grants Rows ${totalGrantsRows}`);
    console.log(`Total Grants Loaded ${totalGrantsCount}`);
    console.log(`Grants Net ${Object.keys(Grant.grantLookup).length}`);

    return { 
        totalGrantsCount, 
    };
}

export {graphScaleUp, graphScaleDown, graphScaleReset, POWER_LAW, GOV_EIN, GOV_NODE, Charity, Grant, loadData, scaleValue, formatNumber };
