import {
  Charity,
  Grant,
  formatNumber,
  viewModel,
  BrowseViewModel,
  getColorForEIN,
} from "./models.js";

import {
  sankeyWithCircles,
  adjustCircularLink,
  generateOctagonPath,
  generateTrapezoidPath,
  generatePlusPath,
} from "./d3-sankey-circular.js";

let svg = null;
let zoom = null;

let boundaryScaleFactorX = 2;
let boundaryScaleFactorY = 2;
const MAX_SCALE = 10;

let NODE_WIDTH = 50 * boundaryScaleFactorX;
let OTHER_WIDTH = 30 * boundaryScaleFactorY;
let NODE_PADDING = 25 * boundaryScaleFactorX;
let MIN_LINK_HEIGHT = 5 * boundaryScaleFactorY;
let FONT_SIZE = 12 * boundaryScaleFactorY;
const VERT_SCALE_RATIO = 1.5; // better to scale vertically faster with a sankey
const FONT_CONSTANT = 4;
let isRedrawing = false;

function updateScaledConstants() {
  boundaryScaleFactorX = viewModel.getExpandScaleX();
  boundaryScaleFactorY = viewModel.getExpandScaleY();
  NODE_WIDTH = 50 * boundaryScaleFactorX;
  OTHER_WIDTH = 30 * boundaryScaleFactorX;
  NODE_PADDING = 25 * boundaryScaleFactorY;
  MIN_LINK_HEIGHT = 5 * boundaryScaleFactorY;
  FONT_SIZE = 12 * boundaryScaleFactorY;
}

function updateStatus(message, color = "black") {
  $("#status").text(message).css("color", color);
  //$("#statusSpinner").toggle(message === "Failed to load data.");
}

function dataLoaded(state = true) {
  if (state) {
    $("#statusSpinner").addClass("hidden");
    $("#loading").addClass("hidden");
    $("#downloadPanel").removeClass("hidden");
    document.documentElement.style.setProperty("--web-load", "none");
    document.documentElement.style.setProperty("--db-load", "none");
  } else {
    $("#statusSpinner").removeClass("hidden");
    $("#loading").removeClass("hidden");
    $("#downloadPanel").addClass("hidden");
  }
}

window.exportDB = function () {
  viewModel.exportDB();
};

window.loadPreset = function (value, mode) {
  viewModel.loadPreset(value, mode);
  hidePresets(); // our work here is done.
  refresh();
};

function renderPopup() {
  const popup = document.getElementById("ngo-popup");
  const presets = viewModel.presets();
  popup.innerHTML = `
    <div class="ngopreset-container show-when-panel-shown">
      <div class="ngopreset-columns">
        <div class="toggle-row">
          <div class="button-group-column">
            <button id="ngopreset-toggle" class="ngopreset-toggle-btn " style="font-size: 11px;">Hide Presets</button>
            <div class="ngopreset-mode-switch">
              <span class="toggle-label toggle-label-add">Preset will add</span>
              <div class="toggle-switch">
                <input type="checkbox" id="preset-mode" value="add">
                <label for="preset-mode"></label>
              </div>
              <span class="toggle-label toggle-label-replace">Preset will replace</span>
            </div>
          </div>
          <div class="filler-column"></div>
        </div>
        <div class="grid-row">
          <!-- General Column -->
          <div class="ngopreset-column">
            <h2 class="ngopreset-column-title">General</h2>
            <div class="ngopreset-grid">
              ${presets
                .filter((item) => !item.subcategories)
                .map(
                  (item) => `
                  <button class="ngopreset-btn" 
                          data-eins='${JSON.stringify(item.eins)}' 
                          data-title="${item.title}"
                          title="${item.description || ""}">
                    ${item.title}
                  </button>
                `
                )
                .join("")}
            </div>
          </div>
          <!-- Controversies Column -->
          <div class="ngopreset-column">
            <h2 class="ngopreset-column-title">Controversies</h2>
            <div class="ngopreset-grid">
              ${
                presets
                  .find((item) => item.title === "Controversies")
                  ?.subcategories?.map(
                    (group) => `
                  <button class="ngopreset-btn" 
                          data-eins='${JSON.stringify(group.eins)}' 
                          data-title="${group.title}"
                          title="${group.description || ""}">
                    ${group.title}
                  </button>
                `
                  )
                  .join("") || ""
              }
            </div>
          </div>
          <!-- Politicians Column -->
          <div class="ngopreset-column">
            <h2 class="ngopreset-column-title">Politicians</h2>
            <div class="ngopreset-grid">
              ${
                presets
                  .find((item) => item.title === "Politicians")
                  ?.subcategories?.map(
                    (group) => `
                  <button class="ngopreset-btn" 
                          data-eins='${JSON.stringify(group.eins)}' 
                          data-title="${group.title}"
                          title="${group.description || ""}">
                    ${group.title}
                  </button>
                `
                  )
                  .join("") || ""
              }
            </div>
          </div>
          <!-- Billionaires Column -->
          <div class="ngopreset-column">
            <h2 class="ngopreset-column-title">Billionaires</h2>
            <div class="ngopreset-grid">
              ${
                presets
                  .find(
                    (item) =>
                      item.title === "Friendly Neighborhood Billionaires"
                  )
                  ?.subcategories?.map(
                    (group) => `
                  <button class="ngopreset-btn" 
                          data-eins='${JSON.stringify(group.eins)}' 
                          data-title="${group.title}"
                          title="${group.description || ""}">
                    ${group.title}
                  </button>
                `
                  )
                  .join("") || ""
              }
            </div>
          </div>
        </div>
      </div>
    </div>
  `;

  // Add event listeners for preset buttons
  const presetButtons = document.querySelectorAll(".ngopreset-btn");
  presetButtons.forEach((btn) => {
    btn.addEventListener("click", () => {
      const eins = JSON.parse(btn.dataset.eins);
      const title = btn.dataset.title;
      const mode = document.getElementById("preset-mode").checked
        ? "replace"
        : "add";
      console.log(
        `${mode === "add" ? "Adding" : "Replacing"} ${title}: ${eins}`
      );
      loadPreset({ eins, title }, mode);
    });
  });

  // Wire up the toggle buttons
  const showBtn = document.getElementById("showPresetsBtn");
  const hideBtn = document.getElementById("ngopreset-toggle");
  if (showBtn) {
    showBtn.addEventListener("click", showPresets);
  }
  if (hideBtn) {
    hideBtn.addEventListener("click", hidePresets);
  }

  // Toggle How It Works
  document.getElementById("howItWorksBtn").addEventListener("click", () => {
    const list = document.getElementById("howItWorksList");
    list.classList.toggle("visible");
  });
}

// Show Presets and Hide Presets implementations
window.showPresets = function () {
  document.documentElement.style.setProperty("--panel-shown", "block");
  document.documentElement.style.setProperty("--panel-hidden", "none");
  console.log("Panel shown");
};

window.hidePresets = function () {
  document.documentElement.style.setProperty("--panel-shown", "none");
  document.documentElement.style.setProperty("--panel-hidden", "block");
  console.log("Panel hidden");
};

window.loadingViaDB = function () {
  document.documentElement.style.setProperty("--db-load", "block");
  document.documentElement.style.setProperty("--web-load", "none");
};
window.loadingViaWeb = function () {
  document.documentElement.style.setProperty("--db-load", "none");
  document.documentElement.style.setProperty("--web-load", "block");
};

$(document).ready(function () {
  if (viewModel.dataReady) viewModel.parseQueryParams();

  const params = new URLSearchParams(window.location.search);
  const boundaryScale = viewModel.getExpandScaleX();

  updateStatus("Loading Data...");

  // Initialize the select on page load
  renderPopup();
  updateLayoutButtons();

  dataLoaded(false);
  viewModel
    .loadData()
    .then(() => {
      generateGraph();
    })
    .catch((err) => {
      console.error(err);
      updateStatus("Failed to load data.", "red");
    });

  $("#addEinBtn").on("click", addEINFromInput);
  $("#einInput").on("keypress", (e) => {
    if (e.key === "Enter") addEINFromInput();
  });
  $("#clearEINsBtnShow").on("click", () => {
    viewModel.clearShowList();
    viewModel.clearAll();
    refresh();
  });
  $("#clearEINsBtnHide").on("click", () => {
    viewModel.clearHideList();
    renderHideEINs();
    updateQueryParams();
    generateGraph();
  });
  $("#addFilterBtn").on("click", addKeywordFromInput);
  $("#keywordInput").on("keypress", (e) => {
    if (e.key === "Enter") addKeywordFromInput();
  });
  $("#clearFiltersBtn").on("click", () => {
    viewModel.clearKeywordList();
    renderActiveKeywords();
    updateQueryParams();
    generateGraph();
  });

  $("#downloadBtn").on("click", downloadSVG);

  $("#howItWorksBtn").on("click", function () {
    const $list = $("#howItWorksList");
    const $btn = $(this);
    if ($list.height() === 0) {
      $list.css("height", "auto");
      const autoHeight = $list.height();
      $list.height(0);
      $list.height(autoHeight);
      $btn.text("Hide details");
    } else {
      $list.height(0);
      $btn.text("How it works (& why you should use it)");
    }
  });

  const searchInput = document.getElementById("searchInput");
  const searchResults = document.getElementById("searchResults");
  const clearButton = document.getElementById("clearSearch");

  const newSearchInput = searchInput.cloneNode(true);
  const newSearchResults = searchResults.cloneNode(true);
  const newClearButton = clearButton.cloneNode(true);

  searchInput.parentNode.replaceChild(newSearchInput, searchInput);
  searchResults.parentNode.replaceChild(newSearchResults, searchResults);
  clearButton.parentNode.replaceChild(newClearButton, clearButton);

  newSearchInput.addEventListener("input", handleSearch);
  newSearchInput.addEventListener("blur", handleSearchBlur);
  newSearchInput.addEventListener("keydown", handleSearchKeydown);
  newSearchResults.addEventListener("click", handleSearchClick);

  newClearButton.addEventListener("click", () => {
    newSearchInput.value = "";
    newSearchInput.focus();
    handleSearch({ target: newSearchInput });
  });

  $(window).on("resize", function () {
    if (viewModel.dataReady) generateGraph();
  });
});

function addEINFromInput() {
  let val = $("#einShowInput").val().trim().replace(/[-\s]/g, "");
  if (!/^\d{3,9}$/.test(val)) {
    alert(
      "EIN must be 9 digits after removing dashes/spaces or 3 for countries."
    );
    return;
  }
  const charity = Charity.getCharity(val);
  if (!charity) console.warn("EIN not found in charities.csv (still adding).");
  viewModel.addToShowList(val);
  $("#einShowInput").val("");
  renderActiveEINs();
  if (charity) charity.place(charity.ein);
  updateQueryParams();
  generateGraph();
}

function renderActiveEINs() {
  const $c = $("#activeEINs");
  $c.empty();
  $("#clearEINsBtnShow").toggle(viewModel.getShowList().length > 0);

  viewModel.getShowList().forEach((ein) => {
    const name = Charity.getCharity(ein)?.name || "???";
    const $tag = $(
      '<div class="filter-tag flex items-center gap-0.5 rounded border border-green bg-green/10 text-green px-2 py-1 text-xs"></div>'
    );
    const $text = $(
      `<span title="EIN: ${ein.split(/[:~]/)[0].slice(0, 2)}-${ein
        .split(/[:~]/)[0]
        .slice(2)}"></span>`
    ).text(name);
    const $rm = $(
      '<span class="remove-filter opacity-50 hover:opacity-100 size-5 -my-0.5 -mr-1 cursor-pointer"><svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24"><path fill="#000" fill-rule="evenodd" d="M2 12C2 6.477 6.477 2 12 2s10 4.477 10 10-4.477 10-10 10S2 17.523 2 12Zm7.53-3.53a.75.75 0 0 0-1.06 1.06L10.94 12l-2.47 2.47a.75.75 0 1 0 1.06 1.06L12 13.06l2.47 2.47a.75.75 0 1 0 1.06-1.06L13.06 12l2.47-2.47a.75.75 0 0 0-1.06-1.06L12 10.94 9.53 8.47Z" clip-rule="evenodd"/></svg></span>'
    ).attr("data-ein", ein);
    $rm.on("click", function () {
      viewModel.removeFromShowList(ein);
      renderActiveEINs();
      updateQueryParams();
      generateGraph();
    });
    $tag.append($text).append($rm);
    $c.append($tag);
  });
}

function renderHideEINs() {
  const $c = $("#hideEINs");
  $c.empty();
  $("#clearEINsBtnHide").toggle(viewModel.getHideList().length > 0);

  viewModel.getHideList().forEach((ein) => {
    const name = Charity.getCharity(ein)?.name || "???";
    const $tag = $(
      '<div class="filter-tag flex items-center gap-0.5 rounded border border-red bg-red/10 text-red rounded-md px-2 py-1 text-xs"></div>'
    );
    const $text = $(
      `<span title="EIN: ${ein.split(/[:~]/)[0].slice(0, 2)}-${ein
        .split(/[:~]/)[0]
        .slice(2)}"></span>`
    ).text(name);
    const $rm = $(
      '<span class="remove-filter opacity-50 hover:opacity-100 size-5 -my-0.5 -mr-1 cursor-pointer"><svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24"><path fill="#000" fill-rule="evenodd" d="M2 12C2 6.477 6.477 2 12 2s10 4.477 10 10-4.477 10-10 10S2 17.523 2 12Zm7.53-3.53a.75.75 0 0 0-1.06 1.06L10.94 12l-2.47 2.47a.75.75 0 1 0 1.06 1.06L12 13.06l2.47 2.47a.75.75 0 1 0 1.06-1.06L13.06 12l2.47-2.47a.75.75 0 0 0-1.06-1.06L12 10.94 9.53 8.47Z" clip-rule="evenodd"/></svg></span>'
    ).attr("data-nein", ein);
    $rm.on("click", function () {
      viewModel.removeFromHideList(ein);
      renderHideEINs();
      updateQueryParams();
      generateGraph();
    });
    $tag.append($text).append($rm);
    $c.append($tag);
  });
}

function addKeywordFromInput() {
  const kw = $("#keywordInput").val().trim();
  if (kw.length > 0) {
    viewModel.addToKeywords(kw.toLowerCase());
    $("#keywordInput").val("");
    renderActiveKeywords();
    updateQueryParams();
    generateGraph();
  }
}

function renderActiveKeywords() {
  const $c = $("#activeFilters");
  $c.empty();
  $("#clearFiltersBtn").toggle(viewModel.getKeywordList().length > 0);

  viewModel.getKeywordList().forEach((kw) => {
    const $tag = $(
      '<div class="filter-tag flex items-center gap-0.5 rounded border border-blue bg-blue/10 text-blue rounded-md px-2 py-1 text-xs"></div>'
    );
    const $text = $("<span></span>").text(kw);
    const $rm = $(
      '<span class="remove-filter opacity-50 hover:opacity-100 size-5 -my-0.5 -mr-1 cursor-pointer"><svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24"><path fill="#000" fill-rule="evenodd" d="M2 12C2 6.477 6.477 2 12 2s10 4.477 10 10-4.477 10-10 10S2 17.523 2 12Zm7.53-3.53a.75.75 0 0 0-1.06 1.06L10.94 12l-2.47 2.47a.75.75 0 1 0 1.06 1.06L12 13.06l2.47 2.47a.75.75 0 1 0 1.06-1.06L13.06 12l2.47-2.47a.75.75 0 0 0-1.06-1.06L12 10.94 9.53 8.47Z" clip-rule="evenodd"/></svg></span>'
    ).attr("data-kw", kw);
    $rm.on("click", function () {
      viewModel.removeFromKeywords(kw);
      renderActiveKeywords();
      updateQueryParams();
      generateGraph();
    });
    $tag.append($text).append($rm);
    $c.append($tag);
  });
}

function downloadSVG() {
  const svgEl = document.querySelector("#graph-container svg");
  if (!svgEl) {
    alert("No SVG to download yet.");
    return;
  }
  const svgData = new XMLSerializer().serializeToString(svgEl);
  const blob = new Blob([svgData], { type: "image/svg+xml;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = "charity_graph.svg";
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  URL.revokeObjectURL(url);
}

function updateQueryParams() {
  const params = viewModel.computeURLParams();
  const newUrl = window.location.pathname + "?" + params.toString();
  window.history.replaceState({}, "", newUrl);
}

function compareCharities(a, b) {
  return (
    b.grantsInTotal + b.grantsTotal - (a.grantsInTotal + a.grantsTotal) ||
    a.name.localeCompare(b.name)
  );
}

function compareLinks(a, b) {
  return b.value - a.value;
}

function generateUniqueId(prefix = "gradient", link) {
  return `${prefix}-${link.filer.id}~${link.grantee.id}`;
}

function calculateScale(graph, width, height) {
  const nodes = graph.nodes;
  if (!nodes.length) return 1;

  let minX = Infinity,
    maxX = -Infinity;
  let minY = Infinity,
    maxY = -Infinity;

  nodes.forEach((node) => {
    minX = Math.min(minX, node.x0);
    maxX = Math.max(maxX, node.x1);
    minY = Math.min(minY, node.y0);
    maxY = Math.max(maxY, node.y1);
  });

  const layoutWidth = Math.max(maxX - minX, 1);
  const layoutHeight = Math.max(maxY - minY, 1);
  return Math.min(width / layoutWidth, height / layoutHeight);
}

function computeLinkY(node, linkIndex, links, heightKey, isSourceSide) {
  const sortedLinks = [...links].sort(compareLinks);
  const cumulativeHeight = d3.sum(
    sortedLinks.slice(0, linkIndex),
    (l) => l.width
  );
  const centerY = (node.y0 + node.y1) / 2;
  const height = node[heightKey] || 0;
  const linkHeight = sortedLinks[linkIndex].width || 0;
  const startY = isSourceSide ? centerY - height / 2 : centerY + height / 2;
  const segmentTop = isSourceSide
    ? startY + cumulativeHeight
    : startY - height + cumulativeHeight;
  return segmentTop + linkHeight / 2;
}

function sankeyLinkHorizontalTrapezoid(curvature = 0.5) {
  return function (link) {
    const source = link.source;
    const originalSourceLinks = [...source.sourceLinks];
    const outflowIndex = source.sourceLinks.sort(compareLinks).indexOf(link);
    const sourceY = computeLinkY(
      source,
      outflowIndex,
      source.sourceLinks,
      "outflowHeight",
      true
    );
    const sourceX = source.x1;

    const target = link.target;
    const originalTargetLinks = [...target.targetLinks];
    const inflowIndex = target.targetLinks.sort(compareLinks).indexOf(link);
    const targetY = computeLinkY(
      target,
      inflowIndex,
      target.targetLinks,
      "inflowHeight",
      false
    );
    const targetX = target.x0;

    source.sourceLinks = originalSourceLinks;
    target.targetLinks = originalTargetLinks;

    const dx = targetX - sourceX;
    const cp1X = sourceX + dx * curvature;
    const cp1Y = sourceY;
    const cp2X = targetX - dx * curvature;
    const cp2Y = targetY;

    return `M${sourceX},${sourceY} C${cp1X},${cp1Y} ${cp2X},${cp2Y} ${targetX},${targetY}`;
  };
}

function calculateRegularPosition(node, scale, height) {
  let scaleFactor = 100;
  // note, this are using only the visible grants.
  const sankeyHeight = node.y1 - node.y0;
  if (node.grantsInLogTotal > node.grantsLogTotal)
    scaleFactor = sankeyHeight / node.grantsInLogTotal;
  else scaleFactor = sankeyHeight / node.grantsLogTotal;
  node.outflowHeight = Math.min(
    sankeyHeight,
    node.grantsLogTotal * scaleFactor
  );
  node.inflowHeight = Math.min(
    sankeyHeight,
    node.grantsInLogTotal * scaleFactor
  );
  if (node.grantsLogTotal === 0) {
    node.inflowHeight = sankeyHeight;
    node.outflowHeight = 5;
  }
  if (node.grantsInLogTotal === 0) {
    node.inflowHeight = 5;
    node.outflowHeight = sankeyHeight;
  }
  if (!isFinite(node.outflowHeight) || !isFinite(node.inflowHeight)) {
    console.error(
      `Invalid heights for ${node.filer_ein}: outflow=${node.outflowHeight}, inflow=${node.inflowHeight}`
    );
    node.outflowHeight = 50;
    node.inflowHeight = 50;
  }
}

function calculateNodePositions(nodes, scale, height) {
  nodes.forEach((d) => calculateRegularPosition(d, scale, height));
}

function normalizeStrokeWidths(sankey) {
  const nodes = sankey.nodes;
  nodes.forEach((node) => {
    const totalOutflowWidth = d3.sum(node.sourceLinks, (l) => l.width);
    const outflowHeight = node.outflowHeight || 0;
    if (totalOutflowWidth > 0 && outflowHeight > 0) {
      const scaleFactor = outflowHeight / totalOutflowWidth;
      node.sourceLinks.forEach(
        (link) => (link.normalizedWidth = link.width * scaleFactor)
      );
    }
    const totalInflowWidth = d3.sum(node.targetLinks, (l) => l.width);
    const inflowHeight = node.inflowHeight || 0;
    if (totalInflowWidth > 0 && inflowHeight > 0) {
      const scaleFactor = inflowHeight / totalInflowWidth;
      node.targetLinks.forEach(
        (link) => (link.normalizedWidth = link.width * scaleFactor)
      );
    }
  });
}
function savePreviousState(data) {
  data.nodes.forEach((node) => {
    if (node.hasOwnProperty("x0")) {
      node.previousX0 = node.x0;
      node.previousY0 = node.y0;
      node.previousX1 = node.x1;
      node.previousY1 = node.y1;
      node.hasLeftHat =
        node.canExpandInflows && node.invisibleGrantsIn.length > 0;
      node.hasRightHat =
        !node.isTerminal &&
        node.canExpandOutflows &&
        node.invisibleGrants.length > 0;
    }
  });
  data.links.forEach((link) => {
    if (link.hasOwnProperty("width")) {
      link.previousWidth = link.width;
      link.previousSource = {
        x0: link.source.x0,
        y0: link.source.y0,
        x1: link.source.x1,
        y1: link.source.y1,
      };
      link.previousTarget = {
        x0: link.target.x0,
        y0: link.target.y0,
        x1: link.target.x1,
        y1: link.target.y1,
      };
    }
  });
}

function bindEvents(g) {
  g.selectAll(".node")
    .on("click", (event, d) => {
      console.log("Node clicked:", d.id);
      event.stopPropagation();
      if (event.shiftKey) {
        d.hide();
        Charity.addToHideList(d.ein);
        refresh();
      } else if (event.metaKey) {
        showControlPanel("node", d, this);
      } else {
        viewModel.clickNode(event, d, refresh);
      }
    })
    .on("dblclick", (event, d) => {
      console.log("Node double-clicked:", d.id);
      event.stopPropagation();
      if (d.isTerminal && !event.shiftKey) {
        d.hideUp();
        Charity.addToHideList(d.ein);
        refresh();
      } else {
        viewModel.doubleClickNode(event, d, refresh);
      }
    });

  g.selectAll(".link")
    .on("click", (event, d) => {
      console.log("Link clicked:", d.id);
      event.stopPropagation();
      showControlPanel("link", d, this);
    })
    .on("dblclick", (event, d) => {
      console.log("Link double-clicked:", d.id);
      event.stopPropagation();
      viewModel.doubleClickGrant(event, d, refresh);
    });

  g.selectAll(".hat-up").on("click", (event, d) => {
    console.log("Hat left clicked:", d.id);
    event.stopPropagation();
    viewModel.handleUpClick(event, d, refresh);
  });

  g.selectAll(".hat-down").on("click", (event, d) => {
    console.log("Hat right clicked:", d.id);
    event.stopPropagation();
    viewModel.handleDownClick(event, d, refresh);
  });
}

function zoomToFit() {
  let g = svg.select("g.main");
  if (g.empty()) {
    console.warn("g.main not found, creating new g.main");
    g = svg
      .append("g")
      .attr("class", "main")
      .attr("transform", "translate(50, 50)");
    return; // Skip zooming until graph is rendered
  }
  const bounds = g.node().getBBox();
  if (
    !isFinite(bounds.width) ||
    bounds.width <= 0 ||
    !isFinite(bounds.height) ||
    bounds.height <= 0
  ) {
    console.warn("Invalid bounds for zoom:", bounds);
    return;
  }
  const container = document.getElementById("graph-container");
  const width = container.offsetWidth;
  const height = container.offsetHeight || window.innerHeight * 0.75;
  const dx = bounds.x;
  const dy = bounds.y;
  const scale = 0.9 / Math.max(bounds.width / width, bounds.height / height);
  svg
    .transition()
    .duration(750)
    .call(
      zoom.transform,
      d3.zoomIdentity
        .translate(width / 2, height / 2)
        .scale(scale)
        .translate(-dx - bounds.width / 2, -dy - bounds.height / 2)
    );
}

function generateGraph() {
  if (!viewModel.dataReady) {
    updateStatus("No Data Loaded");
    dataLoaded(false);
    alert("Data not loaded yet. Please wait.");
    return;
  }

  $("#graph").empty();
  updateStatus("Generating Graph...");

  const container = document.getElementById("graph-container");
  const width = container.offsetWidth;
  const height = Math.max(container.offsetHeight, window.innerHeight * 0.75);

  svg = d3
    .select("#graph-container > svg#graph[data-graph='true']")
    .attr("id", "graph")
    .attr("data-graph", "true");
  // Fallback: Create SVG if not found
  if (!svg.node()) {
    console.warn("No #graph SVG found, creating new one");
    svg = d3
      .select("#graph-container")
      .append("svg")
      .attr("id", "graph")
      .attr("data-graph", "true");
  }

  svg
    .attr("width", "100%") // Ensure attributes are set in case SVG was cleared
    .attr("height", "100%")
    .style("display", "block")
    .style("background", "#fff")
    .attr("class", "flex-1");

  let g = svg
    .append("g")
    .attr("class", "main")
    .attr("transform", "translate(50, 50)");

  zoom = d3
    .zoom()
    .extent([
      [0, 0],
      [width, height],
    ]) // Match renderFocusedSankey extent
    .scaleExtent([0.1, 4]) // Match original range
    .filter(
      (event) =>
        event.type === "wheel" ||
        (event.type === "mousedown" && event.button === 0)
    ) // Match renderFocusedSankey filter
    .on("zoom", (event) => {
      svg.select("g.main").attr("transform", event.transform); // Target g.main
    });

  svg.call(zoom);

  updateScaledConstants();
  const sankey = sankeyWithCircles()
    .nodeId((d) => d.ein)
    .nodeWidth(NODE_WIDTH)
    .nodePadding(NODE_PADDING)
    .linkSort(compareLinks)
    .nodeAlign(d3.sankeyCenter)
    .nodeSort(compareCharities)
    .size([
      (width - 100) * viewModel.getExpandScaleX(),
      (height - 100) * viewModel.getExpandScaleY(),
    ]);

  viewModel.parseQueryParams();
  if (viewModel.matchURL() === 0) {
    showPresets();
    return;
  }

  viewModel.previousData = renderFocusedSankey(
    g,
    sankey,
    svg, // Use global svg instead of svgRef
    width,
    height,
    viewModel.getShowList().length
      ? viewModel.getShowList()
      : [viewModel.GOV_EIN],
    viewModel.previousData
  );

  // Button handlers using global zoom
  document.getElementById("zoomIn").onclick = () =>
    svg.transition().duration(300).call(zoom.scaleBy, 1.3);
  document.getElementById("zoomOut").onclick = () =>
    svg.transition().duration(300).call(zoom.scaleBy, 0.7);
  document.getElementById("zoomFit").onclick = () => {
    const g = svg.select("g.main"); // Select g.main dynamically
    const bounds = g.node().getBBox();
    if (
      !isFinite(bounds.width) ||
      bounds.width <= 0 ||
      !isFinite(bounds.height) ||
      bounds.height <= 0
    )
      return;
    const dx = bounds.x;
    const dy = bounds.y;
    const scale = 0.8 / Math.max(bounds.width / width, bounds.height / height);
    svg
      .transition()
      .duration(750)
      .call(
        zoom.transform,
        d3.zoomIdentity
          .translate(width / 2, height / 2)
          .scale(scale)
          .translate(-dx - bounds.width / 2, -dy - bounds.height / 2)
      );
  };
  document.getElementById("scaleUp").onclick = () => {
    viewModel.graphScaleUp();
    refresh();
  };
  document.getElementById("scaleDown").onclick = () => {
    viewModel.graphScaleDown();
    refresh();
  };
  document.getElementById("scaleReset").onclick = () => {
    viewModel.graphScaleReset();
    refresh();
  };

  /*setTimeout(() => {
    const g = svg.select("g.main");
    const bounds = g.node().getBBox();
    if (
      !isFinite(bounds.width) ||
      bounds.width <= 0 ||
      !isFinite(bounds.height) ||
      bounds.height <= 0
    ) {
      console.error("Invalid bounds for zoom:", bounds);
      return;
    }
    const dx = bounds.x;
    const dy = bounds.y;
    const scale = 0.8 / Math.max(bounds.width / width, bounds.height / height);
    svg
      .transition()
      .duration(750)
      .call(
        zoom.transform,
        d3.zoomIdentity
          .translate(width / 2, height / 2)
          .scale(scale)
          .translate(-dx - bounds.width / 2, -dy - bounds.height / 2)
      );
  }, 1000);*/
  document.getElementById("expandLayoutX").onclick = () => {
    if (isRedrawing) return;
    isRedrawing = true;
    boundaryScaleFactorX = viewModel.expandScaleXUp();
    refresh();
    setTimeout(() => (isRedrawing = false), 1000);
  };
  document.getElementById("shrinkLayoutX").onclick = () => {
    if (isRedrawing) return;
    isRedrawing = true;
    boundaryScaleFactorX = viewModel.expandScaleXDown();
    updateLayoutButtons();
    refresh();
    setTimeout(() => (isRedrawing = false), 1000);
  };
  document.getElementById("layoutScaleResetX").onclick = () => {
    if (isRedrawing) return;
    isRedrawing = true;
    viewModel.setExpandScaleX(2);
    updateLayoutButtons();
    refresh();
    setTimeout(() => (isRedrawing = false), 1000);
  };
  document.getElementById("expandLayoutY").onclick = () => {
    if (isRedrawing) return;
    isRedrawing = true;
    boundaryScaleFactorY = viewModel.expandScaleYUp();
    updateLayoutButtons();
    refresh();

    setTimeout(() => (isRedrawing = false), 1000);
  };
  document.getElementById("shrinkLayoutY").onclick = () => {
    if (isRedrawing) return;
    isRedrawing = true;
    boundaryScaleFactorY = viewModel.expandScaleYDown();
    updateLayoutButtons();
    refresh();

    setTimeout(() => (isRedrawing = false), 1000);
  };
  document.getElementById("layoutScaleResetY").onclick = () => {
    if (isRedrawing) return;
    isRedrawing = true;
    viewModel.setExpandScaleY(2);
    updateLayoutButtons();
    refresh();

    setTimeout(() => (isRedrawing = false), 1000);
  };
  renderActiveEINs();
  renderActiveKeywords();
  renderActiveEINs();
  renderHideEINs();

  dataLoaded(true);
  zoomToFit();
}

function adjustCircularLinks(graph) {
  let circularLinkData = graph.links.filter((l) => l.circular);
  // adjust circular links for trapezoid
  for (const l of circularLinkData) {
    l.y0 = computeLinkY(
      l.source,
      l.source.sourceLinks.indexOf(l),
      l.source.sourceLinks,
      "outflowHeight",
      true
    );
    l.y1 = computeLinkY(
      l.target,
      l.target.targetLinks.indexOf(l),
      l.target.targetLinks,
      "inflowHeight",
      false
    );
    adjustCircularLink(l);
  }
}

function updateLayoutButtons() {
  const expandBtnX = document.getElementById("expandLayoutX");
  const shrinkBtnX = document.getElementById("shrinkLayoutX");
  expandBtnX.disabled = !viewModel.canExpandUpX();
  shrinkBtnX.disabled = !viewModel.canExpandDownX();
  const expandBtnY = document.getElementById("expandLayoutX");
  const shrinkBtnY = document.getElementById("shrinkLayoutX");
  expandBtnY.disabled = !viewModel.canExpandUpY();
  shrinkBtnY.disabled = !viewModel.canExpandDownY();
  const scaleDisplayX = document.getElementById("layoutScaleDisplayX");
  if (scaleDisplayX) {
    scaleDisplayX.textContent = `${viewModel.getExpandScaleX().toFixed(1)}x`;
  }
  const scaleDisplayY = document.getElementById("layoutScaleDisplayY");
  if (scaleDisplayY) {
    scaleDisplayY.textContent = ` ${viewModel.getExpandScaleY().toFixed(1)}x`;
  }
}

function renderFocusedSankey(
  g,
  sankey,
  svg,
  width,
  height,
  nodeIds,
  previousData
) {
  const ANIM_NODE = 500;
  const ANIM_LINK = 1200;
  const ANIM_HAT = 1500;
  const ANIM_TEXT = 1500;
  dataLoaded(false);

  let currentData = viewModel.buildSankeyData();
  const nodeCount = currentData.nodes.length;
  const edgeCount = currentData.links.length;
  const edgeTotal = formatNumber(
    currentData.links.reduce((sum, g) => sum + g.amt, 0)
  );

  savePreviousState(currentData);

  updateScaledConstants();
  const sankeyWidth = (width - 100) * boundaryScaleFactorX;
  const sankeyHeight = (height - 100) * boundaryScaleFactorY;
  sankey.size([sankeyWidth, sankeyHeight]).nodePadding(NODE_PADDING);

  const graph = sankey(currentData);

  const scale = calculateScale(graph, width, height);
  calculateNodePositions(graph.nodes, scale, height);
  adjustCircularLinks(graph);
  normalizeStrokeWidths(graph);

  calculateNodePositions(graph.nodes, scale, height);
  adjustCircularLinks(graph);
  normalizeStrokeWidths(graph);

  if (!previousData) {
    svg.selectAll("*").remove();
  }

  g = svg
    .selectAll("g.main")
    .data([0])
    .join("g")
    .attr("class", "main")
    .attr("transform", `translate(50, 50) scale(${scale})`);

  const masterGroup = g
    .selectAll(".graph-group")
    .data([0])
    .join("g")
    .attr("class", "graph-group");

  // Regular (non-circular) links
  const linkGroup = masterGroup
    .selectAll("g.links")
    .data([0])
    .join("g")
    .attr("class", "links")
    .attr("fill", "none")
    .attr("stroke-opacity", 1)
    .style("mix-blend-mode", "multiply");

  const regularLinks = graph.links.filter((d) => !d.circular);
  const link = linkGroup
    .selectAll(".link")
    .data(regularLinks, (d) => `${d.source.id}-${d.target.id}`);

  link.exit().transition().duration(ANIM_LINK).attr("stroke-width", 0).remove();

  const linkEnter = link
    .enter()
    .append("path")
    .attr("class", "link")
    .attr("d", sankeyLinkHorizontalTrapezoid())
    .attr("stroke", (d) => getColorForEIN(d.source.id))
    .style("stroke-opacity", "0.3")
    .attr("stroke-width", 0);

  link
    .merge(linkEnter)
    .transition()
    .duration(ANIM_LINK)
    .attr("d", sankeyLinkHorizontalTrapezoid())
    .attr("stroke", (d) => getColorForEIN(d.source.id))
    .attr("stroke-width", (d) => d.width || 1);

  linkEnter
    .append("title")
    .text(
      (d) => `${d.source.name} → ${d.target.name}\n$${formatNumber(d.amt)}`
    );

  // Function to extract points from an SVG path string
  function extractPointsFromPath(pathString) {
    const points = [];
    let currentPoint = { x: 0, y: 0 };
    const commands = pathString.split(/(?=[MLAZ])/); // Split on M, L, A, Z

    commands.forEach((command) => {
      const type = command[0];
      const values = command
        .slice(1)
        .trim()
        .split(/[\s,]+/)
        .map(parseFloat);

      if (type === "M") {
        currentPoint = { x: values[0], y: values[1] };
        points.push({ ...currentPoint });
      } else if (type === "L") {
        currentPoint = { x: values[0], y: values[1] };
        points.push({ ...currentPoint });
      } else if (type === "A") {
        // For arcs, the endpoint is the last two values (x, y)
        currentPoint = { x: values[5], y: values[6] };
        points.push({ ...currentPoint });
      }
      // Ignore Z (close path) for now since our paths are open
    });

    return points;
  } // Circular links
  // Circular links
  const circularLinkGroup = masterGroup
    .selectAll("g.circular-links")
    .data([0])
    .join("g")
    .attr("class", "circular-links")
    .attr("fill", "none")
    .attr("stroke-opacity", 0.5);

  let circularLinks = graph.links.filter((d) => d.circular);
  const circularLink = circularLinkGroup
    .selectAll(".circular-link")
    .data(circularLinks, (d) => `${d.source.id}-${d.target.id}`);

  circularLink
    .exit()
    .transition()
    .duration(ANIM_LINK)
    .attr("fill-opacity", 0)
    .remove();

  const circularLinkEnter = circularLink
    .enter()
    .append("path")
    .attr("class", "circular-link")
    .attr("d", (d) => d.path)
    .attr("fill", "none")
    .attr("stroke", "rgba(255, 105, 180, 0.5)")
    .attr("stroke-opacity", "0.5")
    .attr("stroke-width", (d) => d.width);

  circularLink
    .merge(circularLinkEnter)
    .transition()
    .duration(ANIM_LINK)
    .attr("d", (d) => d.path)
    .attr("stroke", "rgba(255, 105, 180, 0.5)")
    .attr("stroke-opacity", 0.5);

  /*// Add debug points for each circular link
  circularLink.merge(circularLinkEnter).each(function (d) {
    const path = d.path;
    const points = extractPointsFromPath(path);

    // Append circles for each point within the same group
    d3.select(this.parentNode)
      .selectAll("circle.debug-point-" + d.source.id + "-" + d.target.id)
      .data(points)
      .enter()
      .append("circle")
      .attr("class", "debug-point-" + d.source.id + "-" + d.target.id)
      .attr("cx", (p) => p.x)
      .attr("cy", (p) => p.y)
      .attr("r", 3)
      .attr("fill", "black")
      .attr("stroke", "white")
      .attr("stroke-width", 1)
      .append("title")
      .text((p) => `Point (${p.x.toFixed(2)}, ${p.y.toFixed(2)})`);
  });*/

  circularLinkEnter
    .append("title")
    .text(
      (d) =>
        `${d.source.name} → ${d.target.name}\n$${formatNumber(
          d.amt
        )} (Circular)`
    );

  // Node rendering (unchanged)
  const nodeGroup = masterGroup
    .selectAll("g.nodes")
    .data([0])
    .join("g")
    .attr("class", "nodes");

  const nodeElements = nodeGroup
    .selectAll("g.node")
    .data(graph.nodes, (d) => d.id);

  nodeElements
    .exit()
    .transition()
    .duration(ANIM_NODE)
    .attr("transform", "scale(0)")
    .remove();

  const nodeEnter = nodeElements
    .enter()
    .append("g")
    .attr("class", (d) => (d.isTerminal ? "node no-grants" : "node expand"))
    .attr("data-id", (d) => d.id)
    .style("opacity", 0);

  nodeEnter.each(function (d) {
    const sel = d3.select(this);
    sel
      .append("path")
      .attr("stroke", "#000")
      .attr(
        "d",
        d.isTerminal
          ? generateOctagonPath({
              ...d,
              x0: d.previousX0 || d.x0,
              y0: d.previousY0 || d.y0,
              x1: d.previousX1 || d.x1,
              y1: d.previousY1 || d.y1,
            })
          : generateTrapezoidPath({
              ...d,
              x0: d.previousX0 || d.x0,
              y0: d.previousY0 || d.y0,
              x1: d.previousX1 || d.x1,
              y1: d.previousY1 || d.y1,
            })
      )
      .attr("fill", getColorForEIN(d.id))
      .style("cursor", d.isTerminal ? "zoom-in" : "grab")
      .append("title")
      .text((d) => d.toolTipText());
  });

  nodeEnter.transition().duration(ANIM_NODE).style("opacity", 1);

  nodeElements
    .merge(nodeEnter)
    .filter((d) => d.previousX0 !== undefined)
    .select("path")
    .transition()
    .duration(ANIM_NODE)
    .attr("d", (d) =>
      d.isTerminal ? generateOctagonPath(d) : generateTrapezoidPath(d)
    );

  const hatGroup = masterGroup
    .selectAll("g.expand-hats")
    .data([0])
    .join("g")
    .attr("class", "expand-hats");

  const leftHats = hatGroup.selectAll("g.hat-left").data(
    graph.nodes.filter(
      (d) => d.canExpandInflows && d.invisibleGrantsIn.length > 0
    ),
    (d) => `${d.id}-left`
  );

  leftHats
    .exit()
    .filter((d) => d.hasLeftHat)
    .transition()
    .duration(ANIM_HAT)
    .style("opacity", 0)
    .remove();

  const leftHatEnter = leftHats
    .enter()
    .append("g")
    .attr("class", "hat-left")
    .style("opacity", (d) => (d.hasLeftHat ? 1 : 0));

  leftHatEnter
    .append("path")
    .attr("d", (d) =>
      generatePlusPath({ ...d, isRight: false, isTerminal: d.isTerminal })
    )
    .attr("fill", (d) => getColorForEIN(d.id))
    .attr("stroke", "#000")
    .attr("class", "hat-up")
    .style("cursor", "crosshair")
    .append("title")
    .text("expand more inflows");

  leftHats
    .merge(leftHatEnter)
    .transition()
    .duration(ANIM_HAT)
    .style("opacity", (d) =>
      d.canExpandInflows && d.invisibleGrantsIn.length > 0 && !d.hasLeftHat
        ? 1
        : d.hasLeftHat &&
          !(d.canExpandInflows && d.invisibleGrantsIn.length > 0)
        ? 0
        : 1
    )
    .select("path")
    .attr("d", (d) =>
      generatePlusPath({ ...d, isRight: false, isTerminal: d.isTerminal })
    );

  const rightHats = hatGroup.selectAll("g.hat-right").data(
    graph.nodes.filter(
      (d) =>
        !d.isTerminal && d.canExpandOutflows && d.invisibleGrants.length > 0
    ),
    (d) => `${d.id}-right`
  );

  rightHats
    .exit()
    .filter((d) => d.hasRightHat)
    .transition()
    .duration(ANIM_HAT)
    .style("opacity", 0)
    .remove();

  const rightHatEnter = rightHats
    .enter()
    .append("g")
    .attr("class", "hat-right")
    .style("opacity", (d) => (d.hasRightHat ? 1 : 0));

  rightHatEnter
    .append("path")
    .attr("d", (d) => generatePlusPath({ ...d, isRight: true }))
    .attr("fill", (d) => getColorForEIN(d.id))
    .attr("stroke", "#000")
    .attr("class", "hat-down")
    .style("cursor", "crosshair")
    .append("title")
    .text("expand more outflows");

  rightHats
    .merge(rightHatEnter)
    .transition()
    .duration(ANIM_HAT)
    .style("opacity", (d) =>
      !d.isTerminal &&
      d.canExpandOutflows &&
      d.invisibleGrants.length > 0 &&
      !d.hasRightHat
        ? 1
        : d.hasRightHat &&
          !(
            !d.isTerminal &&
            d.canExpandOutflows &&
            d.invisibleGrants.length > 0
          )
        ? 0
        : 1
    )
    .select("path")
    .attr("d", (d) => generatePlusPath({ ...d, isRight: true }));

  const textGroup = masterGroup
    .selectAll("g.text")
    .data([0])
    .join("g")
    .attr("class", "text");

  const text = textGroup.selectAll("text").data(graph.nodes, (d) => d.id);

  text.exit().remove();

  const textEnter = text
    .enter()
    .append("text")
    .attr("dy", "0.35em")
    .attr("x", (d) => (d.x0 < sankey.nodeWidth() / 2 ? d.x1 + 6 : d.x0 - 6))
    .attr("y", (d) => ((d.previousY0 || d.y0) + (d.previousY1 || d.y1)) / 2)
    .attr("text-anchor", (d) =>
      d.x0 < sankey.nodeWidth() / 2 ? "start" : "end"
    )
    .style(
      "font-size",
      (d) => `${Math.max(FONT_SIZE, 10, (d.y1 - d.y0) / FONT_CONSTANT)}px`
    );

  text
    .merge(textEnter)
    .transition()
    .duration(ANIM_TEXT)
    .attr("x", (d) => (d.x0 < sankey.nodeWidth() / 2 ? d.x1 + 6 : d.x0 - 6))
    .attr("y", (d) => (d.y0 + d.y1) / 2)
    .attr("text-anchor", (d) =>
      d.x0 < sankey.nodeWidth() / 2 ? "start" : "end"
    )
    .style(
      "font-size",
      (d) => `${Math.max(FONT_SIZE, 10, (d.y1 - d.y0) / FONT_CONSTANT)}px`
    )
    .text((d) => d.name);

  bindEvents(g);

  viewModel.cleanAfterRender();
  dataLoaded(true);
  updateStatus(`Orgs: ${nodeCount} Flows:${edgeCount} $:${edgeTotal}`);

  const post = encodeURIComponent(
    `Hey, @twinforces @datarepublican, Check this out because:`
  );
  const url = encodeURIComponent(window.location.href);
  const hashtags = encodeURIComponent("DRBadNGOs");
  $("#PostBox").html(
    `<a href="https://x.com/intent/tweet?url=${url}&text=${post}&hashtags=${hashtags}&via=twinforces" 
    target="_blank"  
    title="Share on X" 
    class="x-share-button">&#x1D54F;</a>`
  );
  return currentData;
}

// [Rest of the file unchanged...]
function handleSearch(e) {
  const value = e.target.value.toLowerCase();
  const searchResults = document.getElementById("searchResults");
  const clearButton = document.getElementById("clearSearch");

  if (!value) {
    searchResults.classList.add("hidden");
    clearButton.classList.add("hidden");
    return;
  }

  clearButton.classList.remove("hidden");

  const matches = Object.values(Charity.charityLookup)
    .filter(
      (d) => d.name.toLowerCase().includes(value) || d.ein.includes(value)
    )
    .slice(0, 5);

  if (matches.length > 0) {
    searchResults.innerHTML = matches
      .map(
        (d, index) => `
          <div class="p-2 cursor-pointer ${
            index === 0 ? "bg-blue/10" : ""
          } hover:bg-gray-100" 
               data-ein="${d.ein}" data-index="${index}" 
               onmouseenter="handleSearchResultHover(${index})">
            ${d.name}
          </div>
        `
      )
      .join("");
    searchResults.classList.remove("hidden");
    selectedSearchIndex = 0;
    const firstResult = searchResults.querySelector('[data-index="0"]');
    if (firstResult) firstResult.classList.add("bg-blue/10");
  } else {
    searchResults.classList.add("hidden");
    selectedSearchIndex = -1;
  }
}

function handleSearchBlur() {
  // No specific action needed on blur
}

let selectedSearchIndex = 0;

function handleSearchKeydown(e) {
  const searchResults = document.getElementById("searchResults");
  if (searchResults.classList.contains("hidden")) return;

  const results = searchResults.querySelectorAll("[data-index]");
  const maxIndex = results.length - 1;

  if (maxIndex < 0) {
    selectedSearchIndex = -1;
    return;
  }

  switch (e.key) {
    case "ArrowDown":
      e.preventDefault();
      selectedSearchIndex = Math.min(selectedSearchIndex + 1, maxIndex);
      updateSearchSelection(results);
      break;
    case "ArrowUp":
      e.preventDefault();
      selectedSearchIndex = Math.max(selectedSearchIndex - 1, 0);
      updateSearchSelection(results);
      break;
    case "Enter":
      e.preventDefault();
      if (selectedSearchIndex >= 0) {
        const selectedResult = results[selectedSearchIndex];
        if (selectedResult) handleSearchClick({ target: selectedResult });
      }
      break;
    case "Escape":
      e.preventDefault();
      searchResults.classList.add("hidden");
      e.target.blur();
      break;
  }
}

function handleSearchClick(e) {
  const ein = e.target.getAttribute("data-ein");
  if (ein) {
    viewModel.addToShowList(ein);
    renderActiveEINs();
    updateQueryParams();
    generateGraph();
    document.getElementById("searchResults").classList.add("hidden");
  }
}

function updateSearchSelection(results) {
  results.forEach((result, index) => {
    if (index === selectedSearchIndex) {
      result.classList.add("bg-blue/10");
      result.classList.remove("hover:bg-gray-100");
      result.scrollIntoView({ block: "nearest", behavior: "smooth" });
    } else {
      result.classList.remove("bg-blue/10");
      result.classList.add("hover:bg-gray-100");
    }
  });
}

function handleSearchResultHover(index) {
  selectedSearchIndex = index;
  updateSearchSelection(document.querySelectorAll("[data-index]"));
}

function refresh() {
  updateQueryParams();
  renderActiveEINs();
  renderHideEINs();
  generateGraph();
}

window.porkClick = function (ein) {
  viewModel.porkClick(ein);
  refresh();
};
window.billClick = function (ein) {
  viewModel.billClick(ein);
};

function showControlPanel(type, data, element) {
  const panel = document.getElementById("control-panel");
  let content = "";

  function renderButtons(node, withButtons) {
    if (!withButtons) return "";
    return `
      <div class="flex-1 bg-gray-200 p-4">
        <button onclick="focusNode('${node.ein}')">Focus on This</button>
        <button ${
          !node.canExpandInflows ? 'disabled class="bg-gray-100 disabled"' : ""
        } onclick="expandInflows('${node.ein}')">Show 3 Inflows</button>
        <button ${
          !node.canExpandOutflows ? 'disabled class="bg-gray-100 disabled"' : ""
        } onclick="expandOutflows('${node.ein}')">Expand 3 Outflows</button>
      </div>
      <div class="flex-1 bg-gray-200 p-4">
        <button onclick="removeNode('${node.ein}')">Remove This</button>
        <button ${
          !node.canCompressInflows ? "disabled class='disabled'" : ""
        } onclick="compressInflows('${node.ein}')">Hide 3 Inflows</button>
        <button ${
          !node.canCompressOutflows ? "disabled class='disabled'" : ""
        } onclick="compressOutflows('${node.ein}')">Hide 3 Outflows</button>
      </div>
    `;
  }

  function renderNode(node, withButtons = false) {
    let buttons = renderButtons(node, withButtons);
    let links = "";
    let inflows = "<p>Inflows: N/A</p>";
    let outflows = "<p>Outflows: N/A</p>";
    let hiddenInflows = node.invisibleGrantsIn.length
      ? `<p><i>$${formatNumber(
          node.invisibleGrantsIn.reduce((sum, g) => sum + g.amt, 0)
        )} hidden (${node.invisibleGrantsIn.length} grants)</i></p>`
      : "";
    let hiddenOutflows = node.invisibleGrants.length
      ? `<p><i>$${formatNumber(
          node.invisibleGrants.reduce((sum, g) => sum + g.amt, 0)
        )} hidden (${node.invisibleGrants.length} grants)</i></p>`
      : "";

    if (node.isGov) {
      return `
        <div class="bg-blue-500 text-white flex-col p-4 text-center">
          <h3>${node.name}</h3>
        </div>
        <div class="flex flex-row gap-4">
          <div class="flex-1 bg-gray-200 p-4">
            <p>US Taxpayers: <b>$4.6T</b></p>
            <p>Outflows: $${formatNumber(node.visibleGrantsTotal)} visible (${
        node.visibleGrants.length
      } grants)</p>
            ${hiddenOutflows}
          </div>
          ${buttons}
        </div>
      `;
    } else {
      if (!node.isRoot)
        inflows = `<p>Inflows: $${formatNumber(
          node.visibleGrantsInTotal - node.govt_amt
        )} visible (${
          node.visibleGrantsIn.length
        } grants)</p> ${hiddenInflows}`;
      if (!node.isTerminal)
        outflows = `<p>Outflows: $${formatNumber(
          node.visibleGrantsTotal
        )} visible (${node.visibleGrants.length} grants)</p> ${hiddenOutflows}`;
      links = `
        <p>Direct From US Gov: <b>$${formatNumber(node.govt_amt)}</b></p>
        <p>Find indirect sources: 
           <a onClick="porkClick(${
             node.ein
           })" title="Show USG Indirect (each click does one more level)"><span class="emoji">&#x1F437;</span></a>
           </p>
        <p><a href="${node.financialsLink()}">Show me the Financials</a></p>
        <p><a href="${node.officersLink()}">Show me the Officers</a></p>
        <p><a href="${node.nonprofitsLink()}">Show me the Money!</a></p>
        <p>${node.propublicaLink("Take me to Propublica")}</p>
        <p>${node.googleLink("Google")}</p>
        <!--<p>${node.grokLink("Grok")}</p>-->
      `;
    }

    return `
      <div class="bg-blue-500 text-white flex-col p-4 text-center">
        <h3>${node.name}</h3>
        <p>EIN: ${node.longEIN}</p>
      </div>
      <div class="flex flex-row gap-4">
        <div class="flex-1 bg-gray-200 p-4">
          ${links}
          ${inflows}
          ${outflows}
        </div>
        ${buttons}
      </div>
    `;
  }

  if (type === "node") {
    content = renderNode(data, true);
  } else if (type === "link") {
    content = `
      <div class="flex flex-col gap-4">
        <div class="bg-blue-500 text-white p-4 text-center">
          <h3>Grant Details</h3>
          <p>Amount: $${formatNumber(data.amt)}</p>
        </div>
        <div class="flex flex-row gap-4">
          <div class="flex-1 bg-gray-200 p-4">
            <h4>From:</h4>
            ${renderNode(data.filer)}
            <button onclick="expandOutflows('${
              data.filer.ein
            }')">Expand Source</button>
            <button onclick="compressOutflows('${
              data.filer.ein
            }')">Compress Source</button>
          </div>
          <div class="flex-1 bg-gray-300 p-4">
            <h4>To:</h4>
            ${renderNode(data.grantee)}
            <button onclick="expandInflows('${
              data.grantee.ein
            }')">Expand Target</button>
            <button onclick="compressInflows('${
              data.grantee.ein
            }')">Compress Target</button>
          </div>
        </div>
      </div>
    `;
  }

  panel.innerHTML = content;
  panel.style.display = "block";

  d3.selectAll(".node").classed("selected", false);
  d3.selectAll(".link").classed("selected", false);
  d3.select(element).classed("selected", true);
}

function closePanel() {
  document.getElementById("control-panel").style.display = "none";
  d3.selectAll(".node").classed("selected", false);
  d3.selectAll(".link").classed("selected", false);
}

document.addEventListener("click", closePanel);
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") closePanel();
});

document.getElementById("control-panel").addEventListener("click", (event) => {
  event.stopPropagation();
});

window.removeNode = function (ein) {
  const charity = Charity.getCharity(ein);
  if (charity) {
    charity.hide();
    refresh();
  }
  closePanel();
};

window.expandInflows = function (ein) {
  const charity = Charity.getCharity(ein);
  if (charity) {
    charity.expandInflows();
    refresh();
  }
  closePanel();
};

window.expandOutflows = function (ein) {
  const charity = Charity.getCharity(ein);
  if (charity) {
    charity.expandOutflows();
    refresh();
  }
  closePanel();
};

window.compressInflows = function (ein) {
  const charity = Charity.getCharity(ein);
  if (charity) {
    charity.compressInflows();
    refresh();
  }
  closePanel();
};

window.compressOutflows = function (ein) {
  const charity = Charity.getCharity(ein);
  if (charity) {
    charity.compressOutflows();
    refresh();
  }
  closePanel();
};

window.focusNode = function (ein) {
  const params = new URLSearchParams();
  params.append("ein", ein);
  const newUrl = window.location.pathname + "?" + params.toString();
  window.history.replaceState({}, "", newUrl);
  viewModel.parseQueryParams();
  viewModel.resetAll();
  generateGraph();
  closePanel();
};

const extraStyle = `
  .node { fill: #999; }
  .node.expand { cursor: grab; }
  .node.no-grants { cursor: zoom-in; }
  .link { stroke-opacity: 0.5; }
  .hat-up, .hat-down { cursor: crosshair; }
  text { fill: #000; }
  .selected { stroke: #ff0; stroke-width: 2px; }
`;
d3.select("head").append("style").text(extraStyle);
