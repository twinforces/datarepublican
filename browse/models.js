const POWER_LAW_RESET = 3;
const TOP_N_INITIAL = 5;
const START_REVEAL = 5;
const MIN_REVEAL = 2;
const NEXT_REVEAL = 3;
const NEXT_REVEAL_MAX = 15;
const GOV_EIN = "001";
const MAX_NODES = 100;
const STORE_CHUNK_SIZE = 10000;
const PROCESS_CHUNK_SIZE = 10000;
const CHUNK_SIZE = 10000;

const MAX_KEYWORD_NODES = 25;

const DB_NAME = "CharityDatabase";
const DB_VERSION = 1;
const CHARITY_STORE = "charities";
const GRANT_STORE = "grants";
const METADATA_STORE = "metadata";
const DATA_VERSION = "2025-06-25";

import { DATA_FILES } from "./data_files.js"; // Adjust path if needed

import ORGANIZATION_TYPES from "./charityTypes.js";
import { iso3166_alpha2 } from "./countryCodes.js";
import { openDB } from "https://cdn.jsdelivr.net/npm/idb@8/+esm";
import JSZip from "https://cdn.jsdelivr.net/npm/jszip@3.10.1/+esm";
import presetsData from "./presets.js";
let GOV_NODE = null; // I use this when debugging.

/**
 * Tried logarithmic scaling, but it was too drastic 1M vs. 1B was 3. 
 * Power law scaling is more natural, default of cube root means
 * it looks like this:
 * 
 * Steps    Power Law (x^(1/3))    Logarithmic (ln(x))
1K       10                     6.9078
1M       100                    13.8155
1B       1,000                  20.7233
1T       10,000                 27.6310

 * @param {*} amt 
 * @returns 
 */
export function scaleValue(amt) {
  return Math.pow(amt, 1 / viewModel.POWER_LAW);
}

/**
 * Aka what ls et all call human scaling.
 * @param {*} num
 * @returns
 */
export function formatNumber(num) {
  if (num >= 1e12) return (num / 1e12).toFixed(1) + "T";
  if (num >= 1e9) return (num / 1e9).toFixed(1) + "B";
  if (num >= 1e6) return (num / 1e6).toFixed(1) + "M";
  if (num >= 1e3) return (num / 1e3).toFixed(1) + "K";
  return num == null ? "N/A" : num.toString();
}

/**
 * As they say in Highlander, there can be only one
 */
let viewModel = null;

// Hash function
function hashEIN(ein) {
  let hash = 0;
  for (let i = 0; i < ein.length; i++) {
    hash = (hash * 31 + ein.charCodeAt(i)) % 1000000;
  }
  return hash / 1000000;
}

// Sinebow function
function sinebow(t) {
  t = t * 2 * Math.PI;
  const offset = Math.PI / 2;
  const r = Math.sin(t + offset);
  const g = Math.sin(t + offset + (2 * Math.PI) / 3);
  const b = Math.sin(t + offset + (4 * Math.PI) / 3);
  return d3.rgb(
    Math.floor((0.2 + 0.6 * r * r) * 255),
    Math.floor((0.2 + 0.6 * g * g) * 255),
    Math.floor((0.2 + 0.6 * b * b) * 255)
  );
}

// Color function
export function getColorForEIN(ein) {
  return sinebow((hashEIN(ein) * 5) % 1);
}

// Initialize IndexedDB

async function initDB() {
  console.log("Opening IndexedDB: CharityDatabase");
  // Close any existing connections
  const existingDBs = indexedDB.databases ? await indexedDB.databases() : [];
  for (const dbInfo of existingDBs) {
    if (dbInfo.name === "CharityDatabase") {
      console.log("Closing existing CharityDatabase connection");
      await new Promise((resolve) => {
        const request = indexedDB.open(dbInfo.name);
        request.onsuccess = () => {
          request.result.close();
          resolve();
        };
      });
    }
  }

  return new Promise((resolve, reject) => {
    const request = indexedDB.open("CharityDatabase", 1);
    request.onupgradeneeded = (event) => {
      const db = event.target.result;
      console.log("Creating or upgrading IndexedDB stores...");
      if (db.objectStoreNames.contains(CHARITY_STORE)) {
        db.deleteObjectStore(CHARITY_STORE);
        console.log(`Deleted existing ${CHARITY_STORE} store`);
      }
      if (db.objectStoreNames.contains(GRANT_STORE)) {
        db.deleteObjectStore(GRANT_STORE);
        console.log(`Deleted existing ${GRANT_STORE} store`);
      }
      if (db.objectStoreNames.contains(METADATA_STORE)) {
        db.deleteObjectStore(METADATA_STORE);
        console.log(`Deleted existing ${METADATA_STORE} store`);
      }
      db.createObjectStore(CHARITY_STORE, { keyPath: "filer_ein" });
      console.log(`Created ${CHARITY_STORE} store with keyPath: filer_ein`);
      db.createObjectStore(GRANT_STORE, { keyPath: "id" });
      console.log(`Created ${GRANT_STORE} store with keyPath: id`);
      db.createObjectStore(METADATA_STORE, { keyPath: "id" });
      console.log(`Created ${METADATA_STORE} store with keyPath: id`);
    };
    request.onsuccess = () => {
      console.log("IndexedDB opened successfully");
      resolve(request.result);
    };
    request.onerror = () => {
      console.error("Failed to open IndexedDB:", request.error);
      reject(request.error);
    };
  });
}

async function clearStorage() {
  try {
    const db = await initDB();
    const tx = db.transaction(
      [CHARITY_STORE, GRANT_STORE, METADATA_STORE],
      "readwrite"
    );
    await tx.objectStore(CHARITY_STORE).clear();
    await tx.objectStore(GRANT_STORE).clear();
    await tx.objectStore(METADATA_STORE).clear();
    await tx.done;
    Charity.charityLookup = {};
    Grant.grantLookup = {};
    updateStatus("Local storage cleared, reloading data...", "black", true);
    await viewModel.loadData();
  } catch (err) {
    console.error("Error clearing storage:", err);
    updateStatus(`Error clearing storage: ${err.message}`, "red", false);
    throw err;
  }
}

async function hasValidData(db) {
  try {
    const tx = db.transaction(METADATA_STORE, "readonly");
    const store = tx.objectStore(METADATA_STORE);
    const versionRequest = await new Promise((resolve, reject) => {
      const request = store.get("version");
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
    });
    const generatedRequest = await new Promise((resolve, reject) => {
      const request = store.get("generated");
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
    });
    await tx.done;
    return (
      versionRequest?.value === DATA_FILES.dbVersion &&
      generatedRequest?.value === true
    );
  } catch (error) {
    console.error("Error checking hasValidData:", error);
    return false;
  }
}

// Fetch data from IndexedDB
async function fetchLocalData(db, storeName) {
  try {
    const tx = db.transaction(storeName, "readonly");
    const store = tx.objectStore(storeName);
    const records = await store.getAll();
    await tx.done;
    return records;
  } catch (err) {
    console.error(`Error fetching data from ${storeName}:`, err);
    updateStatus(
      `Error loading ${storeName} from local storage: ${err.message}`,
      "red",
      false
    );
    throw err;
  }
}

async function storeData(db, storeName, records) {
  if (!db) {
    throw new Error(`Database is undefined in storeData for ${storeName}`);
  }
  if (!storeName) {
    throw new Error(`storeName is undefined in storeData`);
  }
  if (!Array.isArray(records)) {
    throw new Error(
      `Records is not an array in storeData for ${storeName}: ${JSON.stringify(
        records
      )}`
    );
  }
  if (!db.objectStoreNames.contains(storeName)) {
    throw new Error(`Store ${storeName} does not exist in database`);
  }

  try {
    console.time(`storeD-${storeName}`);
    const tx = db.transaction(storeName, "readwrite");
    const store = tx.objectStore(storeName);
    let storedRecords = 0;

    for (const record of records) {
      await new Promise((resolve, reject) => {
        const request = store.put(record);
        request.onsuccess = () => {
          storedRecords++;
          resolve();
        };
        request.onerror = () => {
          console.error(
            `Failed to store record in ${storeName} with filer_ein ${record.filer_ein}:`,
            request.error
          );
          reject(request.error);
        };
      });
    }

    await new Promise((resolve, reject) => {
      tx.oncomplete = () => resolve();
      tx.onabort = () =>
        reject(new Error(`Transaction aborted for ${storeName}`));
      tx.onerror = () => reject(tx.error);
    });

    console.timeEnd(`storeD-${storeName}`);
    return storedRecords;
  } catch (err) {
    console.error(`Error storing data in ${storeName}:`, err);
    updateStatus(`Error storing ${storeName}: ${err.message}`, "red", false);
    throw err;
  }
}

async function exportDB() {
  try {
    const db = await initDB();
    const tx = db.transaction(
      [CHARITY_STORE, GRANT_STORE, METADATA_STORE],
      "readonly"
    );
    const charities = await tx.objectStore(CHARITY_STORE).getAll();
    const grants = await tx.objectStore(GRANT_STORE).getAll();
    const metadata = await tx.objectStore(METADATA_STORE).getAll();
    await tx.done;

    // Export charities
    const charitiesBlob = new Blob([JSON.stringify({ charities })], {
      type: "application/json",
    });
    const charitiesUrl = URL.createObjectURL(charitiesBlob);
    const charitiesA = document.createElement("a");
    charitiesA.href = charitiesUrl;
    charitiesA.download = "charities_db.json";
    charitiesA.click();
    URL.revokeObjectURL(charitiesUrl);

    // Export grants
    const grantsBlob = new Blob([JSON.stringify({ grants })], {
      type: "application/json",
    });
    const grantsUrl = URL.createObjectURL(grantsBlob);
    const grantsA = document.createElement("a");
    grantsA.href = grantsUrl;
    grantsA.download = "grants_db.json";
    grantsA.click();
    URL.revokeObjectURL(grantsUrl);

    // Export metadata
    const metadataBlob = new Blob([JSON.stringify({ metadata })], {
      type: "application/json",
    });
    const metadataUrl = URL.createObjectURL(metadataBlob);
    const metadataA = document.createElement("a");
    metadataA.href = metadataUrl;
    metadataA.download = "metadata_db.json";
    metadataA.click();
    URL.revokeObjectURL(metadataUrl);

    updateStatus("Database exported successfully", "green", false);
  } catch (err) {
    console.error("Error exporting database:", err);
    updateStatus(`Error exporting database: ${err.message}`, "red", false);
    throw err;
  }
}

async function fetchAndStoreTSV(db, files) {
  try {
    if (!Array.isArray(files)) {
      throw new Error(
        `files parameter is not an array: ${JSON.stringify(files)}`
      );
    }
    updateStatus(`Fetching charities and grants...`);
    let charityRowsProcessed = 0;
    let charityRowsSkipped = 0;
    let grantRowsProcessed = 0;
    let grantRowsSkipped = 0;
    const BATCH_SIZE = STORE_CHUNK_SIZE;
    const MAX_CONCURRENT = 5;

    async function processChunk({
      tsvText,
      tsvFile,
      chunkIndex,
      type,
      grantType,
    }) {
      const lines = tsvText.split("\n").filter((line) => line.trim());
      if (lines.length < 1) {
        return { processed: 0, skipped: 0 };
      }
      const headers = lines[0].split("\t").map((header) => header.trim());
      const expectedColumns =
        type === "charities"
          ? [
              "filer_ein",
              "filer_name",
              "xml_name",
              "receipt_amt",
              "govt_amt",
              "contrib_amt",
              "tax_year",
              "org_type",
              "total_assets",
              "form_type",
              "denominator",
            ]
          : ["filer_ein", "grant_ein", "grant_amt"];
      const columnMap = {};
      headers.forEach((header, i) => {
        columnMap[header] = i;
      });

      const missingColumns = expectedColumns.filter(
        (col) => !(col in columnMap)
      );
      if (missingColumns.length > 0) {
        return { processed: 0, skipped: 0 };
      }

      const records = [];
      let rowsProcessed = 0;
      let rowsSkipped = 0;

      for (
        let startIndex = 1;
        startIndex < lines.length;
        startIndex += BATCH_SIZE
      ) {
        const endIndex = Math.min(startIndex + BATCH_SIZE, lines.length);
        let row;
        try {
          console.time(`processTSV-${tsvFile}-${startIndex}`);
          for (let index = startIndex; index < endIndex; index++) {
            const values = lines[index]
              .split("\t")
              .map((value) => (value ? value.trim() : ""));
            if (values.length != expectedColumns.length) {
              rowsSkipped++;
              continue;
            }
            row = {};
            expectedColumns.forEach((header) => {
              row[header] =
                columnMap[header] < values.length
                  ? values[columnMap[header]]
                  : "";
            });

            if (type === "charities") {
              const charity = {
                filer_ein: row.filer_ein,
                filer_name: row.filer_name || "",
                name: row.filer_name || "",
                xml_name: row.xml_name || "",
                receipt_amt: parseInt(row.receipt_amt || "0", 10) || 0,
                govt_amt: parseInt(row.govt_amt || "0", 10) || 0,
                contrib_amt: parseInt(row.contrib_amt || "0", 10) || 0,
                tax_year: row.tax_year ? parseInt(row.tax_year, 10) : null,
                org_type: row.org_type || null,
                total_assets: row.total_assets
                  ? parseFloat(row.total_assets)
                  : null,
                form_type: row.form_type || null,
                denominator: row.denominator
                  ? parseFloat(row.denominator)
                  : null,
              };
              if (
                !charity.filer_ein ||
                !/^[0-9]{3,9}$/.test(charity.filer_ein)
              ) {
                rowsSkipped++;
                continue;
              }
              if (charity.xml_name && !/.*\.xml$/.test(charity.xml_name)) {
                rowsSkipped++;
                continue;
              }
              records.push(charity);
              try {
                Charity.buildCharityFromRow(charity);
              } catch (buildError) {
                rowsSkipped++;
                continue;
              }
              rowsProcessed++;
            } else {
              let grant_ein = row.grant_ein;
              let filer_ein = row.filer_ein;
              if (grant_ein?.length === 7) grant_ein = "0" + grant_ein;
              if (filer_ein?.length === 7) filer_ein = "0" + filer_ein;
              const grant = {
                id: `${filer_ein}~${grant_ein}`,
                filer_ein,
                grant_ein,
                amt: parseInt(row.grant_amt || row.amt || "0", 10) || 0,
                grantType,
              };
              if (
                grant.filer_ein &&
                grant.grant_ein &&
                grant.filer_ein !== grant.grant_ein &&
                /^[0-9]{3,9}$/.test(grant.grant_ein)
              ) {
                records.push(grant);
                Grant.loadGrantRow(grant, grantType);
                rowsProcessed++;
              } else {
                rowsSkipped++;
              }
            }
          }
          console.timeEnd(`processTSV-${tsvFile}-${startIndex}`);

          if (records.length > 0) {
            console.time(`storeTSV-${tsvFile}-${startIndex}`);
            try {
              await storeData(
                db,
                type === "charities" ? CHARITY_STORE : GRANT_STORE,
                records.splice(0, records.length)
              );
            } catch (storeError) {
              console.error(
                `Failed to store ${type} for chunk ${chunkIndex}:`,
                storeError
              );
              throw storeError;
            }
            console.timeEnd(`storeTSV-${tsvFile}-${startIndex}`);
          }
          updateStatus(
            `Loaded ${formatNumber(
              Object.keys(Charity.charityLookup).length
            )} charities, ${formatNumber(
              Object.keys(Grant.grantLookup).length
            )} grants`
          );
        } catch (err) {
          console.error(
            `Error in batch for ${tsvFile} at row ${rowsProcessed}:`,
            err
          );
          throw err;
        }
      }
      return { processed: rowsProcessed, skipped: rowsSkipped };
    }

    async function fetchAndProcessChunk(file, type, grantType, chunkIndex) {
      const zipFile = `${file.baseFile}${chunkIndex}.tsv.zip`;
      const tsvFile = `${file.tsvFilePrefix}${chunkIndex}.tsv`;
      try {
        const response = await fetch(zipFile);
        if (!response.ok) {
          if (response.status === 404) {
            return null;
          }
          throw new Error(`HTTP error ${response.status} for ${zipFile}`);
        }
        const zipBlob = await response.blob();
        const zip = await JSZip.loadAsync(zipBlob);
        const tsv = zip.file(tsvFile);
        if (!tsv) {
          return null;
        }
        const tsvText = await tsv.async("text");
        return await processChunk({
          tsvText,
          tsvFile,
          chunkIndex,
          type,
          grantType,
        });
      } catch (error) {
        console.error(`Error fetching ${zipFile}:`, error);
        return null;
      }
    }

    async function fetchWithLimit(tasks) {
      const results = [];
      const executing = new Set();

      for (let i = 0; i < tasks.length; i++) {
        if (executing.size >= MAX_CONCURRENT) {
          await Promise.race(executing);
          const completed = [...executing].find(
            (p) =>
              p[Symbol.toStringTag] === "Promise" && p.status === "fulfilled"
          );
          if (completed) {
            results.push(await completed);
            executing.delete(completed);
          }
        }
        const promise = tasks[i]().then((result) => {
          executing.delete(promise);
          return result;
        });
        executing.add(promise);
        results.push(promise);
      }

      return Promise.all(results);
    }

    for (const file of files) {
      updateStatus(
        `Processing ${file.tsvFilePrefix}: ${formatNumber(
          Object.keys(Charity.charityLookup).length
        )} charities, ${formatNumber(
          Object.keys(Grant.grantLookup).length
        )} grants`
      );
      const tasks = [];
      const maxChunks = file.chunkCount;
      for (let chunkIndex = 0; chunkIndex < maxChunks; chunkIndex++) {
        tasks.push(() =>
          fetchAndProcessChunk(file, file.type, file.grantType, chunkIndex)
        );
      }
      const results = await fetchWithLimit(tasks);
      for (const result of results) {
        if (result) {
          if (file.type === "charities") {
            charityRowsProcessed += result.processed;
            charityRowsSkipped += result.skipped;
          } else {
            grantRowsProcessed += result.processed;
            grantRowsSkipped += result.skipped;
          }
        }
      }
    }

    return [];
  } catch (error) {
    console.error(`Error processing files:`, error);
    updateStatus(`Error loading data: ${error.message}`, "red", false);
    throw error;
  }
}
/**
 * So this is an M-V-VM architecture.
 * M - Model, deals with the data
 * V = View, displays the data
 * VM = View Model, translates between M and V.
 *
 * I've tried MVC, didn't work. MVVM does.
 */
export class BrowseViewModel {
  #_presetsData = presetsData; // Private field initialized with imported presets

  constructor({ POWER_LAW = POWER_LAW_RESET, GOV_EIN = "001" } = {}) {
    this.POWER_LAW = POWER_LAW; /** Users can change the scaling on the fly */
    this.GOV_EIN =
      GOV_EIN; /** Had to pick something, it was this or 0000000001  */
    this.GOV_NODE = null; /** keep this around for debugging */

    /**
     * Ok, a lot of the work between the M and the V is about maintaining visibilitiy.
     * Nominally, we start with 100,000 nodes, 660,000 edges.
     * We need to capture things the user wants (showList) and things they don't want
     * hideList. keywords is another way to specify things to show.
     *
     * Strictly speaking, one could argue that its the ViewModels job to maintain
     * visibility, but we have the Model calculate how that propogates.
     */
    this.hideList = {};
    this.showList = {};

    /** this isn't used yet, but the idea is that eventually we'll keep track
     * of the users explorations, so we can replay them to adjust the visibility.
     */
    this.breadCrumbs = [];
    this.keywords = {};

    /** Essentially globals */
    this.renderData = null;
    this.dataReady = false;
    viewModel = this;
    this.resetAll();
  }

  exportDB() {
    exportDB();
  }

  clearAll() {
    for (const c of Charity.visibleCharities) c.clearVisibility();
  }

  presets() {
    return this.#_presetsData;
  }

  loadPreset(preset, mode) {
    const eins = preset.eins;
    if (mode === "replace") {
      this.clearAll();
      this.setShowList(eins);
      this.computeAndSaveURLParams();
    } else {
      for (const e of eins) {
        if (e === "-86") {
          // quick hack.
          clearStorage();
          return;
        }
        this.addToShowList(e);
      }
    }
  }

  /**Called when we focus on just one node*/
  resetAll() {
    Object.values(Grant.grantLookup).forEach((g) => {
      g.desiredVisible = false;
      g.impliedVisible = 0;
    });
  }

  /** methods for manipulating the scaling */

  setGraphScale(scale) {
    if (scale != this.POWER_LAW) {
      this.POWER_LAW = scale;
      Charity.disorganzeAll();
    }
  }
  graphScaleDown() {
    this.POWER_LAW++;
    this.computeURLParams(); // update URL
    Charity.disorganzeAll();
  }

  graphScaleUp() {
    this.POWER_LAW--;
    if (this.POWER_LAW < 1) this.POWER_LAW = 1;
    this.computeURLParams(); // update URL
    Charity.disorganzeAll();
  }

  graphScaleReset() {
    this.POWER_LAW = POWER_LAW_RESET;
    this.computeURLParams(); // update URL
    Charity.disorganzeAll();
  }

  /** methods for manipulating the Show List */
  addToShowList(ein) {
    const c = Charity.getCharity(ein);
    if (c) {
      this.showList[c.ein] = ein.split(/[:~]/).slice(1) || [
        START_REVEAL,
        START_REVEAL,
      ];
      c.desiredVisible = true;
    }
  }

  removeFromShowList(ein) {
    const id = ein.split(/[:~]/)[0];
    delete this.showList[id];
    const c = Charity.getCharity(id);
    if (c) {
      c.clearVisibility();
    }
  }

  getShowList() {
    const result = Object.entries(this.showList)
      .sort((a, b) => a[0] - b[0]) // sort by key
      .map(
        ([key, value]) =>
          `${key}~${value[0] || START_REVEAL}~${value[1] || START_REVEAL}`
      );
    return result;
  }

  setShowList(list) {
    this.showList = {};
    list.forEach((ein) => this.addToShowList(ein));
    return this.getShowList();
  }

  clearShowList() {
    this.showList = {};
  }

  /** methods for manipulating the hide list */
  addToHideList(ein) {
    const c = Charity.getCharity(ein);
    if (c) {
      this.hideList[ein] = 1;
      c.desiredVisible = false;
    }
  }

  removeFromHideList(ein) {
    delete this.hideList[ein];
    const c = Charity.getCharity(ein);
    if (c) c.desiredVisible = true;
    viewModel.computeImpliedVisibility(c, true, true);
  }

  shouldHide(ein) {
    return this.hideList[ein];
  }

  getHideList() {
    return Object.keys(this.hideList).sort();
  }

  setHideList(list) {
    this.hideList = {};
    list.forEach((ein) => this.addToHideList(ein));
    return this.getHideList();
  }

  clearHideList() {
    this.hideList = {};
  }

  /** bread crumbs, as noted, aspirational at the moment */
  addToBreadCrumbs(crumb) {
    this.breadCrumbs.push(crumb);
  }

  removeFromBreadCrumbs(crumb) {
    this.breadCrumbs = this.breadCrumbs.filter((c) => c !== crumb);
  }

  getBreadCrumbs() {
    return this.breadCrumbs;
  }

  setBreadCrumbs(list) {
    this.breadCrumbs = list;
  }

  /** keep track of search keywords */
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
    this.keywords = Object.fromEntries(list.map((key) => [key, 1]));
  }

  /** match Charities against search terms */
  matchKeys() {
    return Object.values(Charity.charityLookup).filter((c) =>
      c.searchMatch(Object.keys(this.keywords))
    );
  }

  /**
   * After we expand or compress, we have to reset the EIN in the show list.
   *
   */
  resetEIN(ein) {
    const c = Charity.getCharity(ein);
    if (c) {
      this.addToShowList(c.URLPiece());
    }
  }

  /** Given a model in a given state, calculate the minimum URL necessary to replicate that
   * state. Since visibility can be direct (called desired in the model) or implied, we
   * only need to include the desired charities, not the implied.
   */
  computeURLParams() {
    const params = new URLSearchParams();
    let visibleMap = {};
    Charity.desiredCharities.forEach((c) => {
      const p = c.URLPiece();
      if (p) visibleMap[c.ein] = p;
    });
    this.getHideList().forEach((ein) => delete visibleMap[ein]);
    Object.values(visibleMap).forEach((e) => params.append("ein", e));
    this.getHideList().forEach((e) => params.append("nein", e));
    this.getKeywordList().forEach((k) => params.append("keywords", k));
    params.append("scale", this.POWER_LAW);
    return params;
  }

  computeAndSaveURLParams() {
    const params = this.computeURLParams();
    const newUrl = window.location.pathname + "?" + params.toString();
    window.history.replaceState({}, "", newUrl);
  }

  /** given a URL, parse it into our relevant pieces */
  parseQueryParams(params = new URLSearchParams(window.location.search)) {
    this.showList = {};
    this.setShowList(params.getAll("ein"));
    this.setHideList(params.getAll("nein"));
    this.setBreadCrumbs(params.getAll("breadCrumbs"));
    this.setKeywordList(params.getAll("keywords"));
    const scale = parseInt(params.get("scale") || "0", 10);
    if (scale) this.setGraphScale(scale);
  }

  /** Place holder for when we actually parse the breadcrumb data, for
   * now it insures we have a starting point.
   */
  processBreadCrumbs() {
    if (Charity.visibleCharities.length === 0) {
      randomPreset();
    }
  }

  /**
   *  Given our URL, match the model. That means seeding the model with desiredVisible nodes
   * from the showList and the search keywords, then turning that off for the hide List items.
   * This is called "placing" a Node, which makes the node visible and then marks the first
   * START_REVEAL incoming and outgoing edges and nodes as implied visible. The ein in the
   * URL actually encodes the number of visilbe upstream and downstream grants, so we can
   * faithfully reproduce some expansion.
   * @param {The URL} params
   * @returns
   */
  matchURL(params = new URLSearchParams(window.location.search)) {
    this.parseQueryParams(params);
    updateStatus("", "green", false);
    console.log("ShowList before processing:", this.getShowList());
    Charity.visibleCharities.forEach((c) => {
      c.desiredVisible = false;
      c.impliedVisible = 0;
    });
    this.getShowList().forEach((ein) => {
      const parts = ein.split(/[:~]/);
      const id = parts[0];
      const ups = parseInt(parts[1] || `${START_REVEAL}`, 10) || START_REVEAL;
      const downs = parseInt(parts[2] || `${START_REVEAL}`, 10) || START_REVEAL;
      const charity = Charity.getCharity(id);
      if (charity && !this.shouldHide(id)) {
        charity.place(ups, downs);
        console.log(
          `Matched EIN ${ein}, placed ${id}, grants out: ${charity.grants.length}, in: ${charity.grantsIn.length}`
        );
      } else {
        console.log(`no match for ${ein} in match`);
      }
    });
    this.getHideList().forEach((ein) => {
      const c = Charity.getCharity(ein);
      if (c) c.desiredVisible = false;
    });
    if (this.getKeywordList().length) {
      const matches = Charity.invisibleCharities.filter(
        (c) =>
          !this.shouldHide(c.id) &&
          c.searchMatch(Object.keys(this.keywords)) &&
          !c.desiredVisible
      );

      const limitedMatches = matches.slice(0, MAX_KEYWORD_NODES);
      if (matches.length > MAX_KEYWORD_NODES) {
        updateStatus(
          `<span>Note: Graph limited to first ${MAX_KEYWORD_NODES} of ${matches.length} matching results</span>`,
          "black",
          false
        );
      }

      limitedMatches.forEach((c) => {
        c.place(1, 1); // avoid sankey explosion
      });
    }
    this.computeImpliedVisibility(null, true, true);
    console.log(
      "Visible Charities after matchURL:",
      Charity.visibleCharities.length,
      Array.from(Charity.visibleCharities).map((c) => c.id)
    );
    return Charity.visibleCharities.size;
  }

  async buildTheWorld(db) {
    if (!db) {
      throw new Error("Database not provided to buildTheWorld");
    }
    if (!db.objectStoreNames.contains(CHARITY_STORE)) {
      throw new Error(`CHARITY_STORE does not exist in database`);
    }
    if (!db.objectStoreNames.contains(METADATA_STORE)) {
      throw new Error(`METADATA_STORE does not exist in database`);
    }

    updateStatus(
      `Building World from ${Object.keys(iso3166_alpha2).length} countries`
    );
    const countryRecords = [];
    let countriesProcessed = 0;

    for (const [fake_ein, data] of Object.entries(iso3166_alpha2)) {
      try {
        if (!fake_ein || !data || !data.name || !data.code) {
          continue;
        }
        const country_pro = {
          filer_ein: fake_ein,
          filer_name: data.name,
          name: data.name,
          xml_name: `The World${data.code}`,
          receipt_amt: 0,
          govt_amt: 0,
          contrib_amt: 1,
          tax_year: "2025",
          org_type: "Foreign Country",
          total_assets: null,
          form_type: null,
          denominator: null,
        };
        Charity.buildCharityFromRow(country_pro);
        countryRecords.push(country_pro);
        countriesProcessed++;
      } catch (error) {
        console.error(`Error processing country ${fake_ein}:`, error);
        continue;
      }
    }

    updateStatus(`Storing ${countriesProcessed} country records`);
    try {
      console.time("storeCountries");
      const storedCount = await storeData(db, CHARITY_STORE, countryRecords);
      console.timeEnd("storeCountries");
      if (storedCount !== countryRecords.length) {
        console.warn(
          `Storage mismatch: Prepared ${countryRecords.length} countries, stored ${storedCount}`
        );
      }
    } catch (error) {
      console.error(`Failed to store countries in IndexedDB:`, error);
      console.log("Continuing despite storage error");
    }

    try {
      console.time("storeMetadata");
      const tx = db.transaction(METADATA_STORE, "readwrite");
      const store = tx.objectStore(METADATA_STORE);
      await Promise.all([
        new Promise((resolve, reject) => {
          const request = store.put({ id: "generated", value: true });
          request.onsuccess = resolve;
          request.onerror = () => reject(request.error);
        }),
        new Promise((resolve, reject) => {
          const request = store.put({
            id: "version",
            value: DATA_FILES.dbVersion,
          });
          request.onsuccess = resolve;
          request.onerror = () => reject(request.error);
        }),
      ]);
      await tx.done;
      console.timeEnd("storeMetadata");
    } catch (error) {
      console.error(`Failed to store metadata:`, error);
      console.log("Continuing despite metadata error");
    }
  }

  /**
   *  A Charity that doesn't have any incoming grants I term a root, with the US Govt being
   * the largest. But it seemed intresting to use them as a starting point. This returns
   * all roots, which can then be sliced.
   * @returns
   */
  getRootCharities() {
    if (Charity.rootCharities) return Charity.rootCharities;
    Charity.rootCharities = Object.values(Charity.charityLookup)
      .filter((c) => c.isRoot && !c.govt_amt && !c.isTerminal)
      .filter((c) => !this.shouldHide(c.id))
      .sort((a, b) => b.grantsTotal - a.grantsTotal);
    return Charity.rootCharities;
  }

  /**
   * Clicking on a node toggles its visibility.
   * If it's one of the ones we've specifically chosen to be visible, its
   * now just a regular node. If it was implied (we know its  visible if it was
   * clicked on) its now desired, which has implications for the implied propogation
   * as it will step 1 node farther down the graph.
   */
  clickNode(event, charity, refreshCallback) {
    console.log(`Clicked node ${charity.id} ${charity.name}`);
    //charity.desiredVisible = !charity.desiredVisible; // Toggle user-driven input
    charity.expandOutflows(NEXT_REVEAL);
    charity.expandInflows(NEXT_REVEAL);
    this.computeImpliedVisibility(charity, true, true); // Compute connected visibility
    this.buildSankeyData(); // Update the graph data
    if (refreshCallback) refreshCallback(); // Always refresh
  }
  /**
   *  Doubleclicking being the "expand/open" gesture means in this
   * case that we force desiredVisible to true, and add more visible grants.
   * Shift double-click is constract, we force desiredToFalse and remove visible grants
   * @param {*} event
   * @param {*} charity
   */
  doubleClickNode(event, charity) {
    if (event.shiftKey) {
      charity.desiredVisible = false;
      charity.compressOutflows(NEXT_REVEAL);
      charity.compressInflows(NEXT_REVEAL);
      console.log(`Compressing ${charity.id} ${charity.name}`);
    } else {
      desiredVisible = true;
      charity.expandOutflows(NEXT_REVEAL);
      charity.expandInflows(NEXT_REVEAL);
      console.log(`Expanding ${charity.id} ${charity.name}`);
    }
    this.computeImpliedVisibility(charity, true, true);
  }

  /**
   * Clicking a grant toggles desired visibility. Which is mostly about
   * affecting implied indirectly.
   * @param {*} event
   * @param {*} grant
   */
  clickGrant(event, grant) {
    grant.desiredVisible = !grant.desiredVisible;
    /*TODO Think about what we should do to grant.filer and grant.grantee  */
    console.log(
      `${grant.desiredVisible ? "Showing" : "Hiding"} grant ${grant.id}`
    );
    this.computeImpliedVisibility(grant.filer, true, false);
    this.computeImpliedVisibility(grant.grantee, false, true);
  }

  /** Double clicking a grant forces both ends of the grang */
  doubleClickGrant(event, grant) {
    if (event.shiftKey) {
      grant.filer.desiredVisible = false;
      grant.grantee.desiredVisible = false;
      console.log(`Hiding nodes for grant ${grant.id}`);
    } else {
      grant.filer.desiredVisible = true;
      grant.grantee.desiredVisible = true;
      grant.desiredVisible = true;
      console.log(`Showing nodes and grant ${grant.id}`);
    }
    this.computeImpliedVisibility(grant.filer, true, false);
    this.computeImpliedVisibility(grant.grantee, false, true);
  }

  /**
   * Because its natural to want to expand just one direction visually we draw
   * these little hats on either side of a node, clicking on the upstream hat
   * expands that way, clicking on the downstream that way.
   */
  handleUpClick(event, charity, refreshCallback) {
    console.log(`Expanding inflows for ${charity.id} ${charity.name}`);
    charity.desiredVisible = true;
    charity.expandInflows(NEXT_REVEAL);
    this.computeImpliedVisibility(charity, true, true);
    this.buildSankeyData();
    if (refreshCallback) refreshCallback();
  }

  handleDownClick(event, charity, refreshCallback) {
    console.log(`Expanding outflows for ${charity.id} ${charity.name}`);
    charity.desiredVisible = true;
    charity.expandOutflows(NEXT_REVEAL);
    this.computeImpliedVisibility(charity, true, true);
    this.buildSankeyData();
    if (refreshCallback) refreshCallback();
  }

  /**
   * Workhorse method for doing all the complicated visiblity shit.
   * If rootCharity is null, we're going to reset everything, start with the
   * desired nodes, then propogate one step from there.
   * If rootCharity is set, we're doing an incremental prop, just need to do
   * one step.
   * @param {*} rootCharity Starting point, or null for all desiredVisible nodes
   * @param {*} inflowsOnly Only go left
   * @param {*} outflowsOnly Only go right
   */
  computeImpliedVisibility(
    rootCharity = null,
    inflowsOnly = false,
    outflowsOnly = false
  ) {
    Charity.desiredCharities.forEach((c) => {
      c.impliedVisible = 1;
    });
    Grant.desiredGrants.forEach((g) => {
      g.impliedVisible = g.desiredVisible;
    });

    /**
     * Ok, so two cases: rootCharity is null, loop over desiredCharities and prop
     * the implied up and down based on existing state. i.e. if no grants are
     * visibile, make START_REVEAL visible, if some are, make NEXT_REVEAL visible.
     *
     * If root charity is not null, as above, but for one charity.
     *
     * I should refactor this to break out ops.
     * Have to go review the existing operations on Charities though.
     */

    if (rootCharity) {
      // incremental case
      if (inflowsOnly) {
        for (const grant of rootCharity.invisibleGrantsIn.slice(
          0,
          NEXT_REVEAL
        )) {
          if (!this.shouldHide(grant.filer.ein)) {
            grant.filer.impliedVisible++;
            grant.impliedVisible = true;
            console.log(`  Inflow filer ${grant.filer.ein} set visible`);
          }
        }
      }

      if (outflowsOnly) {
        for (const grant of rootCharity.invisibleGrants.slice(0, NEXT_REVEAL)) {
          if (!this.shouldHide(grant.grantee.ein)) {
            grant.grantee.impliedVisible++;
            grant.impliedVisible = true;
            console.log(`  Outflow grantee ${grant.grantee.ein} set visible`);
          }
        }
      }
    } else {
      // brute force whole data model case
      const seeds = Charity.desiredCharities; //handy accessor
      for (const charity of seeds) {
        for (const grant of charity.looseVisibleGrants) {
          if (
            (grant.desiredVisible || charity.desiredVisible) &&
            !this.shouldHide(grant.grantee.ein)
          ) {
            grant.grantee.impliedVisible++;
            grant.impliedVisible = true;
            if (!grant.grantee.isGov || grant.grantee.desiredVisible) {
              console.log(`  Outflow grantee ${grant.grantee.ein} set visible`);
            }
          }
        }
        for (const grant of charity.looseVisibleGrantsIn) {
          if (
            (grant.desiredVisible || charity.desiredVisible) &&
            !this.shouldHide(grant.filer.ein)
          ) {
            grant.filer.impliedVisible++;
            grant.impliedVisible = true;
            if (!grant.filer.isGov || grant.filer.desiredVisible) {
              console.log(`  Inflow filer ${grant.filer.ein} set visible`);
            }
          }
        }
      }
    }
    this.computeAndSaveURLParams();
  }

  /**
   * Make sure the sankey data is clean
   */
  buildCleanSankeyData(maxallowed = MAX_NODES) {
    function filterHiddenGrant(grant) {
      if (viewModel.shouldHide(grant.filer.ein)) return true;
      if (viewModel.shouldHide(grant.grantee.ein)) return true;
      return false;
    }
    let maxSeeds = maxallowed;
    this.renderData.links = Grant.visibleGrants.filter(
      (g) => !filterHiddenGrant(g)
    );
    this.renderData.nodes = Array.from(Charity.visibleCharities).filter(
      (node) => !this.shouldHide(node.ein)
    );
    const nodeSet = new Set();
    const missingSet = new Set();
    this.renderData.nodes.forEach((node) => nodeSet.add(node.ein));
    this.renderData.links.forEach((grant) => {
      if (!nodeSet.has(grant.filer.ein)) missingSet.add(grant.filer.ein);
      if (!nodeSet.has(grant.grantee.ein)) missingSet.add(grant.grantee.ein);
    });
    // add missing nodes
    for (const ein of missingSet) {
      console.log(`bCSD: adding missing node ${ein}`);
      this.renderData.nodes.push(Charity.getCharity(ein));
    }
  }

  /**
   * Build the data we need for the Sankey. The model is close enough to what it needs that we
   * can draw direclty from the data, though we do have to undo the fact that the sankey code
   * replaces source as EIN with source as object.
   * @returns
   */
  buildSankeyData() {
    let maxSeeds = MAX_NODES;
    this.renderData = { nodes: [], links: [] };
    //this.computeImpliedVisibility();
    this.buildCleanSankeyData(maxSeeds);
    /*while (this.renderData.nodes.length > MAX_NODES && maxSeeds > 5) {
      updateStatus(`Too Many Nodes, reducing to ${maxSeeds} seeds`);
      Charity.visibleCharities // reverse sort so largest flows larger get kept
        .sort(
          (a, b) =>
            b.grantsTotal + b.grantsInTotal - (a.grantsTotal + b.grantsInTotal)
        )
        .slice(maxSeeds)
        .forEach((c) => c.clearVisibility());
      this.computeImpliedVisibility(null, true, true);
      this.renderData.nodes = Charity.visibleCharities;
      this.renderData.links = Grant.visibleGrants;
      this.buildCleanSankeyData(maxSeeds);
      maxSeeds /= 2; // try with half as much next time.
    }*/
    console.log(
      `Sankey Data - Nodes: ${this.renderData.nodes.length}, Links: ${this.renderData.links.length}`
    );
    return this.renderData;
  }
  async processGrantsZipFile({ status, zipFile, tsvFile, grantType }) {
    try {
      console.log(`Starting ${zipFile} at ${new Date().toISOString()}`);
      const db = await initDB();
      console.time("Starting grant fetch" + zipFile);

      const records = await fetchAndStoreTSV(db, {
        zipFile,
        tsvFile,
        type: "grants",
        grantType,
        status,
      });
      console.timeEnd("Starting grant fetch" + zipFile);

      console.time("Starting grant store" + zipFile);
      await storeData(db, GRANT_STORE, records);
      console.timeEnd("Starting grant store" + zipFile);
      let totalGrantsRows = records.length;

      for (let i = 0; i < records.length; i += CHUNK_SIZE) {
        const chunk = records.slice(i, i + CHUNK_SIZE);
        for (const row of chunk) {
          Grant.loadGrantRow(row, grantType);
        }
        await new Promise((resolve) => setTimeout(resolve, 0));
        updateStatus(
          `${status}: ${formatNumber(totalGrantsRows)} grants processed`
        );
      }

      console.log(`Finished ${zipFile}, totalGrantsRows: ${totalGrantsRows}`);
      updateStatus(
        `Processed ${formatNumber(totalGrantsRows)} grants for ${tsvFile}`,
        "green",
        false
      );
      return { rows: totalGrantsRows, grantType };
    } catch (err) {
      console.error(`Error processing ${zipFile}:`, err);
      updateStatus(`Error processing ${tsvFile}: ${err.message}`, "red", false);
      throw err;
    }
  }
  async loadData() {
    console.log(`Starting loadData at ${new Date().toISOString()}`);
    updateStatus("Loading data...");
    this.dataReady = false;
    Charity.charityLookup = {};
    Grant.grantLookup = {};

    try {
      console.log("Initializing IndexedDB...");
      this.db = await initDB();
      console.log("IndexedDB initialized");
      console.log(
        `Database stores: ${Array.from(this.db.objectStoreNames).join(", ")}`
      );

      if (await hasValidData(this.db)) {
        updateStatus("Loading from local storage...");
        console.time("loadCharitiesFromDB");
        const charities = await fetchLocalData(this.db, CHARITY_STORE);
        console.timeEnd("loadCharitiesFromDB");
        console.log(`Fetched ${charities.length} charities from IndexedDB`);

        let chunkSize = 10000;
        const TARGET_CYCLE_TIME = 500;
        const MIN_CHUNK_SIZE = 2500;
        const MAX_CHUNK_SIZE = 40000;
        let processedCharities = 0;

        let i = 0;
        while (i < charities.length) {
          const startTime = performance.now();
          console.time(`processCharities-${i}`);
          const chunk = charities.slice(i, i + chunkSize);
          for (const row of chunk) {
            try {
              Charity.buildCharityFromRow(row);
              processedCharities++;
            } catch (error) {
              console.error(`Error processing charity row ${i}:`, error);
              continue;
            }
          }
          console.timeEnd(`processCharities-${i}`);
          updateStatus(
            `Loading charities: ${formatNumber(
              Object.keys(Charity.charityLookup).length
            )} processed`
          );
          await new Promise((resolve) => setTimeout(resolve, 0));

          const cycleTime = performance.now() - startTime;
          if (
            cycleTime < TARGET_CYCLE_TIME * 0.5 &&
            chunkSize < MAX_CHUNK_SIZE
          ) {
            chunkSize = Math.min(chunkSize * 2, MAX_CHUNK_SIZE);
          } else if (
            cycleTime > TARGET_CYCLE_TIME * 2 &&
            chunkSize > MIN_CHUNK_SIZE
          ) {
            chunkSize = Math.max(Math.floor(chunkSize / 2), MIN_CHUNK_SIZE);
          }
          i += chunk.length;
        }

        console.time("loadGrantsFromDB");
        const grants = await fetchLocalData(this.db, GRANT_STORE);
        console.timeEnd("loadGrantsFromDB");
        console.log(`Fetched ${grants.length} grants from IndexedDB`);

        chunkSize = 10000;
        let processedGrants = 0;
        i = 0;
        while (i < grants.length) {
          const startTime = performance.now();
          console.time(`processGrants-${i}`);
          const chunk = charities.slice(i, i + chunkSize);
          for (const row of chunk) {
            try {
              Grant.loadGrantRow(row, row.grantType);
              processedGrants++;
            } catch (error) {
              console.error(`Error processing grant row ${i}:`, error);
              continue;
            }
          }
          console.timeEnd(`processGrants-${i}`);
          updateStatus(
            `Loading grants: ${formatNumber(
              Object.keys(Grant.grantLookup).length
            )} processed`
          );
          await new Promise((resolve) => setTimeout(resolve, 0));

          const cycleTime = performance.now() - startTime;
          if (
            cycleTime < TARGET_CYCLE_TIME * 0.5 &&
            chunkSize < MAX_CHUNK_SIZE
          ) {
            chunkSize = Math.min(chunkSize * 2, MAX_CHUNK_SIZE);
          } else if (
            cycleTime > TARGET_CYCLE_TIME * 2 &&
            chunkSize > MIN_CHUNK_SIZE
          ) {
            chunkSize = Math.max(Math.floor(chunkSize / 2), MIN_CHUNK_SIZE);
          }
          i += chunk.length;
        }

        updateStatus(
          `Loaded ${formatNumber(
            Object.keys(Charity.charityLookup).length
          )} charities, ${formatNumber(
            Object.keys(Grant.grantLookup).length
          )} grants from local storage`,
          "green",
          false
        );
      } else {
        updateStatus("Fetching data from server...");
        try {
          console.time("buildTheWorld");
          await this.buildTheWorld(this.db);
          console.timeEnd("buildTheWorld");

          console.time("loadTSVFiles");
          await fetchAndStoreTSV(this.db, DATA_FILES.files);
          console.timeEnd("loadTSVFiles");

          console.time("buildGovCharity");
          await this.buildGovCharity(this.db);
          console.timeEnd("buildGovCharity");

          console.time("storeMetadata");
          const tx = this.db.transaction(METADATA_STORE, "readwrite");
          const store = tx.objectStore(METADATA_STORE);
          await Promise.all([
            new Promise((resolve, reject) => {
              const request = store.put({ id: "generated", value: true });
              request.onsuccess = resolve;
              request.onerror = () => reject(request.error);
            }),
            new Promise((resolve, reject) => {
              const request = store.put({
                id: "version",
                value: DATA_FILES.dbVersion,
              });
              request.onsuccess = resolve;
              request.onerror = () => reject(request.error);
            }),
          ]);
          await tx.done;
          console.timeEnd("storeMetadata");

          updateStatus(
            `Loaded ${formatNumber(
              Object.keys(Charity.charityLookup).length
            )} charities, ${formatNumber(
              Object.keys(Grant.grantLookup).length
            )} grants from server`,
            "green",
            false
          );

          await new Promise((resolve) => setTimeout(resolve, 0));
        } catch (error) {
          console.error("Error during server fetch:", error);
          updateStatus(
            `Error during server fetch: ${error.message}`,
            "red",
            false
          );
          throw error;
        }
      }

      console.log(
        `Grants Net ${formatNumber(Object.keys(Grant.grantLookup).length)}`
      );
      updateStatus("USG & NGOs & grants loaded", "black", false);
      this.dataReady = true;
      console.log(`loadData completed at ${new Date().toISOString()}`);
      return Object.keys(Grant.grantLookup).length;
    } catch (err) {
      console.error("Error in loadData:", err);
      updateStatus(`Error loading data: ${err.message}`, "red", false);
      this.dataReady = false;
      return Object.keys(Grant.grantLookup).length;
    }
  }
  /*
   * So the NGO data we're parsing calls out how much money each NGO is getting from the Government.
   * That's treated as an implied grant from a virtual NGO, so we generate that by scanning all the
   * NGOs and creating that data. This is technically a model function, but its here now and I'm
   * not religious about any kind of code architecture enough to bother moving it.
   * @returns
   */
  async buildGovCharity(db) {
    if (!db) {
      throw new Error("Database not provided to buildGovCharity");
    }
    updateStatus(
      `Building US Govt from ${
        Object.keys(Charity.charityLookup).length
      } charities`
    );
    const gov_ein = this.GOV_EIN;
    const gov_proto = {
      filer_ein: gov_ein,
      filer_name: "US Government",
      name: "US Government",
      xml_name: "The Beast",
      contrib_amt: 4.6e12,
      tax_year: "2025",
      org_type: "USG",
      receipt_amt: 0,
      govt_amt: 0,
      total_assets: null,
      form_type: null,
      denominator: null,
    };

    // Register US Government charity in lookup first
    try {
      Charity.buildCharityFromRow(gov_proto);
    } catch (error) {
      console.error(
        `Failed to register US Government charity in lookup:`,
        error
      );
      throw error;
    }

    const govChar = Charity.getCharity(gov_ein);
    let govGrants = 0;
    let processList = Object.values(Charity.charityLookup)
      .filter((c) => c.govt_amt)
      .sort((a, b) => b.govt_amt - a.govt_amt);
    const govCount = processList.length;
    let govTotal = 0;
    const totalGrants = processList.reduce((sum, c) => sum + c.govt_amt, 0);
    const grantRecords = [];

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
          const grant = {
            id: `${filer}~${grantee}`,
            filer_ein: filer,
            grant_ein: grantee,
            amt: amt,
            grantType: "gov",
          };
          Grant.loadGrantRow(grant, "gov");
          grantRecords.push(grant);
          govTotal += amt;
        }
      });
      updateStatus(
        `<span>Gov processing: ${formatNumber(
          Object.keys(Charity.charityLookup).length
        )} charities, ${formatNumber(
          Object.keys(Grant.grantLookup).length
        )} grants</span><span class="text-[13px] opacity-60">${Math.round(
          (govGrants / govCount) * 100
        )}% ${Math.round(
          (govTotal / totalGrants) * 100
        )}% ${govGrants}/${govCount} ${formatNumber(govTotal)}/${formatNumber(
          totalGrants
        )} complete</span>`
      );
      await new Promise((resolve) => setTimeout(resolve, 0));
      chunk = processList.slice(0, CHUNK_SIZE);
      processList = processList.slice(CHUNK_SIZE);
    }

    try {
      console.time("storeGovCharity");
      await storeData(db, CHARITY_STORE, [gov_proto]);
      console.timeEnd("storeGovCharity");
    } catch (error) {
      console.error(`Failed to store government charity:`, error);
      throw error;
    }

    try {
      console.time("storeGovGrants");
      await storeData(db, GRANT_STORE, grantRecords);
      console.timeEnd("storeGovGrants");
    } catch (error) {
      console.error(`Failed to store government grants:`, error);
      throw error;
    }

    updateStatus(
      `<span>Gov charity complete: ${formatNumber(
        Object.keys(Charity.charityLookup).length
      )} charities, ${formatNumber(
        Object.keys(Grant.grantLookup).length
      )} grants</span><span class="text-[13px] opacity-60">${
        govChar.grants.length
      } generated, ${formatNumber(govTotal)}</span>`
    );
    govChar.isGov = true;
    this.GOV_NODE = govChar;
    GOV_NODE = govChar;
    return govChar;
  }

  /**
   * So I had to pick smothign ast the starting point, so I place the 5 largest root notes.
   */
  loadDefaultData() {
    const usGov = this.GOV_NODE;
    updateStatus("placing US Government");
    usGov.desiredVisible = true;
    usGov.expandOutflows(TOP_N_INITIAL);
    updateStatus(
      `USG placed adding top ${TOP_N_INITIAL} roots`,
      "green",
      false
    );
    this.getRootCharities()
      .slice(1, TOP_N_INITIAL) // skip USG, already placed.
      .forEach((c) => {
        c.desiredVisible = true;
        c.expandOutflows(START_REVEAL);
      });
    this.computeImpliedVisibility(null, true, true);
  }

  /** So the sankey was messing with the data, so we have to reset the link data post
   * render.
   */
  cleanAfterRender() {
    if (this.renderData && this.renderData.links) {
      this.renderData.links.forEach((link) => link.resetSourceTarget());
    }
  }
}

/**
 * Class to hold an NGO.
 */
export class Charity {
  /** charities are stored in an object by EIN for quick lookup */
  static charityLookup = {};
  static _desiredCharities = new Set();
  static _visibleCharities = new Set();

  /** Basic methods for puting charites into and out of the lookup */
  static getCharity(ein) {
    if (!ein) return null;
    const parts = ein.split(/[:~]/);
    return Charity.charityLookup[parts[0]];
  }

  static registerCharity(ein, c) {
    Charity.charityLookup[ein] = c;
  }

  /** accessors are convenient */
  static get visibleCharities() {
    return Charity._visibleCharities;
    //return Object.values(Charity.charityLookup).filter((c) => c.isVisible);
  }

  static get invisibleCharities() {
    return Object.values(Charity.charityLookup).filter((c) => !c.isVisible);
  }

  static get impliedCharities() {
    return Object.values(Charity.charityLookup).filter(
      (c) => c.impliedVisible > 0
    );
  }

  static get desiredCharities() {
    return Charity._desiredCharities;
    //return Object.values(Charity.charityLookup).filter((c) => c.desiredVisible);
  }

  static get getCharityCount() {
    return Object.keys(Charity.charityLookup).length;
  }

  /**
   * Clear all caches
   */
  static disorganzeAll() {
    Charity.organizedSet.forEach((c) => (c.isOrganized = false));
  }

  /**
   * Factory for building one from a data file row.
   * @param {} row
   * @returns
   */

  static buildCharityFromRow(row) {
    function titleCase(str) {
      return str
        .toLowerCase()
        .split(" ")
        .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
        .join(" ");
    }
    const ein = row.filer_ein;
    if (!ein) {
      console.warn(`Skipping charity row: missing filer_ein`, row);
      return;
    }
    const name = row.name || row.filer_name || "Unknown Charity";
    let rAmt = parseInt(row.receipt_amt || "0", 10) || 0;
    let gAmt = parseInt(row.govt_amt || "0", 10) || 0;
    let cAmt = parseInt(row.contrib_amt || "0", 10) || 0;
    return new Charity({
      ein,
      name: titleCase(name),
      xml_name: row.xml_name,
      receipt_amt: rAmt,
      govt_amt: gAmt,
      contrib_amt: cAmt,
      tax_year: row.tax_year || null,
      org_type: row.org_type || null,
      total_assets: row.total_assets || null,
      form_type: row.form_type || null,
      denominator: row.denominator || null,
      row,
    });
  }

  static TSV_MANUAL_COLUMNS = [
    "tax_year",
    "org_type",
    /*"comp_pct",
    "comp_ptile",
    "travel_pct",
    "travel_ptile",
    "conferences_pct",
    "conferences_ptile",
    "grants_pct",
    "grants_ptile",
    "foreign_expenses_pct",
    "foreign_expenses_ptile",
    "grift_ratio",*/
    "total_assets",
    "form_type",
    "denominator",
    /*"foreign_office",
    "foreign_expenses",
    "grift",*/
  ];
  /** It is what it is. */
  constructor({
    ein,
    name,
    xml_name,
    govt_amt = 0,
    contrib_amt = 0,
    receipt_amt = 0,
    grants = [],
    grantsIn = [],
    desiredVisible = false,
    isOrganized = false,
    row,
  }) {
    this.id = ein; // these 3 are interchangeable
    this.ein = ein;
    this.filer_ein = ein;
    this.name = name;
    this.xml_name = xml_name;
    this.receipt_amt = receipt_amt;
    this.govt_amt = govt_amt;
    this.contrib_amt = contrib_amt;
    this.grants = grants;
    this.grantsIn = grantsIn;
    this._desiredVisible = desiredVisible;
    this._impliedVisible = 0;
    this.isOrganized = isOrganized;
    this.isGov = false;
    this.expanded = false;
    this._valueCache = {};
    this.sourceLinks = [];
    this.targetLinks = [];
    Charity.loadExtraData(this, row);
    Charity.registerCharity(ein, this);
  }

  static loadExtraData(obj, raw_row) {
    for (const mkey of Charity.TSV_MANUAL_COLUMNS) {
      if (mkey in raw_row) {
        // Convert numeric fields
        if (
          [
            "tax_year",
            "comp_pct",
            "comp_ptile",
            "travel_pct",
            "travel_ptile",
            "conferences_pct",
            "conferences_ptile",
            "grants_pct",
            "grants_ptile",
            "foreign_expenses_pct",
            "foreign_expenses_ptile",
            "grift_ratio",
            "total_assets",
            "denominator",
            "foreign_expenses",
            "grift",
          ].includes(mkey)
        ) {
          const value = parseFloat(raw_row[mkey]);
          obj[mkey] = isNaN(value) ? null : value;
        }
        // Convert boolean field
        else if (mkey === "foreign_office") {
          const value =
            typeof raw_row[mkey] === "string"
              ? raw_row[mkey].toLowerCase()
              : raw_row[mkey];
          obj[mkey] =
            value === "true" ||
            value === "yes" ||
            value === "1" ||
            value === true;
        }
        // Strings
        else {
          obj[mkey] = raw_row[mkey];
        }
      } else {
        // Handle missing fields (default or error)
        obj[mkey] = null; // Or throw new Error(`Missing field: ${mkey}`);
      }
    }
  }

  /**
   * Note the OR here, a node is visible if its impliedVisible by a desired node
   * or if it itself is desired.
   */
  get isVisible() {
    return this.impliedVisible > 0 || this.desiredVisible;
  }

  /**
   * We use this when we're trying to pare down a graph
   */

  clearVisibility() {
    this.desiredVisible = false;
    this.impliedVisible = 0;
    this.grantsIn.forEach((g) => {
      g.impliedVisible = false;
      g.desiredVisible = false;
    });
    this.grants.forEach((g) => {
      g.impliedVisible = false;
      g.desiredVisible = false;
    });
  }

  get desiredVisible() {
    return this._desiredVisible;
  }

  set desiredVisible(value) {
    if (this._desiredVisible !== value) {
      this._desiredVisible = value;
      this.isOrganized = false;
      if (value) Charity._desiredCharities.add(this);
      else Charity._desiredCharities.delete(this);
    }
  }

  get impliedVisible() {
    //if (!this.isOrganized) this.organize(); not necessary
    return this._impliedVisible;
  }

  set impliedVisible(value) {
    if (this._impliedVisible !== value) {
      this._impliedVisible = value;
      this.isOrganized = false;
      if (value || this.desiredVisible) Charity._visibleCharities.add(this);
      else Charity._visibleCharities.delete(this);
    }
  }

  /**
   * Canonically EIN form
   */
  get longEIN() {
    return `${this.ein.slice(0, 2)}-${this.ein.slice(2)}`;
  }

  get propublica990Id() {
    const matches = this.xml_name.match(/(\d*)_public.xml/);
    return matches ? `${matches[1]}` : "";
  }

  /** can only grow to the left if there are grants to show */
  get canExpandInflows() {
    return this.invisibleGrantsIn.length > 0;
  }

  /** can only grow to the right if there are grants to show */
  get canExpandOutflows() {
    return this.invisibleGrants.length > 0;
  }

  /** can only suck grants back in if there are grants to suck back in */
  get canCompressInflows() {
    return this.visibleGrantsIn.length > 0;
  }

  /** can only suck grants back in if there are grants to suck back in */
  get canCompressOutflows() {
    return this.visibleGrants.length > 0;
  }

  /** an NGO that doesn't make any grants of its own is terminal, either because
   * they've sucked up the money into "adminstraiton" or because they just buy stuff
   * on their own, which we have no way of seeing.
   */
  get isTerminal() {
    return this.grants.length === 0;
  }
  /**
   * An NGO that doesn't get money from the GOVT or another charity is a root.
   */
  get isRoot() {
    return this.grantsIn.length === 0;
  }

  /**
   * Ok, a whole bunch of caching accessors on the various kinds of relation ships and
   * the summed values they have. The only thing tricky is that
   * scaled values are stored with the POWER_LAW they were calculated under
   * so that if the POWER_LAW changes, the values will be automatically
   * updated. Also stored based on the number of visible grants for a similar reason.
   */
  get logGrantsTotal() {
    const cacheKey = `logGrantTotal-${viewModel.POWER_LAW}-${this.grants.length}`;
    if (this._valueCache[cacheKey]) return this._valueCache[cacheKey];
    return (this._valueCache[cacheKey] = scaleValue(this.grantsTotal));
  }

  get logGrantsInTotal() {
    const cacheKey = `logGrantsInTotal-${viewModel.POWER_LAW}-${this.grantsIn.length}`;
    if (this._valueCache[cacheKey]) return this._valueCache[cacheKey];
    return (this._valueCache[cacheKey] = scaleValue(this.grantsInTotal));
  }

  get grantsLogTotal() {
    const vgrants = this.visibleGrants; // use all grants for scaling now
    const cacheKey = `grantsLogTotal-${viewModel.POWER_LAW}-${vgrants.length}`;
    if (this._valueCache[cacheKey]) return this._valueCache[cacheKey];
    if (vgrants.length)
      return (this._valueCache[cacheKey] = vgrants.reduce(
        (total, g) => total + g.value,
        0
      ));
    return 0;
  }

  get grantsInLogTotal() {
    const vgrants = this.visibleGrantsIn; // use all grants for scaling
    const cacheKey = `grantsInLogTotal-${viewModel.POWER_LAW}-${vgrants.length}`;
    if (this._valueCache[cacheKey]) return this._valueCache[cacheKey];
    if (vgrants.length)
      return (this._valueCache[cacheKey] = vgrants.reduce(
        (total, g) => total + g.value,
        0
      ));
    return 0;
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

  get visibleGrantsInTotal() {
    const cacheKey = `visibleGrantsInTotal`;
    if (this._valueCache[cacheKey]) return this._valueCache[cacheKey];
    return (this._valueCache[cacheKey] = this.visibleGrantsIn.reduce(
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

  /**
   * A grant can only consider itself visible if both its nodes are
   * visible.
   */

  /**
   * Grants only consider themselves visible if both ends are visible, but
   * when propogating visibility, we just need to know if the grants
   * would like to be visible.
   */
  get looseVisibleGrants() {
    if (!this.isOrganized) this.organize();
    const cacheKey = `looseVisibleGrants`;
    if (this._valueCache[cacheKey]) return this._valueCache[cacheKey];
    return (this._valueCache[cacheKey] = this.grants.filter(
      (g) => g.isLooseVisible
    ));
  }

  get looseVisibleGrantsIn() {
    if (!this.isOrganized) this.organize();
    const cacheKey = `looseVisibleGrantsIn`;
    if (this._valueCache[cacheKey]) return this._valueCache[cacheKey];
    return (this._valueCache[cacheKey] = this.grantsIn.filter(
      (g) => g.isLooseVisible
    ));
  }

  get visibleGrants() {
    if (!this.isOrganized) this.organize();
    const cacheKey = `visibleGrants`;
    if (this._valueCache[cacheKey]) return this._valueCache[cacheKey];
    return (this._valueCache[cacheKey] = this.grants.filter(
      (g) => g.isVisible
    ));
  }

  get invisibleGrants() {
    if (!this.isOrganized) this.organize();
    const cacheKey = `invisibleGrants`;
    if (this._valueCache[cacheKey]) return this._valueCache[cacheKey];
    return (this._valueCache[cacheKey] = this.grants.filter(
      (g) => !g.isVisible
    ));
  }

  get visibleGrantsIn() {
    if (!this.isOrganized) this.organize();
    const cacheKey = `visibleGrantsIn`;
    if (this._valueCache[cacheKey]) return this._valueCache[cacheKey];
    return (this._valueCache[cacheKey] = this.grantsIn.filter(
      (g) => g.isVisible
    ));
  }

  get invisibleGrantsIn() {
    if (!this.isOrganized) this.organize();
    const cacheKey = `invisibleGrantsIn`;
    if (this._valueCache[cacheKey]) return this._valueCache[cacheKey];
    return (this._valueCache[cacheKey] = this.grantsIn.filter(
      (g) => !g.isVisible
    ));
  }

  get hasVisibleGrants() {
    return this.visibleGrants.length > 0;
  }

  /** accessor to trash the cache */
  set isOrganized(value) {
    if (this._isOrganized !== value) {
      this._valueCache = {};
      this._isOrganized = value;
      this.clearValueCache(); // force regen
    }
  }

  /**
   * Accessors for maingining the root lists, and managing the cache
   */
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
    const grantee = Charity.charityLookup[g.grant_ein];
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
      this._origOut += grant.amt;
    }
  }

  removeGrantIn(grant) {
    const index = this.grantsIn.indexOf(grant);
    if (index !== -1) {
      this.grantsIn.splice(index, 1);
      this.isOrganized = false;
      this._origIn += grant.amt;
    }
  }

  clearValueCache() {
    this._valueCache = {};
  }

  /** Part of organizing is keeping the grants sorted */
  organize() {
    if (!this.isOrganized) {
      this.grants.sort((a, b) => b.amt - a.amt);
      this.grantsIn.sort((a, b) => b.amt - a.amt);
      this.isOrganized = true;
    }
  }

  /** Mark a grant circular have to tell both sides*/
  static circularGrant(g) {
    g.grantee.circleGrant(g);
    g.filer.circleGrant(g);
    Grant.unregisterGrant(g);
  }

  /** nice when debugging */
  get grantsTotalString() {
    return formatNumber(this.grantsTotal);
  }

  get invisibleTotalString() {
    return formatNumber(this.invisibleGrantsTotal);
  }

  /**
   * A-B-A is a simple circle which we can handle in a filter
   * Might as well.
   */
  simpleCircular() {
    return this.grants.filter((g1) =>
      g1.grantee.grants.some((g2) => g2.grant_ein === this.ein)
    );
  }

  /** This may be DEAD CODE now */
  handleClick(e, count = -1) {
    if (e.altKey) return this.tunnelNode(e);
    if (e.metaKey || this.isTerminal) {
      console.log(`Hiding ${this.id} ${this.name}`);
      this.hide();
      return false;
    }
    console.log(`Expanding ${this.id} ${this.name}`);
    this.expandDown(NEXT_REVEAL);
    this.expandUp(NEXT_REVEAL);
    return true;
  }

  /** This should be DEAD CODE now */
  handleGrantClick(e, g) {
    g.filer.handleClick(e, -1);
    g.grantee.handleClick(e, -1);
  }

  /**
   * Swim upstream, making grants visible
   * @param {*} count
   */
  expandInflows(count = NEXT_REVEAL) {
    if (!count || count == "0") return;
    const grantsToReveal = this.invisibleGrantsIn.slice(0, count); // count largest
    grantsToReveal.forEach((grant) => {
      grant.desiredVisible = true; // propogates both directions
    });
    viewModel.resetEIN(this.ein);

    this.isOrganized = false;
  }

  /** make some of the upstream grands invisible */
  // do we need a reference count instead of a simple flag?
  compressInflows(count = NEXT_REVEAL) {
    /*const grantsToHide = this.visibleGrantsIn.slice(-count); // count smallest
    grantsToHide.forEach((grant) => {
      grant.clearVisibility();
    });
    viewModel.resetEIN(this.ein);

    this.isOrganized = false;*/
    let newCount = this.visibleGrantsIn.length - count;
    if (newCount < 0) newCount = 0;
    viewModel.addToShowList(
      `${this.ein}~${newCount}~${this.visibleGrants.length}}`
    ); // match URL will do this for us.
  }

  /**
   * Expand downwards
   * @param {*} count
   */
  expandOutflows(count = NEXT_REVEAL) {
    if (!count || count == "0") return;
    const grantsToReveal = this.invisibleGrants.slice(0, count);
    console.log(
      `Expanding ${grantsToReveal.length} outflows for ${this.id} (total invisible: ${this.invisibleGrants.length})`
    );
    grantsToReveal.forEach((grant) => {
      grant.desiredVisible = true;
      console.log(
        `  Grant ${grant.id} set visible, grantee ${grant.grantee.id} set visible`
      );
    });
    viewModel.resetEIN(this.ein);
    this.isOrganized = false;
  }

  compressOutflows(count = NEXT_REVEAL) {
    /*const grantsToHide = this.visibleGrants.slice(-count);
    grantsToHide.forEach((grant) => {
      grant.clearVisibility();
    });
    this.isOrganized = false;*/
    let newCount = this.visibleGrants.length - count;
    if (newCount < 0) newCount = 0;
    viewModel.addToShowList(
      `${this.ein}~${this.visibleGrantsIn.length}~${newCount}`
    ); // match URL will do this for us.
  }

  /**
   * These manipulate the Hide and HostLists too
   */

  show() {
    this.desiredVisible = true;
    viewModel.removeFromHideList(this.id);
    viewModel.addToBreadCrumbs(`Show|${this.id}`);
  }

  hide() {
    viewModel.addToBreadCrumbs(`Hide|${this.id}`);
    viewModel.addToHideList(this.id);
    this.desiredVisible = false;
    this.grantsIn.forEach((g) => this.clearVisibility());
    this.grants.forEach((g) => this.clearVisibility());
  }

  /**
   *
   * @returns EIN:numberIncoming:numberOutgoing
   */
  URLPiece() {
    if (!this.desiredVisible) return null;
    return `${this.ein}~${this.visibleGrantsIn.length}~${this.visibleGrants.length}`;
  }

  /**
   * DO these search terms match?
   * @param {*} keywords
   * @returns
   */
  searchMatch(keywords) {
    const lowerStr = this.name.toLowerCase();
    return keywords
      .map((kw) => kw.trim().toLowerCase())
      .filter((kw) => kw !== "")
      .some((kw) => lowerStr.includes(kw));
  }

  /**
   *   So at one point I was thinking instead of constantly extending the graph
   * I'd provide a way that it would just jump to a new starting point.
   */
  tunnelNode() {
    const newParams = new URLSearchParams(); //empty
    newParams.add("ein", this.ein);
    viewModel.matchURL(newParams);
  }

  /**
   *  "placing a node" means making it desired, and making sure there
   * are the matching number of visible grants.
   * @param {*} upCount
   * @param {*} downCount
   */
  place(upCount = START_REVEAL, downCount = START_REVEAL) {
    this.desiredVisible = true;
    this.expandOutflows(downCount);
    this.expandInflows(upCount);
    this.organize();
    this.expanded = true;
    console.log(
      `Placed ${this.id}: ${this.visibleGrants.length} outflows visible, ${this.invisibleGrants.length} outflows invisible, ${this.visibleGrantsIn.length} inflows visible`
    );
  }

  get orgShort() {
    if (!this.org_type) return "n/a";
    if (this.ein === "001") return "US Government";
    if (this.ein.length == 3) return "Country";
    const orgLookup = ORGANIZATION_TYPES[this.org_type];
    if (!orgLookup) return "???";

    return `${orgLookup.shortDescription} ${this.org_type.replace("501", "")}`;
  }
  /**
   * Technically a VM responsibility, but we just do it here.
   * @returns
   */
  toolTipText() {
    let outFlows = this.grantsTotal
      ? `\ngrants out: $${formatNumber(this.grantsTotal)}`
      : `\nout: N/A`;
    let inFlows = this.grantsInTotal
      ? `\ngrants in: $${formatNumber(this.grantsInTotal)}`
      : `\nin: N/A`;
    return `${this.name}\n${this.orgShort}\n${this.longEIN}${inFlows}${outFlows}`;
  }

  get griftRating() {
    function griftGrade(ptile) {
      if (ptile >= 95) return "F-";
      if (ptile >= 90) return "F";
      if (ptile >= 80) return "D";
      if (ptile >= 70) return "C";
      if (ptile >= 60) return "B";
      if (ptile >= 50) return "A";
      return "-";
    }
    const ptileFields = [
      this.comp_ptile,
      this.travel_ptile,
      this.conferences_ptile,
      this.grants_ptile,
      this.foreign_expenses_ptile,
      this.grift_ratio,
    ];
    const validPtiles = ptileFields.filter((val) => val != null && !isNaN(val));

    const griftiest = validPtiles.length > 0 ? Math.max(...validPtiles) : null;
    return `${griftGrade(griftiest)} - ${griftiest}%`;
  }

  /** links to elsewhere in the site */
  officersLink() {
    return `/officers/?nonprofit_kw=${this.ein}`;
  }

  financialsLink() {
    return `/nonprofit/assets/?filter=${this.ein}`;
  }

  nonprofitsLink() {
    return `/nonprofit/?filter=${this.ein}`;
  }

  propublicaLink(message) {
    return `<a href="https://projects.propublica.org/nonprofits/organizations/${this.ein}/${this.propublica990Id}/full" target="_blank" rel="noopener noreferrer" class="whitespace-nowrap">${message}</a>`;
  }
  googleLink(message) {
    const params = new URLSearchParams();
    params.set("q", `${this.longEIN} ${this.name}`);
    return `<a href="https://google.com/search?${params.toString()}" target="_blank" rel="noopener noreferrer" class="whitespace-nowrap"}>${message}</a>`;
  }

  grokLink(message) {
    const params = new URLSearchParams();
    params.set(
      "q",
      `Tell me about ${this.name} who has EIN ${this.longEIN} are they legit?`
    );
    return `<a href="https://grok.com/search?${params.toString()}"} target="_blank" rel="noopener noreferrer" class="whitespace-nowrap">${message}</a>`;
  }
}

/**
 * The edge as opposed to a Charity node.
 * filer: from
 * grantee: to
 *
 * aliases for source and target for sankey
 */
export class Grant {
  /** so we can find a grant quickly */
  static grantLookup = {};
  static missingValues = {};
  static _desiredGrants = new Set();

  static getGrant(id) {
    return Grant.grantLookup[id];
  }

  static registerGrant(g) {
    Grant.grantLookup[g.id] = g;
  }

  static unregisterGrant(g) {
    delete Grant.grantLookup[g.id];
  }

  /**
   * 600,000 grants, only a few charities visible, have to be visible for
   * grant to show, so we work backwards from visible charities.
   */
  static get visibleGrants() {
    // work backwards from visible charities
    const visibleGrants = new Set();
    for (const c of Charity.visibleCharities) {
      for (const g of c.grants) if (g.isLooseVisible) visibleGrants.add(g.id);
      for (const g of c.grantsIn) if (g.isLooseVisible) visibleGrants.add(g.id);
    }
    let result = [];
    for (const id of visibleGrants) {
      const g = Grant.grantLookup[id];
      if (g) result.push(g);
      else console.log(`Couldn't find grant ${g} in visibleGrants`);
    }
    return result;
  }

  static get desiredGrants() {
    return this._desiredGrants;
  }

  /** Commong pattern */
  static get allGrants() {
    return Object.values(Grant.grantLookup);
  }

  /** used when reading from the file */
  static checkGrantMatch(filer_ein, grant_ein) {
    return (
      filer_ein !== grant_ein &&
      Charity.getCharity(filer_ein) &&
      Charity.getCharity(grant_ein)
    );
  }

  /** grants are unique by filer/grantee */
  static grantIDBuilder(filer_ein, grant_ein) {
    return `${filer_ein}~${grant_ein}`;
  }

  /** factory for the file read */
  static loadGrantRow(row, grantType) {
    let filer_ein = row.filer_ein;
    let grant_ein = row.grant_ein;
    if (filer_ein?.length === 7) filer_ein = "0" + filer_ein;
    if (grant_ein?.length === 7) grant_ein = "0" + grant_ein;
    let amt = parseInt(row.grant_amt || row.amt || "0", 10);
    if (isNaN(amt) || amt === 0) {
      console.warn(`Invalid or zero grant_amt/amt for grant row:`, {
        filer_ein,
        grant_ein,
        grant_amt: row.grant_amt,
        amt: row.amt,
      });
      amt = 0;
    }
    if (
      !filer_ein ||
      !grant_ein ||
      filer_ein === grant_ein ||
      grant_ein === "Unknown" ||
      !/^[0-9]{3,9}$/.test(grant_ein)
    ) {
      console.warn(`Invalid EINs for grant row:`, {
        filer_ein,
        grant_ein,
        amt,
      });
      return null;
    }
    if (!Charity.getCharity(filer_ein)) {
      console.warn(`Missing filer_ein ${filer_ein} for grant`, row);
      Grant.missingValues[filer_ein] = "filer";
    }
    if (!Charity.getCharity(grant_ein)) {
      console.warn(
        `Missing grant_ein ${grant_ein} for grant with amount ${amt}`,
        row
      );
      Grant.missingValues[grant_ein] = `grantee-${amt}`;
    }
    const id = Grant.grantIDBuilder(filer_ein, grant_ein);
    const g = Grant.getGrant(id);
    if (g) {
      g.addAmt(amt);
      return g;
    } else {
      return new Grant({
        filer_ein,
        grant_ein,
        amt,
        grantType,
      });
    }
  }

  /** it is what it is */
  constructor({
    filer_ein,
    grant_ein,
    amt = 0,
    isCircular = false,
    desiredVisible = false,
    grantType = "regular",
  }) {
    this.registered = false;
    this.amt = amt;
    this.filer_ein = filer_ein;
    this.grant_ein = grant_ein;
    this.filer = Charity.getCharity(filer_ein);
    this.grantee = Charity.getCharity(grant_ein);
    this._desiredVisible = desiredVisible;
    this._impliedVisible = false;
    this._isCircular = isCircular;
    this.sourceLinks = [];
    this.targetLinks = [];
    this._source = null;
    this._target = null;
    Charity.addGrant(this);
    this.registered = true;
    this.grantType = grantType;
    this.buildId();
  }

  /** see Charity for the split visibility explanation */
  get isVisible() {
    return (
      this.isLooseVisible && this.filer.isVisible && this.grantee.isVisible
    );
  }

  /** see Charity for the split visibility explanation
   *
   * We need the loose visible when propagating to capture intent.
   */
  get isLooseVisible() {
    return this.desiredVisible || this.impliedVisible; // || this.isVisible;
  }

  /**
   * This is how the visibility propogates, goes one step to filer/grantee
   */
  set isVisible(value) {
    if (value) {
      if (this.filer) {
        this.filer.impliedVisible++;
      }
      if (this.grantee) {
        this.grantee.impliedVisible++;
      }
    } else {
      if (this.filer) {
        this.filer.impliedVisible--;
      }
      if (this.grantee) {
        this.grantee.impliedVisible--;
      }
    }
    // Propagate organization state changes to connected charities
    if (this.filer) this.filer.isOrganized = false;
    if (this.grantee) this.grantee.isOrganized = false;
  }

  /**
   * accessors
   * One nice thing about accessors is it makes it trivial to set breakpoints
   * when things change
   */
  get desiredVisible() {
    return this._desiredVisible;
  }

  set desiredVisible(value) {
    if (this._desiredVisible !== value) {
      this._desiredVisible = value;
      this.disorganize();
      if (value) Grant._desiredGrants.add(this);
      else Grant._desiredGrants.delete(this);
    }
  }

  get impliedVisible() {
    return this._impliedVisible;
  }

  set impliedVisible(value) {
    if (this._impliedVisible != value) {
      this._impliedVisible = value;
      if (!value) {
        if (this.filer) {
          this.filer.impliedVisible--;
        }
        if (this.grantee) {
          this.grantee.impliedVisible--;
        }
      } else {
        if (this.filer) {
          this.filer.impliedVisible++;
        }
        if (this.grantee) {
          this.grantee.impliedVisible++;
        }
      }
      // Propagate organization state changes to connected charities
      if (this.filer) this.filer.isOrganized = false;
      if (this.grantee) this.grantee.isOrganized = false;
    }
  }
  /** accessors to match the sankey API */
  get source() {
    return this._source || this.filer_ein;
  }

  set source(s) {
    this._source = s;
  }

  get target() {
    return this._target || this.grant_ein;
  }

  set target(t) {
    this._target = t;
  }

  /**
   * So we scale the amount so we can see both small and large grants.
   * However, one side effect of this is because sqrt(a+b) != sqrt(a) + sqrt(b)
   */
  get value() {
    return scaleValue(this.amt);
  }

  get scaledAmt() {
    return scaleValue(this.amt);
  }

  /** apsirational code to show what % of grants a particular grant represents */
  get relativeInAmount() {
    return this.amt / (this.filer.grantsTotal + 1);
  }

  get relativeAmount() {
    return this.amt / (this.grantee.grantsTotal + 1);
  }

  clearVisibility() {
    this.impliedVisible = false;
    this.desiredVisible = false;
    this.disorganize();
  }

  /** probably aspirational but if either is charity is hidden so are we */
  shouldHide() {
    return (
      viewModel.shouldHide(this.filer_ein) ||
      viewModel.shouldHide(this.grant_ein)
    );
  }

  /** so the way the data load works, different grants betweeen the same two NGOs will
   * appear, so we have to aggregate.
   */
  addAmt(amt) {
    this.amt += amt;
  }

  /**
   * build the ID for this grant, and register it
   * @returns
   */
  buildId() {
    this.id = Grant.grantIDBuilder(this.filer_ein, this.grant_ein);
    Grant.registerGrant(this);
    return this.id;
  }

  /** flow disorganization up to the filer and grantee */
  disorganize() {
    this.filer.isOrganized = false;
    this.grantee.isOrganized = false;
  }

  /**
   * sankey will read the ID, then map that to a Charity, and write it back.
   *
   * Which is ok, except when it fails matching on objects later.
   * Best to reset them back to IDs.
   */
  resetSourceTarget() {
    this._source = null;
    this.sourceLinks = [];
    this._target = null;
    this.targetLinks = [];
  }

  /** Convenience */
  toString() {
    return `${this.id} ${formatNumber(this.amt)} (${this.value})`;
  }

  /**
   * A version of tunneling for grants, show just the two nodes involved.
   */
  tunnelGrant() {
    Charity.desiredCharities.forEach((c) => (c.desiredVisible = false));
    Object.values(Grant.grantLookup).forEach((g) => (g.desiredVisible = false));
    this.desiredVisible = true;
    this.filer_ein.desiredVisible = true;
    this.grant_ein.desiredVisible = true;
  }
}

/**
 * Utility function
 * @param {*} message
 * @param {*} color
 * @param {*} loading
 */
function updateStatus(message, color = "black", loading = true) {
  $("#status").html(`<span class="flex flex-col items-end text-sm">
    ${loading ? "<span>Loading...</span>" : ""}
    ${message}</span>`);
}

viewModel = new BrowseViewModel();

export { viewModel };
