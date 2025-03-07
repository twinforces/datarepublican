let POWER_LAW = 3;
const TOP_N_INITIAL = 5;

const START_REVEAL = 5;
const MIN_REVEAL = 2;
const NEXT_REVEAL = 3;
const NEXT_REVEAL_MAX = 15;
const GOV_EIN = "001";
const MAX_NODES = 100;
const CHUNK_SIZE = 1000; // Number of grants per chunk; adjust as needed
let GOV_NODE = null; // for easier debugging

function scaleValue(amt) {
  return Math.pow(amt, 1 / POWER_LAW); // 2rd root, 4th root, 5tth root, etc.
}

function formatNumber(num) {
  if (num >= 1e12) return (num / 1e12).toFixed(1) + "T";
  if (num >= 1e9) return (num / 1e9).toFixed(1) + "B";
  if (num >= 1e6) return (num / 1e6).toFixed(1) + "M";
  if (num >= 1e3) return (num / 1e3).toFixed(1) + "K";
  return num == null ? "N/A" : num.toString();
}

/**
 *
 * I'm an MVVM believer, time to hoist stuff into a View Model
 * All these static methods are an anti-pattern.
 * */

/**
 * There can be only 1
 */
let viewModel = null;
class BrowseViewModel {
  constructor({ POWER_LAW = 3, GOV_EIN = "001" } = {}) {
    this.POWER_LAW = POWER_LAW;
    this.POWER_LAW_RESET = this.POWER_LAW;
    this.GOV_EIN = GOV_EIN;
    this.GOV_NODE = null; // we build this later.
    this.hideList = {};
    this.showList = {};
    this.breadCrumbs = [];
    this.keywords = {};
    this.renderData = null;
    this.dataReady = false;
    viewModel = this;
  }

  graphScaleDown() {
    this.POWER_LAW++;
  }

  graphScaleUp() {
    this.POWER_LAW--;
    if (this.POWER_LAW < 1) this.POWER_LAW = 1;
  }

  graphScaleReset() {
    this.POWER_LAW = this.POWER_LAW_RESET;
  }

  addToShowList(ein) {
    const c = Charity.getCharity(ein);
    const visible = c?.isVisible;
    this.showList[c?.ein] = 1;
    if (c && !visible) c.show();
  }

  removeFromShowList(ein) {
    const visible = this.showList[ein];
    delete this.showList[ein];
    if (visible) Charity.getCharity(ein)?.show();
  }

  getShowList() {
    return Object.keys(this.showList).sort();
  }
  setShowList(list) {
    this.showList = {};
    list.forEach((ein) => this.addToShowList(ein));
    return this.getShowList();
  }

  clearShowList() {
    this.showList = {};
  }
  clearHideList() {
    this.showList = {};
  }

  addToHideList(ein) {
    const c = Charity.getCharity(ein);
    const visible = c.isVisible;
    this.hideList[ein] = 1;
    if (visible) c.hide();
  }

  removeFromHideList(ein) {
    const visible = Charity.hideList[ein];
    delete Charity.hideList[ein];
    if (visible) Charity.getCharity(ein).show();
  }
  shouldHide(ein) {
    return this.hideList[ein];
  }
  getHideList() {
    return Object.keys(Charity.hideList).sort();
  }
  setHideList(list) {
    this.hideList = {};
    list.forEach((ein) => this.addToHideList(ein));
    return this.getHideList();
  }

  clearHideList() {
    this.showList = {};
  }

  addToBreadCrumbs(crumb) {
    this.breadCrumbs.push(crumb);
  }

  removeFromBreadCrumbs(crumb) {
    this.breadCrumbs = this.breadCrumbs.filter((c) => c != crumb);
  }

  getBreadCrumbs() {
    return this.breadCrumbs;
  }
  setBreadCrumbs(list) {
    this.breadCrumbs = list;
  }

  /**
      the Government is implied by the data  
    */
  async buildGovCharity() {
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
    let processList = Object.values(Charity.charityLookup)
      .filter((c) => c.govt_amt)
      .sort((a, b) => b.govt_amt - a.govt_amt);
    const govTotalAmount = 0;
    const govCount = processList.length;
    let govTotal = 0;
    const totalGrants = processList.reduce(
      (sum, charity) => sum + charity.govt_amt,
      0
    );

    let chunk = processList.slice(0, CHUNK_SIZE);
    processList = processList.slice(CHUNK_SIZE);
    while (chunk.length) {
      chunk.forEach((c) => {
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
      updateStatus(
        `Gov Processing: ${Math.round(
          (govGrants / govCount) * 100
        )}%  $${Math.round(
          (govTotal / totalGrants) * 100
        )}% ${govGrants}/${govCount} ${formatNumber(govTotal)}/${formatNumber(
          totalGrants
        )} complete`
      );
      await new Promise((resolve) => setTimeout(resolve, 0));
      chunk = processList.slice(0, CHUNK_SIZE);
      processList = processList.slice(CHUNK_SIZE);
    }

    updateStatus(
      `...Gov Charity complete, ${
        govChar.grants.length
      } generated, ${formatNumber(govTotal)}`
    );
    govChar.isGov = true;
    console.log(`${govGrants} Implied Government Grants Generated`);
    console.log(`Gov Total: ${formatNumber(govTotal)}`);
    console.log(
      `USG grants count: ${govChar.grants.length}, sample:`,
      govChar.grants.slice(0, 5).map((g) => ({
        target: g.grantee_ein,
        amt: formatNumber(g.amt),
      }))
    );
    this.GOV_NODE = govChar;
    return govChar;
  }
  /*
      for now, we simply hide any grant that circles back into a charities upstream
    */
  async findCircularGrants() {
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
    const charitiesTotal = Object.values(Charity.charityLookup).length;
    let charitiesProcessed = 0;

    updateStatus("Marking Circular Grants, step 1, A->B->A");
    await new Promise((resolve) => setTimeout(resolve, 0));
    // Step 1: Handle obvious circular grants (fast pre-check)
    for (let charity of Object.values(Charity.charityLookup)) {
      const obviousCircles = charity.simpleCircular(); // Assumes this method exists
      obviousCirclesCount += obviousCircles.length;
      if (obviousCircles.length) charitiesWithBadGrants++;
      if (!(charitiesProcessed++ % CHUNK_SIZE)) {
        updateStatus(
          `${Math.round(
            (charitiesProcessed / charitiesTotal) * 100
          )}% charities scanned for obvious loopbacks`
        );
        await new Promise((resolve) => setTimeout(resolve, 0));
      }
      obviousCircles.forEach((grant) => {
        grant.isCircular = true;
        cycleGrants.add(grant);
      });
    }
    console.log(`${obviousCirclesCount} obvious circular grants found`);
    updateStatus(`${obviousCirclesCount} obvious circular grants found`);
    await new Promise((resolve) => setTimeout(resolve, 0));

    // Step 2: Deep cycle detection with chunking
    updateStatus("...Finding Deeper Loopback Grants... A->B->C->A");
    await new Promise((resolve) => setTimeout(resolve, 0));

    // Calculate total grants for progress tracking
    const totalGrants = Object.values(Charity.charityLookup).reduce(
      (sum, charity) => sum + charity.grants.length,
      0
    );
    let processedGrants = 0;

    // Process each unvisited charity as a starting point
    for (const [startId, startCharity] of Object.entries(
      Charity.charityLookup
    )) {
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
              stack.push({
                charity: grantee,
                grantIndex: 0,
                parentGrant: grant,
              });
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
            `Circular Processing: ${Math.round(
              (processedGrants / totalGrants) * 100
            )}% complete`
          );
          await new Promise((resolve) => setTimeout(resolve, 0));
        }
      }
    }

    // Step 3: Final stats and cleanup
    Object.values(Charity.charityLookup).forEach((charity) => {
      let hasBadGrants = false;
      charity.grants.forEach((grant) => {
        if (cycleGrants.has(grant)) {
          hasBadGrants = true;
          badTotal += grant.amt; // Assumes grant.amt is the amount
        }
      });
      if (hasBadGrants) charitiesWithBadGrants++;
    });

    // Final update
    updateStatus(
      `$${formatSimpleNumber(badTotal)} of deep loopbacks removed, ${
        cycleGrants.size
      } in ${charitiesWithBadGrants} charities`
    );
    console.log(`${charitiesWithBadGrants} charities had circular grants`);
    console.log(`${cycleGrants.size} circular grants (including obvious)`);
    Object.values(Charity.charityLookup).forEach((c) => c.organize()); // Assumes this method exists

    return cycleGrants;
  }

  getRootCharities() {
    return Object.values(Charity.charityLookup)
      .filter((c) => c.isRoot && !c.govt_amt && !c.isTerminal)
      .filter((c) => !Charity.shouldHide(c.id))
      .sort((a, b) => b.grantsTotal - a.grantsTotal);
  }

  addBreadCrumb(bc) {
    this.breadCrumbs.push(bc);
  }

  addToKeywords(word) {
    this.keywords[word.toLowerCase()] = 1;
  }
  removeFromKeywords(word) {
    delete this.keywords[word.toLowerCase()];
  }
  clearKeywordList() {
    this.keywords = {};
  }
  getKeywordList() {
    return Object.keys(this.keywords).sort();
  }
  setKeywordList(list) {
    Object.fromEntries(list.map((key) => [key, true]));
  }

  matchKeys() {
    return Object.values(Charity.charityLookup).filter((c) =>
      c.searchMatch(Object.keys(this.keywords))
    );
  }

  computeURLParams() {
    const params = new URLSearchParams();
    let visibleMap = {};
    Charity.visibleCharities().forEach((c) => {
      // URL starts with EIN of everything visible
      const p = c.URLPiece();
      if (p) {
        visibleMap[c.ein] = p;
      }
    }, []);

    // but we remove thing that come up vis search.
    Charity.matchKeys(keywords).forEach((c) => {
      if (c.isVisible) delete visibleMap[c.ein];
    });

    this.getHideList().forEach((ein) => delete visibleMap[ein]);
    Object.values(visibleMap).forEach((e) => params.append("ein", e));
    this.getHideList().forEach((e) => params.append("nein", e));
    this.getKeywordList().forEach((k) => params.append("keywords", k));
    return params;
  }
  parseQueryParams(params = new URLSearchParams(window.location.search)) {
    this.setShowList(params.getAll("ein"));
    this.setHideList(params.getAll("nein"));
    this.setBreadCrumbs(params.getAll("breadCrumbs"));
    this.setKeywordList(params.getAll("keywords"));
  }

  processBreadCrumbs() {
    if (Charity.visibleCharities().length == 0) {
      this.loadDefaultData();
    }
  }
  /* given a URL and a search list, which nodes are visible */
  matchURL(params = new URLSearchParams(window.location.search)) {
    this.parseQueryParams(params);
    Object.values(Charity.charityLookup).forEach((c) => (c.isVisible = false)); //start with everything hidden
    this.getShowList().forEach((ein) => {
      const id = ein.split(":")[0];
      if (!this.shouldHide(id) && !Charity.getCharity(ein)?.isVisible)
        Charity.placeNode(ein);
    });
    this.getHideList().forEach((ein) => {
      const c = Charity.getCharity(ein);
      if (c) {
        c.isVisible = false;
      }
    });
    if (this.getKeywordList().length) {
      Charity.invisibleCharities().forEach((c) => {
        if (!Charity.shouldHide(c.id)) {
          if (c.searchMatch(keywords)) Charity.placeNode(c.ein);
        }
      });
    }
    this.processBreadCrumbs();
    return Charity.visibleCharities().length;
  }

  cleanAfterRender() {
    //rendering screws up source and target, so reset them.
    this.renderData.links.forEach((link) => {
      link.resetSourceTarget();
    });
  }
  buildSankeyData() {
    this.renderData = { nodes: [], links: [] };
    this.matchURL();
    this.renderData.links = Grant.visibleGrants().filter(
      (g) => g && !g.isOther && g.isVisible
    ); // All visible grants
    this.renderData.nodes = Charity.visibleCharities(); // All visible nodes
    /*data.nodes.forEach(node => {
                node.buildUpstream();
                node.buildDownstream();
            
            });*/
    if (this.renderData.nodes.length > MAX_NODES) {
      updateStatus("Too Many Nodes, reducing");
      Charity.visibleCharities()
        .sort(
          (a, b) =>
            b.grantTotal + b.grantInTotal - (a.grantTotal - b.grantInTotal)
        )
        .slice(MAX_NODES)
        .forEach((c) => (c.isVisible = false));
      this.renderData.nodes = Charity.visibleCharities();
      this.renderData.links = Grant.visibleGrants();
    }

    console.log(
      `Nodes: ${this.renderData.nodes.length}, Links: ${this.renderData.links.length}`
    );
    // Optional: Check for duplicate links
    const linkIds = new Set(
      this.renderData.links.map((link) => `${link.source}~${link.target}`)
    );
    if (linkIds.size !== this.renderData.links.length) {
      console.warn("Duplicate links detected!");
    }
    return this.renderData;
  }

  async loadData() {
    updateStatus("...Loading Data...");
    this.dataReady = false;
    const charitiesZipBuf = await fetch("../expose/charities.csv.zip").then(
      (r) => r.arrayBuffer()
    );
    const charitiesZip = await JSZip.loadAsync(charitiesZipBuf);
    const charitiesCsvString = await charitiesZip
      .file("charities_truncated.csv")
      .async("string");

    await new Promise((resolve, reject) => {
      updateStatus("...Parsing Charities...");
      Papa.parse(charitiesCsvString, {
        header: true,
        skipEmptyLines: true,
        complete: (results) => {
          let counter = 0;
          results.data.forEach((row) => {
            Charity.buildCharityFromRow(row);
            counter++;
            if (!(counter % CHUNK_SIZE))
              updateStatus(` Building NGO List ${counter}`);
          });
          resolve();
        },
        error: (err) => reject(err),
      });
    });

    await this.buildGovCharity();
    const grantsZipBuf = await fetch("../expose/grants.csv.zip").then((r) =>
      r.arrayBuffer()
    );
    const grantsZip = await JSZip.loadAsync(grantsZipBuf);
    const grantsCsvString = await grantsZip
      .file("grants_truncated.csv")
      .async("string");
    let totalGrantsCount = 0;
    let totalGrantsRows = 0;

    await new Promise((resolve, reject) => {
      updateStatus("...Parsing Grants...");
      Papa.parse(grantsCsvString, {
        header: true,
        skipEmptyLines: true,
        complete: (results) => {
          results.data.forEach((row) => {
            totalGrantsRows++;
            if (Grant.loadGrantRow(row)) {
              totalGrantsCount++;
            }
            if (!(totalGrantsRows % CHUNK_SIZE))
              updateStatus(` Building Grant List ${totalGrantsCount}`);
          });
          resolve();
        },
        error: (err) => reject(err),
      });
    });
    updateStatus("Grants Loaded, marking loopbacks");
    await this.findCircularGrants();
    console.log(`Total Grants Rows ${totalGrantsRows}`);
    console.log(`Total Grants Loaded ${totalGrantsCount}`);
    console.log(`Grants Net ${Object.keys(Grant.grantLookup).length}`);
    updateStatus("USG & NGOs & Grants Loaded", "black", false);
    this.dataReady = true;
    return {
      totalGrantsCount,
    };
  }

  loadDefaultData() {
    const usGov = this.GOV_NODE;
    updateStatus("placing US Government");
    usGov.place();
    updateStatus(`USG placed adding top ${TOP_N_INITIAL} roots`);
    this.getRootCharities()
      .slice(0, TOP_N_INITIAL)
      .forEach((c) => {
        c.place();
      });
    this.customGraphEdges = Charity.visibleCharities();
  }
}

viewModel = new BrowseViewModel();
/**
 * Class to hold NGO data, aka "nodes"
 */
class Charity {
  static charityLookup = {};
  static hideList = {};

  static getCharity(ein) {
    const parts = ein.split(":");
    return Charity.charityLookup[parts[0]];
  }

  static registerCharity(ein, c) {
    Charity.charityLookup[ein] = c;
  }
  static visibleCharities() {
    return Object.values(Charity.charityLookup).filter((g) => g.isVisible);
  }
  static invisibleCharities() {
    return Object.values(Charity.charityLookup).filter((g) => !g.isVisible);
  }

  static getCharityCount() {
    return Object.keys(Charity.charityLookup).length;
  }

  static shouldHide(ein) {
    return Charity.hideList[ein];
  }

  static buildCharityFromRow(row) {
    const ein = (row["filer_ein"] || "").trim();
    if (!ein) return;
    let rAmt = parseInt((row["receipt_amt"] || "0").trim(), 10);
    if (isNaN(rAmt)) rAmt = 0;
    return new Charity({
      ein: ein,
      name: (row["filer_name"] || "").trim(),
      xml_name: row["xml_name"],
      receipt_amt: rAmt,
      govt_amt: parseInt((row["govt_amt"] || "0").trim(), 10) || 0,
      contrib_amt: parseInt((row["contrib_amt"] || "0").trim(), 10) || 0,
    });
  }

  constructor({
    ein,
    name,
    xml_name,
    govt_amt = 0,
    contrib_amt = 0,
    receipt_amt = 0,
    isVisible = false,
    isOther = false,
    grants = [],
    grantsIn = [],
    loopbackgrants = [],
    loopforwards = [],
    otherUp = null,
    isOrganized = false,
    otherdown = null,
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
    this._valueCache = {};
    this.sourceLinks = []; //work around for sankey issue;
    this.targetLinks = []; //work around for sankey issue;
    Charity.registerCharity(ein, this);
  }

  get longEIN() {
    const matches = this.ein.match(/(\d\d)-*(\d\d\d\d\d\d)/);
    return `${matches[0]}${matches[1]}`;
  }

  get canExpandInflows() {
    return (
      this.invisibleGrantsIn.length ||
      (this.otherUp && this.otherUp.grants.length > 0)
    );
  }

  get canExpandOutflows() {
    return (
      this.invisibleGrants.length ||
      (this.otherDown && this.otherDown.grantsIn.length > 0)
    );
  }
  get canCompressInflows() {
    return this.grantsIn.length;
  }

  get canCompressOutflows() {
    return this.grants.length;
  }

  get isVisible() {
    return this._isVisible;
  }

  set isVisible(value) {
    if (this._isVisible != value) {
      this._isVisible = value;
      this.isOrganized = false; // force some recalc
      if (this.otherDown) {
        this.otherDown.isVisible = value;
        //this.otherDown.otherGrant.isVisible=value;
      }
      if (this.otherUp) {
        this.otherUp.isVisible = value;
        //this.otherUp.otherGrant.isVisible=value;
      }
    }
  }

  // a terminal charity doesn't have any outflows.
  get isTerminal() {
    return this.grants.length == 0;
  }

  // a root charity doesn't have any inflows
  get isRoot() {
    return this.grantsIn.length == 0;
  }

  // useful for overall scale
  get logGrantsTotal() {
    const cacheKey = `logGrantTotal-${POWER_LAW}`;
    if (this._valueCache[cacheKey]) return this._valueCache[cacheKey];
    return (this._valueCache[cacheKey] = scaleValue(this.grantsTotal));
  }

  get logGrantsInTotal() {
    const cacheKey = `logGrantsInTotal-${POWER_LAW}`;
    if (this._valueCache[cacheKey]) return this._valueCache[cacheKey];
    return (this._valueCache[cacheKey] = scaleValue(this.grantsInTotal));
  }

  //useful for the sankey which doesn't know its values are scaled
  //ok, so the problems is that sqrt(a)+sqrt(b) != sqrt(a+b)
  // so what we have to do is return the size based on the visible grants only
  // unless there aren't any, in which case logGrantsTotal is fine as a placeholder.
  // this means the trapezoid will change as flows are revealed, but they'll match the trap
  get grantsLogTotal() {
    const vgrants = this.visibleGrants;
    const cacheKey = `grantsLogTotal-${POWER_LAW}-${vgrants.length}`;
    if (this._valueCache[cacheKey]) return this._valueCache[cacheKey];
    if (vgrants.length)
      return (this._valueCache[cacheKey] = vgrants.reduce(
        (total, g) => total + g.value,
        0
      ));
    return this.logGrantsTotal;
  }

  get grantsInLogTotal() {
    const vgrants = this.visibleGrantsIn;
    const cacheKey = `grantsInLogTotal-${POWER_LAW}-${vgrants.length}`;
    if (this._valueCache[cacheKey]) return this._valueCache[cacheKey];
    if (vgrants.length)
      return (this._valueCache[cacheKey] = vgrants.reduce(
        (total, g) => total + g.value,
        0
      ));
    return this.logGrantsInTotal;
  }

  get grantsTotal() {
    const cacheKey = `grantsTotal`;
    if (this._valueCache[cacheKey]) return this._valueCache[cacheKey];
    return (this._valueCache[cacheKey] = this.grants.reduce(
      (total, g) => total + g.amt,
      0
    ));
  }

  get grantsInTotal() {
    const cacheKey = `grantsInTotal`;
    if (this._valueCache[cacheKey]) return this._valueCache[cacheKey];
    return (this._valueCache[cacheKey] = this.grantsIn.reduce(
      (total, g) => total + g.amt,
      0
    ));
  }

  get visibleGrantsTotal() {
    const cacheKey = `visibleGrantsTotal`;
    if (this._valueCache[cacheKey]) return this._valueCache[cacheKey];
    return (this._valueCache[cacheKey] = this.visibleGrants.reduce(
      (total, g) => total + g.amt,
      0
    ));
  }

  get invisibleGrantsTotal() {
    const cacheKey = `invisibleGrantsTotal`;
    if (this._valueCache[cacheKey]) return this._valueCache[cacheKey];
    return (this._valueCache[cacheKey] = this.invisibleGrants.reduce(
      (total, g) => total + g.amt,
      0
    ));
  }

  get loopbackTotal() {
    const cacheKey = `loopbackTotal`;
    if (this._valueCache[cacheKey]) return this._valueCache[cacheKey];
    return (this._valueCache[cacheKey] = this.loopbackgrants.reduce(
      (total, g) => total + g.amt,
      0
    ));
  }

  get loopForwardTotal() {
    const cacheKey = `loopforwardTotal`;
    if (this._valueCache[cacheKey]) return this._valueCache[cacheKey];
    return (this._valueCache[cacheKey] = this.loopforwards.reduce(
      (total, g) => total + g.amt,
      0
    ));
  }

  get visibleGrants() {
    const cacheKey = `visibleGrants`;
    if (this._valueCache[cacheKey]) return this._valueCache[cacheKey];
    return (this._valueCache[cacheKey] = this.grants
      .filter((g) => g.isVisible)
      .sort((a, b) => b.amt - a.amt));
  }

  get invisibleGrants() {
    const cacheKey = `invisibleGrants`;
    if (this._valueCache[cacheKey]) return this._valueCache[cacheKey];
    return (this._valueCache[cacheKey] = this.grants.filter(
      (g) => !g.isVisible
    ));
  }
  get visibleGrantsIn() {
    const cacheKey = `visibleGrantsIn`;
    if (this._valueCache[cacheKey]) return this._valueCache[cacheKey];
    return (this._valueCache[cacheKey] = this.grantsIn
      .filter((g) => g.isVisible)
      .sort((a, b) => b.amt - a.amt));
  }

  get invisibleGrantsIn() {
    const cacheKey = `invisibleGrantsIn`;
    if (this._valueCache[cacheKey]) return this._valueCache[cacheKey];
    return (this._valueCache[cacheKey] = this.grantsIn.filter(
      (g) => !g.isVisible
    ));
  }

  set isOrganized(value) {
    if (this._isOrganized != value) {
      this._valueCache = {}; //clear cache
    }
    this._isOrganized = value;
  }

  addGrant(grant) {
    if (grant instanceof Grant) {
      this.grants.push(grant);
      this.isOrganized = false;
      this._origOut -= grant.amt;
    } else {
      console.log("Error: Can only add Grant objects.");
    }
  }

  addGrantIn(grant) {
    if (grant instanceof Grant) {
      this.grantsIn.push(grant);
      this.isOrganized = false;
      this._origIn -= grant.amt;
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
    this._origOut += grant.amt;
  }
  removeGrantIn(grant) {
    const index = this.grantsIn.indexOf(grant);
    if (index !== -1) {
      this.grantsIn.splice(index, 1);
      this.isOrganized = false;
    }
    this._origIn += grant.amt;
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
    if (g.filer == this) {
      this.loopbackgrants.push(g);
      this.removeGrant(g);
    }
    if (g.grantee == this) {
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

  // check grants such that a gives to b, who gives to a
  simpleCircular() {
    const simpleCircles = this.grants.filter(
      (g1) =>
        g1.grantee.grants.filter((g2) => g2.grantee_ein == this.ein).length
    );
    return simpleCircles;
  }

  // are the gramts downstrea visible?
  get hasVisibleGrants() {
    return (
      this.visibleGrants.length > 0 &&
      this.visibleGrants.some((g) => g.isVisible)
    );
  }

  handleClick(e, count = -1) {
    if (e.altKey) {
      return this.tunnelNode(e);
    }
    if (e.metaKey || this.isTerminal) {
      console.log(`Hiding ${this.id} ${this.name}`);
      this.hide();
      return false;
    }
    /*if (this.expanded || !e.shiftKey) {
        console.log(`Shrinking ${this.id} ${this.name}`);
        this.shrink(e);
        return false;
    }
    else*/ {
      console.log(`Expanding ${this.id} ${this.name}`);
      this.expandDown(NEXT_REVEAL);
      this.recurseDownShow(0, 1);
      this.expandUp(NEXT_REVEAL);
      this.recurseUpShow(0, 1);
      return true;
    }
  }
  // grants that get clicked hide their path and their destination node
  handleGrantClick(e, g) {
    g.filer.handleClick(e, -1);
    g.grantee.handleClick(e, -1);
  }

  get origOut() {
    if (this.otherDown) return this._origOut;
    return this.grantsTotal;
  }
  get origIn() {
    if (this.otherUp) return this._origIn;
    return this.grantsInTotal;
  }

  // Hidden inflows from Upstream
  get hiddenInflowsTotal() {
    return this.otherUp ? this.otherUp.grantsTotal : 0;
  }

  // Hidden outflows from Downstream
  get hiddenOutflowsTotal() {
    return this.otherDown ? this.otherDown.grantsInTotal : 0;
  }
  // Number of hidden inflows (grants in UpstreamOther)
  get hiddenInflows() {
    return this.otherUp ? this.otherUp.grants.length : 0;
  }

  // Number of hidden outflows (grants in DownstreamOther)
  get hiddenOutflows() {
    return this.otherDown ? this.otherDown.grantsIn.length : 0;
  }

  // Total amount of visible inflows
  get visibleGrantsInTotal() {
    return this.visibleGrantsIn.reduce((total, g) => total + g.amt, 0);
  }

  // Total amount of hidden inflows
  get hiddenInflowsTotal() {
    return this.otherUp
      ? this.otherUp.grants.reduce((total, g) => total + g.amt, 0)
      : 0;
  }

  // Total amount of hidden outflows
  get hiddenOutflowsTotal() {
    return this.otherDown
      ? this.otherDown.grantsIn.reduce((total, g) => total + g.amt, 0)
      : 0;
  }

  // Existing method for visible outflows total (assuming it’s already defined)
  get visibleGrantsTotal() {
    return this.visibleGrants.reduce((total, g) => total + g.amt, 0);
  }

  // Existing method for visible inflows
  get visibleGrantsIn() {
    return this.grantsIn
      .filter((g) => g.isVisible)
      .sort((a, b) => b.amt - a.amt);
  }

  // Method to expand inflows (move grants from otherUp to parent)
  expandInflows(count = -1) {
    if (this.otherUp) {
      this.otherUp.handleClick({}, count); // Moves grants to parent
    }
    this.recurseUpShow(0, 1); // Updates visibility
  }

  // Method to compress inflows (move grants from parent to otherUp)
  compressInflows(count = NEXT_REVEAL) {
    if (!this.otherUp) {
      this.buildUpstream(count);
    }
    const grantsToMove = this.grantsIn.slice(-count); // compress works from the bottom
    grantsToMove.forEach((grant) => {
      this.otherUp.addGrant(grant);
      this.removeGrantIn(grant);
      grant.stashUp(this, this.otherUp);
    });
    this.isOrganized = false;
  }

  // Similar methods for outflows
  expandOutflows(count = -1) {
    if (this.otherDown) {
      this.otherDown.handleClick({}, count);
    }
    this.recurseDownShow(0, 1);
  }

  compressOutflows(count = NEXT_REVEAL) {
    if (!this.otherDown) {
      this.buildDownstream(count);
    }
    const grantsToMove = this.grants.slice(-count); // compress the smallest
    grantsToMove.forEach((grant) => {
      this.otherDown.addGrantIn(grant);
      this.removeGrant(grant);
      grant.stashDown(this, this.otherDown);
    });
    this.isOrganized = false;
  }
  buildDownstream(count = START_REVEAL) {
    if (!this.isTerminal && !this.otherDown && !this.isOther) {
      this._origOut = this.grantsTotal; // remememer for later
      this.otherDown = new DownstreamOther(this, count);
    }
  }
  buildUpstream(count = START_REVEAL) {
    if (!this.isRoot && !this.otherUp && !this.isOther) {
      this._origIn = this.grantsInTotal;
      this.otherUp = new UpstreamOther(this, count);
    }
  }

  expandUp(count) {
    if (!this.otherUp) return this.buildUpstream();
    if (count && this.otherUp) this.otherUp.akaw(count);
  }
  expandDown(count) {
    if (!this.otherDown) return this.buildDownstream();
    if (count && this.otherDown) this.otherDown.akaw(count);
  }
  shrink(count = NEXT_REVEAL) {
    if (this.otherUp) this.otherUp.compress(count);
    if (this.otherDown) this.otherUp.compress(count);
  }

  /*when we get turned off, we have to flow down to other nodes*/
  recurseDownHide(depth = 0, limit = 2) {
    if (this.isVisible && depth < limit) {
      this.isVisible = false;
      this.grants.forEach((g) => {
        g.grantee.recurseDownHide(depth + 1);
      });
    }
  }

  /* recurse upwards through nodes we're hiding to hide flows*/
  recurseUpHide(depth = 0, limit = 2) {
    if (this.isVisible && depth < limit) {
      this.isVisible = false;
      this.grantsIn.forEach((g) => {
        g.filer.recurseUpHide(depth + 1);
      });
    }
  }
  /*when we get turned off, we have to flow down to other nodes*/
  recurseDownShow(depth = 0, limit = 2) {
    if (!this.isVisible && depth < limit) {
      this.isVisible = true;
      this.grants.forEach((g) => {
        g.grantee.recurseDownShow(depth + 1);
      });
    }
  }

  /* recurse upwards through nodes we're hiding to hide flows*/
  recurseUpShow(depth = 0, limit = 2) {
    if (!this.isVisible && depth < limit) {
      this.isVisible = true;
      this.grantsIn.forEach((g) => {
        g.filer.recurseUpShow(depth + 1);
      });
    }
  }
  show() {
    this.isVisible = true;
    viewModel.removeFromHideList(this.id);
    viewModel.addBreadCrumb(`Show|${this.id}`);
  }

  hide() {
    viewModel.addBreadCrumb(`Hide|${this.id}`);
    viewModel.addToHideList(this.id);
    this.recurseUpHide(0, 1);
    this.isVisible = true; // to fake out recruseDownHide
    this.recurseDownHide(0, 1);
  }
  hideUp() {
    this.grantsIn.forEach((g) => g.filer.eatGrant(g));
    this.isVisible = false;
  }

  /**
    
        So we want to be able to capture the state of the graph, so we need
        to report our node state as a string with our EIN.
    */
  URLPiece() {
    if (!this.isVisible || this.isOther || (!this.otherUp && !this.otherDown))
      return null;
    return `${this.ein}:${this.grantsIn.length}:${this.grants.length}`;
  }

  /**
        we we match if any of the words in the search stricng match our EIN or our
        name
    */
  searchMatch(keywords) {
    const lowerStr = this.name.toLowerCase();
    return keywords
      .map((kw) => kw.trim().toLowerCase())
      .filter((kw) => kw !== "")
      .some((kw) => lowerStr.includes(kw));
  }

  tunnelNode() {
    //mark every other node and grant invisible
    Object.values(Charity.charityLookup).forEach((c) => (c.isVisible = false));
    Object.values(Grant.grantLookup).forEach((g) => (g.isVisible = false));
    // add just us to chart
    Charity.placeNode(this.id);
    return true;
  }

  place(upCount = START_REVEAL, downCount = START_REVEAL) {
    // show ourselves and generate the other notes
    this.expandDown(downCount);
    this.expandUp(upCount);
    this.organize();
    this.expanded = true;
    this.isVisible = true;
  }

  /* show node and appropriate number of grants*/
  static placeNode(startEIN, simple = false) {
    const splits = startEIN.split(/:/);
    const c = Charity.getCharity(splits[0]);
    if (c) {
      if (!c.isVisible) c.place(splits[1], splits[2]);
    } else {
      console.log(`Couldn't place ${startEIN}'`);
    }
    return c;
  }

  toolTipText() {
    let outFlows = "";
    let inFlows = "";
    let loopbacks = "";
    if (this.grantsInTotal)
      inFlows = `\ngrants in: $${formatNumber(this.grantsInTotal)}`;
    else inFlows = `\n in: N/A`;
    if (this.grantsTotal)
      outFlows = `\ngrants out: $${formatNumber(this.grantsTotal)}`;
    else outFlows = `\nout: N/A`;
    if (this.otherUp?.grants.length > 1)
      inFlows += `\n more in: ${this.otherUp.grants.length}/$${formatNumber(
        this.otherUp.grantsTotal
      )})`;
    if (this.otherDown?.grantsIn.length > 1)
      outFlows += `\n more out: ${
        this.otherDown.grantsIn.length
      }/$${formatNumber(this.otherDown.grantsInTotal)})`;
    if (this.loopbackTotal) {
      loopbacks = `\n$\nLoop Backs: $${formatNumber(this.loopbackTotal)}`;
    }
    if (this.loopforwardTotal) {
      loopbacks += `\n$\nLoop Forwards: $${formatNumber(
        this.loopforwardTotal
      )}`;
    }
    return `${this.name}\n${this.ein}${inFlows}${outFlows}${loopbacks}`;
  }
  longEIN() {
    return `${this.ein.slice(0, 2)}-${this.ein.slice(2)}`;
  }
  officersLink(ein) {
    return `/officers/?nonprofit_kw=${this.longEIN()}`;
  }
  financialsLink(ein) {
    return `/nonprofit/assets/?filter=${this.ein}`;
  }

  nonprofitsLink() {
    return `/nonprofit/?filter=${this.ein}`;
  }
  propublicaLink(message) {
    return `<a href="https://projects.propublica.org/nonprofits/organizations/${this.ein}/${this.xml_name}/full" 
           target="_blank" rel="noopener noreferrer" class="whitespace-nowrap">
          ${message}
        </a>`;
  }
  percentTaxpayer() {
    // need the govIndirectAmt to calculate this
    return (
      ((this.govt_amt + govIndirectAmt) / (this.govt_amt + this.contrib_amt)) *
      100
    );
  }
}
class DownstreamOther extends Charity {
  constructor(parent, count = START_REVEAL) {
    super({
      ein: `${parent.ein}-Down`,
      name: `More grants from ${parent.name}...`,
      xml_name: `${parent.xml_name}-Down`,
      isVisible: true,
      isOther: true,
      isOrganized: false,
      grantsIn: [], // do this later parent.grants.filter(g => !g.shouldHide() && !g.isOther).slice(count),
      grants: [], // Enforce no outflows
    });
    this.isRight = true;
    this.parent = parent;
    this.parent.otherDown = this;
    // the virtual grant that represents us
    this.otherGrant = new Grant({
      filer_ein: this.parent.ein,
      filter: this.parent,
      grantee_ein: this.ein,
      grantee: this,
      amt: 0,
      isOther: true,
      isOtherDest: this,
    });
    // special case at build time, count is
    // how many to *leave*
    this.waka(parent.grants.length - count); // all of them!
    this.isVisible = true; // we we exist, you can see us
    // another special case, mark whatever is left visible
    this.parent.grants.forEach((c) => (c.isVisible = true));
    this.organize();
  }

  eatGrant(g) {
    this.parent.removeGrant(g);
    this.addGrantIn(g);
    g.stashDown(this.parent, this);
    return g.amt;
  }

  // so this is the opposite of eating a grant.
  pukeGrant(g) {
    this.removeGrantIn(g);
    this.parent.addGrant(g);
    g.unstashDown(this.parent, this);
    g.grantee.isVisible = true;
    return g.amt;
  }
  handleClick(e) {
    if (e.shiftKey) this.waka();
    else this.akaw();
    this.parent.recurseDownShow(1);
  }

  // Gen X joke: Waka is the sound the pacman makes.
  waka(count = NEXT_REVEAL) {
    const grantsToEat = this.parent.grants
      .filter((g) => !g.shouldHide() && !g.isOther)
      .slice(-count); // eat the weakest first
    grantsToEat.forEach((g) => {
      this.otherGrant.amt += this.eatGrant(g);
    });
    this.parent.grants.forEach((g) => (g.isVisible = true)); // remaining should be visible
  }
  // akaw is the sound of a pacman going backwards
  akaw(count = NEXT_REVEAL) {
    const grantsToPuke = this.grantsIn
      .filter((g) => !g.isOther)
      .slice(0, count); // put the strongest back
    grantsToPuke.forEach((g) => {
      this.otherGrant.amt -= this.pukeGrant(g); // the pacman givith, the pacman takith away
    });
  }

  get isVisible() {
    const visible = this.parent.isVisible && this.grantsIn.length > 1; // don't count ours
    return visible;
  }
  set isVisible(v) {
    super.isVisible = v;
  }

  toolTipText() {
    const hiddenflow = `\nhidden: $${formatNumber(this.grantsInTotal)}`;

    return `Rolldown ${this.parent.name}\n${this.parent.ein}${hiddenflow}`;
  }
}

class UpstreamOther extends Charity {
  constructor(parent, count = START_REVEAL) {
    super({
      ein: `${parent.ein}-Up`,
      name: `More for ${parent.name}...`,
      xml_name: `${parent.xml_name}-Down`,
      isVisible: true,
      isOther: true,
      isOrgnaized: false,
      grants: [], // later parent.grantsIn.filter(g => !g.shouldHide() && !g.isOther).slice(count),
      grantsIn: [], // Enforce no inflows
    });
    this.isRight = false;
    this.parent = parent;
    this.parent.otherUp = this;
    this.otherGrant = new Grant({
      filer_ein: this.ein,
      grantee_ein: this.parent.ein,
      filer: this,
      grantee: this.parent,
      amt: this.grantsTotal,
      isOther: true,
      isOtherDest: this,
    });
    // at construction time, count means how many to leave
    // not how many to eat.
    // then we have to make the remaingig visible
    this.waka(this.parent.grantsIn.length - count);
    this.parent.grantsIn.forEach((c) => (c.isVisible = true));
    this.organize();
  }

  eatGrant(g) {
    this.parent.removeGrantIn(g);
    this.addGrant(g);
    g.stashUp(this.parent, this);
    return g.amt;
  }

  pukeGrant(g) {
    this.removeGrant(g);
    this.addGrantIn(g);
    g.unstashUp(this.parent, this);
    g.filer.isVisible = true;
    return g.amt;
  }

  // Gen X joke: Waka is the sound the pacman makes.
  waka(count) {
    const grantsToEat = this.parent.grantsIn
      .filter((g) => !g.shouldHide() && !g.isOther)
      .slice(-count); // eat the weak and lame
    grantsToEat.forEach((g) => {
      this.otherGrant.amt += this.eatGrant(g);
    });
    this.parent.grants.forEach((g) => (g.isVisible = true)); // remaining should be visible
  }
  // akaw is the sound of a pacman going backwards
  akaw(count) {
    const grantsToPuke = this.grantsIn
      .filter((g) => !g.isOther)
      .slice(0, count); // return the best
    grantsToPuke.forEach((g) => {
      this.otherGrant.amt -= this.pukeGrant(g); // the pacman givith, the pacman takith away
    });
  }

  handleClick(e, count = NEXT_REVEAL) {
    if (e.shitKey) this.waka(count);
    else this.akaw(count);
  }

  handleGrantClick(e, g) {
    g.filer.handleClick(e, -1);
  }

  get isVisible() {
    const visible = this.parent.isVisible && this.grants.length > 1; //don't count ours
    return visible;
  }
  set isVisible(v) {
    super.isVisible = v;
  }

  toolTipText() {
    const hiddenflow = `\nhidden: $${formatNumber(this.grantsTotal)}`;

    return `Rollup ${this.parent.name}\n${this.parent.ein}${hiddenflow}`;
  }
}

/**
        Class to hold a grant, aka a flow between two charities
*/
class Grant {
  static grantLookup = {};

  static getGrant(id) {
    const g = Grant.grantLookup[id];
    /*if (!g) {
      console.log(`Couldn't find Grant ${id}`);
    }*/
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
      .filter((c) => !c.isOther)
      .flatMap((c) => [
        ...c.visibleGrants.map((g) => g.id),
        ...c.visibleGrantsIn.map((g) => g.id),
      ]);

    // Deduplicate the IDs
    const uniqueGrantIds = [...new Set(allGrantIds)];

    // Map back to full Grant objects
    return uniqueGrantIds.map((gid) => Grant.getGrant(gid));
  }

  static checkGrantMatch(filer_ein, grantee_ein) {
    return (
      filer_ein !== grantee_ein &&
      Charity.getCharity(filer_ein) &&
      Charity.getCharity(grantee_ein)
    );
  }

  shouldHide() {
    return (
      !Charity.shouldHide(this.filer_ein) &&
      Charity.shouldHide(this.grantee_ein)
    );
  }

  resetSourceTarget() {
    this._source = null;
    this.sourceLinks = [];
    this._target = null;
    this.targetLinks = [];
  }
  /* next few accessors implement the sankey API*/
  get source() {
    // name alias for sankey
    return this._source || this.filer_ein;
  }

  set source(s) {
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

  static grantIDBuilder(filer_ein, grantee_ein) {
    return `${filer_ein}~${grantee_ein}`;
  }
  static loadGrantRow(row) {
    const filer = (row["filer_ein"] || "").trim();
    const grantee = (row["grant_ein"] || "").trim();
    let amt = parseInt((row["grant_amt"] || "0").trim(), 10);
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
          amt: amt,
        });
      }
    } else if (filer !== grantee) {
      console.warn(`Missing charity for EIN: ${filer} or ${grantee}`);
    }
    return null;
  }
  constructor({
    filer_ein,
    grantee_ein,
    amt = 0,
    isCircular = false,
    isVisible = false,
    isOther = false,
    isOtherDest,
  }) {
    this.registered = false;
    this.amt = amt;
    this.filer_ein = filer_ein;
    this.grantee_ein = grantee_ein;
    this.filer = Charity.getCharity(filer_ein);
    this.grantee = Charity.getCharity(grantee_ein);
    Charity.addGrant(this);
    this.registered = true;
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
    return this.filer.isVisible && this.grantee.isVisible; // safest to compute this
  }

  set isVisible(v) {
    //if (v != this._isVisible)
    {
      this._isVisible = v;
      // if we're visible, we have to have somewhere to draw from/to.
      this.filer.isVisible = v;
      this.grantee.isVisible = v;
      this.disorganize();
    }
  }

  set isCircular(value) {
    if (value !== this.isCircular && this.registered) {
      // avoid race condition
      Charity.circularGrant(this);
    }
    this._isCircular = value;
  }
  buildId() {
    this.filer_ein = this.filer.ein;
    this.grantee_ein = this.grantee.ein;
    this.id = Grant.grantIDBuilder(this.filer_ein, this.grantee_ein);
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
    Object.values(Charity.charityLookup).forEach((c) => (c.isVisible = false));
    Object.values(Grant.grantLookup).forEach((g) => (g.isVisible = false));
    // add ourselves back, plus the nodes we point to
    this.isVisible = true;
    Charity.placeNode(this.filer_ein);
    Charity.placeNode(this.grantee_ein);
  }
}
function updateStatus(message, color = "black", loading = true) {
  $("#status").html(`<span class="flex items-center text-sm">
                ${
                  loading
                    ? '<img src="/assets/images/loading.svg" class="size-6" alt="Loading...">'
                    : ""
                }
                ${message}.</span>`);
}

export { formatNumber, Charity, Grant, scaleValue, BrowseViewModel, viewModel };
