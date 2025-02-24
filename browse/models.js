export const POWER_LAW = 0.3;

 function scaleValue(amt) {
    return Math.pow(amt, POWER_LAW);
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
        grants = [], 
        grantsIn = [], 
        loopbackgrants = [], 
        loopforwards = [] 
    }) {
        this.id = ein;
        this.filer_ein = ein;
        this.name = name;
        this.xml_name = xml_name;
        this.receipt_amt = receipt_amt;
        this.govt_amt = govt_amt;
        this.contrib_amt = contrib_amt;
        this.grantTotal = 0;
        this.grantInTotal = 0;
        this.grants = grants;
        this.grantsIn = grantsIn;
        this.loopbackgrants = loopbackgrants;
        this.loopforwards = loopforwards;
        this.isVisible = isVisible;
        this.isOrganized = false;
        this.isGov = false;
        Charity.registerCharity(ein, this);
    }

    get logGrantTotal() {
        return scaleValue(this.grantTotal);
    }

    get logGrantsInTotal() {
        return scaleValue(this.grantInTotal);
    }

    get calcGrantsTotal() {
        return this.grantTotal = this.grants.reduce((total, g) => total + g.amt, 0);
    }

    get calcGrantsInTotal() {
        return this.grantInTotal = this.grantsIn.reduce((total, g) => total + g.amt, 0);
    }

    get visibleGrantsTotal() {
        return this.visibleGrants.reduce((total, g) => total + g.amt, 0);
    }

    get invisibleGrantsTotal() {
        return this.invisibleGrants.reduce((total, g) => total + g.amt, 0);
    }

    get getLoopbackTotal() {
        return this.loopbackgrants.reduce((total, g) => total + g.amt, 0);
    }

    get getLoopForwardTotal() {
        return this.loopforwards.reduce((total, g) => total + g.amt, 0);
    }

    get visibleGrants() {
        return this.grants.filter(g => g.isVisible);
    }

    get invisibleGrants() {
        return this.grants.filter(g => !g.isVisible);
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

    organize() {
        if (!this.isOrganized) {
            this.grants = this.grants.sort((a, b) => b.amt - a.amt);
            this.grantsIn = this.grantsIn.sort((a, b) => b.amt - a.amt);
            this.grantTotal = this.calcGrantsTotal;
            this.grantInTotal = this.calcGrantsInTotal;
            this.grantsVisibleTotal = this.visibleGrantsTotal;
            this.grantsInvisibleTotal = this.invisibleGrantsTotal;
            this.isOrganized = true;
        }
    }

    static circularGrant(g) {
        g.grantee.circleGrant(g);
        g.filer.circleGrant(g);
    }

    circleGrant(g) {
        this.removeGrant(g);
        this.loopbackgrants.push(g);
        this.isOrganized = false;
    }

    get grantsTotalString() { 
        return formatNumber(this.grantTotal);
    }

    get invisibleTotalString() { 
        return formatNumber(this.invisibleGrantsTotal);
    }

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

    static buildGovCharity() {
        const gov_ein = "001";
        const gov_proto = {
            ein: gov_ein,
            filer_ein: gov_ein,
            name: "US Government",
            xml_name: "The Beast",
            contrib_amt: 4.6e12,
        };
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

        const govChar = new Charity(gov_proto);
        console.log(`${govGrants} Implied Government Grants Generated`);
        console.log(`Gov Total: ${formatNumber(govTotal)}`);
        console.log(`USG grants count: ${govChar.grants.length}, sample:`, 
            govChar.grants.slice(0, 5).map(g => ({ 
                target: g.grantee_ein, 
                amt: formatNumber(g.amt) 
            })));
        return govChar;
    }

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
                    grant.isCircular = true;
                    hasBadGrants = true;
                }
            });
            if (hasBadGrants) {
                charitiesWithBadGrants++;
            }
        }
        console.log(`${charitiesWithBadGrants} charities had bad grants`);
        console.log(`${cycleGrants.size} bad grants`);
        return cycleGrants;
    }
}

 class Grant {
    static grantLookup = {};

    static getGrant(id) {
        return Grant.grantLookup[id];
    }

    static registerGrant(g) {
        Grant.grantLookup[g.id] = g;
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
        this.id = `${filer_ein}~${grantee_ein}`;
        this.isCircular = isCircular;
        this.amt = amt;
        this.filer_ein = filer_ein;
        this.grantee_ein = grantee_ein;
        this.filer = Charity.getCharity(filer_ein);
        this.grantee = Charity.getCharity(grantee_ein);
        this.isVisible = isVisible;
        Grant.registerGrant(this);
        Charity.addGrant(this);
    }

    get relativeInAmount() {
        return this.amt / (this.filer.grantTotal + 1);
    }

    get relativeAmount() {
        return this.amt / (this.grantee.grantTotal + 1);
    }

    toString() {
        return `${this.id} ${formatNumber(this.amt)} (${this.scaledAmt})`;
    }

    disorganize() {
        this.filer.isOrganized = false;
        this.grantee.isOrganized = false;
    }

    set isVisible(v) {
        this._isVisible = v;
        this.disorganize();
    }

    set isCircular(value) {
        if (value !== this.isCircular) {
            Charity.circularGrant(this);
        }
        this.isCircular = value;
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
        g.filer.loopbackgrants.push(g);
        g.grantee.loopbackgrants.push(g);
    });

    return { 
        totalGrantsCount, 
        badGrantsCount: badGrants.size, 
        badCharsCount: badCharsCounter.size 
    };
}

export {POWER_LAW, Charity, Grant, loadData, scaleValue, formatNumber };
