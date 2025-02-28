 let POWER_LAW = 3;
 
 const START_REVEAL = 5;
 const MIN_REVEAL = 2;
 const NEXT_REVEAL = 3;
 
 function scaleDown()
 {
        POWER_LAW++;
                
 }
 
 function scaleUp()
 {
        POWER_LAW--;
        if (POWER_LAW < 1) POWER_LAW=1;
                
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

    static getCharity(ein) {
        return Charity.charityLookup[ein];
    }

    static registerCharity(ein, c) {
        Charity.charityLookup[ein] = c;
    }
    static visibleCharities() {
        return Object.values(Charity.charityLookup).filter(g => g.isVisible);
    }

    static getCharityCount() {
        return Object.keys(Charity.charityLookup).length;
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
        otherdown=null
    }) {
        this.id = ein;
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
        this.isOrganized = false;
        this.isGov = false;
        this.isOther = isOther;
        this.expanded = false;
        this._valueCache={};
        Charity.registerCharity(ein, this);
    }
    
    get isVisible() {
        return this._isVisible;
    
    }
    
    set isVisible(value) {
        if (this._isVisible != value)
        {
                this._isVisible = value;
                this.isOrganized = false; // force some recalck
        }
    }

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

    get grantsTotal() {
        const cacheKey = `grantsTotal-${POWER_LAW}`;
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
        return this._valueCache[cacheKey]=  this.grants.filter(g => g.isVisible);
    }

    get invisibleGrants() {
        const cacheKey = `invisibleGrants`;
        if (this._valueCache[cacheKey])
                return this._valueCache[cacheKey];
        return this._valueCache[cacheKey]=   this.grants.filter(g => !g.isVisible);
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
        const gov_ein = "001";
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

        console.log(`${govGrants} Implied Government Grants Generated`);
        console.log(`Gov Total: ${formatNumber(govTotal)}`);
        console.log(`USG grants count: ${govChar.grants.length}, sample:`, 
            govChar.grants.slice(0, 5).map(g => ({ 
                target: g.grantee_ein, 
                amt: formatNumber(g.amt) 
            })));
        return govChar;
    }

    /*
      for now, we simply hide any grant that circles back into a charities upstream
    */
    static findCircularGrants() {
        const visited = new Set();
        const onStack = new Set();
        const cycleGrants = new Set();
        const stack = [];

        for (const [startId, startCharity] of Object.entries(Charity.charityLookup)) {
            if (visited.has(startId)) continue;

            stack.push([
                startCharity,
                (startCharity.grants || [])[Symbol.iterator](),
                null
            ]);

            while (stack.length > 0) {
                let [charity, grantIter, parentGrant] = stack[stack.length - 1];
                const charityId = charity.id;

                if (!visited.has(charityId)) {
                    visited.add(charityId);
                    onStack.add(charityId);
                }

                const iterResult = grantIter.next();
                if (!iterResult.done) {
                    const grant = iterResult.value;
                    const grantee = grant.grantee;
                    const granteeId = grantee.id;

                    if (onStack.has(granteeId)) {
                        cycleGrants.add(grant);
                    } else if (!visited.has(granteeId)) {
                        stack.push([
                            grantee,
                            (grantee.grants || [])[Symbol.iterator](),
                            grant
                        ]);
                    }
                } else {
                    stack.pop();
                    onStack.delete(charityId);
                }
            }
        }

        let charitiesWithBadGrants = 0;
        for (const [charityId, charity] of Object.entries(Charity.charityLookup)) {
            let hasBadGrants = false;
            charity.grants.forEach(grant => {
                if (cycleGrants.has(grant)) {
                    grant.isCircular = true; // this triggers Charity.cycleGrant as a side effect
                    hasBadGrants = true;
                }
            });
            if (hasBadGrants) {
                charitiesWithBadGrants++;
            }
        }
        console.log(`${charitiesWithBadGrants} charities had circular grants`);
        console.log(`${cycleGrants.size} circular grants`);
        Object.values(Charity.charityLookup).forEach(c => c.organize); // start out organized
        return cycleGrants;
    }
    
    // charities with no grants in are "root"
    static getRootCharities()
    {
        return Object.values(Charity.charityLookup).filter(c => !c.grantsIn.length && !c.govt_amt && c.grants.length);
    }
    
    // are the gramts downstrea visible?
    get hasVisibleGrants() {
        return this.visibleGrants.length > 0 && this.visibleGrants.some(g => g.isVisible);
    }    
    
    
    handleClick(e) {
    
        if (e.altKey)
        {
                if (e.shiftKey) 
                {
                        this.shrink(e);
                }
                else
                {
                        this.expandUp(e);
                }
        
        }
        else {
                if (!this.hasVisibleGrants())
                {
                        this.expandDown(e);
        
                }
                else
                {
                        this.shrink(e);
                }       
        
        }
     
    }
    
    expandDown(e) {
        if (!this.otherDown)
                this.otherDown = new DownstreamOther({parent: this});
        else
                this.otherDown.handleClick(e); 
    }
    
    expandUp(e) {
        if (!this.otherUp)
                this.otherUp = new UpstreamOther({parent: this});
        else
                this.otherUp.handleClick(e); 
    
    }
    shrink(e) {
        let start = this.grants.length-NEXT_REVEAL;
        let end = start+NEXT_REVEAL;
        if (start < 0) start = 0;
        this.grants.slice(start, end).forEach( g =>{
                this.handleGrantClick(g,e);
        });
    
    }
    
    /*when we get turned off, we have to flow down to other nodes*/
    recurseHide() {
        this.isVisible=false;
        this.grants.forEach(g => {
                g.isVisible=false;
                g.grantee.recurseHide();
        });
    
    }
    
    /* recurse upwards through nodes we're hiding to hide flows*/
    recurseUpHide() {
        this.isVisible=false;
        this.grantsIn.forEach(g => {
                g.isVisible=false;
                g.filer.recurseUpHide();
        });
    
    }
    
    /**
    
        So we want to be able to capture the state of the graph, so we need
        to report our node state as a string with our EIN.
    */
    URLPiece() {
        if (!this.isVisible()) return null;
        return `{this.ein}:${this.grantsIn.length}:${this.grants.length}`;
    }
    
    /**
        we we match if any of the words in the search stricng match our EIN or our
        name
    */
     searchMatch(s) {
        const words = s.split(/\s+/);
        return words.some(w => this.name.includes(w) || this.ein.includes(w));
    }
    
    /* Given a list of URLs and a search string we compute the net URL*/
    static computeURL(URLList, search) {
        let visibleMap = {};
        pieces=Object.values(Charity.charityLookup).reduce((total,c) => {
                const p =c.URLPiece();
                        if (p) {
                                visibleMap[c.ein]=p;
                        }
                },[]);
        
        URLList.forEach(ein => visibleMap.remove(ein));
        Object.values(visibleMap).forEach(c => {
                if (c.searchMatch(s))
                        delete visibleMap[ein];
        });
        URL = [];
        if (search) URL.push(`search=${search}`);
        EINList = Object.values(visibleMap).forEach(e => URL.push(`ein={e.ein}`));
        return URL.join('&');
    }
    
    /* given a URL and a search list, which nodes are visible */
    static matchURL(URLList, search){
        URLList.forEach(ein => {
                const c = Charity.getCharity(ein);
                
        })
    
    }
    
    static placeNode( startEin) {
    
        const c = Charity.getCharity(startEin);
        if (c) {
        
                c.isVisible=true;
                
        }
        else
        {
        
                        system.log(`Couldn't place ${startEin}'`);

        }
    
    }
    
    
}

/**
        This object holds the downstream grants taht we don't want to show. 
        Clicking on it shows NEXT_REVEAL more grants and moves them back to the parent
        for display
*/
class DownstreamOther extends Charity
{
        constructor({parent, count=START_REVEAL})
        {
                super({       
                        ein: `${parent.ein}-Down`, 
                        name: `${parent.name}-Downstream`, 
                        xml_name: `${parent.xml_name}-Down`, 
                        isVisible : true,
                        isOther:true,
                        grants : parent.grants.slice(count)
                } );
                this.parent = parent;
                this.parent.otherDown = this;
                this.grants.slice(count).forEach( g => {
                        g.isVisiblbe=false;
                        parent.removeGrant(g);
                        g.filer=this;
                });
        
        }
        handleClick() { // every time we get clicked on, 3 more get shown
                this.grants.slice(0,NEXT_REVEAL).forEach( g=> {
                        g.isVisible=true;
                        g.grantee.isVisible=true;
                        this.parent.addGrant(g);
                        this.removeGrant(g);
                        g.filer=this.parent;
                
                })       
        }
        // grants that get clicked hide their path and their node
        handleGrantClick(g, event) {
                g.isVisible=false;
                g.grantee.recurseHide();
                this.addGrant(g);
                this.parent.removeGrant(g);        
        }
        
        

}

/**
        This object holds the upstream grants taht we don't want to show. 
        Clicking on it shows NEXT_REVEAL more grants and moves them back to the parent
        for display
*/

class UpstreamOther extends Charity
{
        constructor({parent, count=START_REVEAL})
        {
                super({       
                        ein: `${parent.ein}-Up`, 
                        name: `${parent.name}-Up`, 
                        xml_name: `${parent.xml_name}-Down`, 
                        isVisible : true,
                        isOther:true,
                        grantsIn : parent.grantsIn.slice(count)
                } );
                this.parent = parent;
                this.parent.otherUp = this;
                this.grants.slice(count).forEach( g => {
                        g.isVisiblbe=false;
                        parent.removeGrantIn(g);
                        g.grantee=this;
                });
        
        }
        handleClick() { // every time we get clicked on, 3 more get shown
                this.grants.slice(0,NEXT_REVEAL).forEach( g=> {
                        g.isVisible=true;
                        g.filer.isVisible=true;
                        this.parent.addGrantIn(g);
                        this.removeGrantIn(g);
                        g.grantee=this.parent;
                
                })       
        }
        // grants that get clicked hide their path and their node
        handleGrantClick(g) {
                g.isVisible=false;
                g.filer.recurseUpHide();
                this.addGrantIn(g);
                this.parent.removeGrantIn(g);        
        }
        
        

}

/**
        Class to hold a grant, aka a flow between two charities
*/
 class Grant {
    static grantLookup = {};

    static getGrant(id) {
        return Grant.grantLookup[id];
    }

    static registerGrant(g) {
        Grant.grantLookup[g.id] = g;
    }
    
    static visibleGrants() {
        return Object.values(Grant.grantLookup).filter(g => g.isVisible);
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
            const id = `${filer}~${grantee}`;
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

    addAmt(amt) {
        this.amt += amt;
    }

    get scaledAmt() {
        return scaleValue(this.amt);
    }

    constructor({ filer_ein, grantee_ein, amt = 0, isCircular = false, isVisible = false }) {
        this.registered=false;
        this.id = `${filer_ein}~${grantee_ein}`;
        this.amt = amt;
        this.filer_ein = filer_ein;
        this.grantee_ein = grantee_ein;
        this.filer = Charity.getCharity(filer_ein);
        this.grantee = Charity.getCharity(grantee_ein);
        Grant.registerGrant(this);
        Charity.addGrant(this);
        this.registered=true;
        this.isVisible = isVisible;
        this._isCircular = isCircular;
   }

    get relativeInAmount() {
        return this.amt / (this.filer.grantsTotal + 1);
    }

    get relativeAmount() {
        return this.amt / (this.grantee.grantsTotal + 1);
    }

    toString() {
        return `${this.id} ${formatNumber(this.amt)} (${this.scaledAmt})`;
    }

    disorganize() {
        this.filer.isOrganized = false;
        this.grantee.isOrganized = false;
    }
    
    get isVisible() {
        return this._isVisible;
    }

    set isVisible(v) {
        this._isVisible = v;
        this.disorganize();
    }

    set isCircular(value) {
        if (value !== this.isCircular && this.registered) { // avoid race condition
            Charity.circularGrant(this);
        }
        this._isCircular = value;
    }
    
    /** the links in the sankey have to have a specific interface */
    buildSankeyLink() {
    
        return {
             source: this.filer,
             target: this.grantee,
             value: scaleValue(this.amt),
             rawValue: this.amt || 0,
             filer: this.filer,
             grantee: this.grantee,
             grant: this
         };
    }
    
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
                    Charity.buildCharityFromRow(row);
                });
                $('#status').html('<span class="flex items-center text-sm"><img src="/assets/images/loading.svg" class="size-6" alt="Loading..."> Building Gov Master...</span>');
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

    let badCharsCounter = new Set();
    let badGrants = Charity.findCircularGrants();
    badGrants.forEach(g => {
        badCharsCounter.add(g.grantee.id);
    });

    return { 
        totalGrantsCount, 
        badGrantsCount: badGrants.size, 
        badCharsCount: badCharsCounter.size 
    };
}

export {scaleUp, scaleDown, POWER_LAW, Charity, Grant, loadData, scaleValue, formatNumber };
