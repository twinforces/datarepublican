import {
  Charity,
  Grant,
  formatNumber,
  viewModel,
  BrowseViewModel,
} from "./models.js";

let svg = null;
let zoom = null;

const NODE_WIDTH = 50;
const OTHER_WIDTH = 30;
const NODE_PADDING = 10;
const MIN_LINK_HEIGHT = 5;

const colorScale = d3.scaleOrdinal(d3.schemeCategory10);

function updateStatus(message, color = "black") {
  $("#status").text(message).css("color", color);
}

$(document).ready(function () {
  if (viewModel.dataReady) viewModel.parseQueryParams();

  updateStatus("Loading Data...");
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
    renderActiveEINs();
    updateQueryParams();
    generateGraph();
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
      $btn.text("How it works");
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
  if (!/^\d{9}$/.test(val) && val !== "001") {
    alert("EIN must be 9 digits after removing dashes/spaces or 001.");
    return;
  }
  const charity = Charity.getCharity(val);
  if (!charity) console.warn("EIN not found in charities.csv (still adding).");
  viewModel.addToShowList(val);
  $("#einShowInput").val("");
  renderActiveEINs();
  Charity.placeNode(val);
  updateQueryParams();
  generateGraph();
}

function renderActiveEINs() {
  const $c = $("#activeEINs");
  $c.empty();
  $("#clearEINsBtnShow").toggle(viewModel.getShowList().length > 0);

  viewModel.getShowList().forEach((ein) => {
    const $tag = $(
      '<div class="filter-tag flex items-center gap-0.5 rounded border border-blue bg-blue/10 text-blue rounded-md px-2 py-1 text-xs"></div>'
    );
    const $text = $("<span></span>").text(ein.slice(0, 2) + "-" + ein.slice(2));
    const $rm = $(
      '<span class="remove-filter opacity-50 hover:opacity-100 size-5 -my-0.5 -mr-1 cursor-pointer"><svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24"><path fill="#000" fill-rule="evenodd" d="M2 12C2 6.477 6.477 2 12 2s10 4.477 10 10-4.477 10-10 10S2 17.523 2 12Zm7.53-3.53a.75.75 0 0 0-1.06 1.06L10.94 12l-2.47 2.47a.75.75 0 1 0 1.06 1.06L12 13.06l2.47 2.47a.75.75 0 1 0 1.06-1.06L13.06 12l-2.47-2.47a.75.75 0 0 0-1.06-1.06L12 10.94 9.53 8.47Z" clip-rule="evenodd"/></svg></span>'
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
    const $tag = $(
      '<div class="filter-tag flex items-center gap-0.5 rounded border border-blue bg-blue/10 text-blue rounded-md px-2 py-1 text-xs"></div>'
    );
    const $text = $("<span></span>").text(ein.slice(0, 2) + "-" + ein.slice(2));
    const $rm = $(
      '<span class="remove-filter opacity-50 hover:opacity-100 size-5 -my-0.5 -mr-1 cursor-pointer"><svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24"><path fill="#000" fill-rule="evenodd" d="M2 12C2 6.477 6.477 2 12 2s10 4.477 10 10-4.477 10-10 10S2 17.523 2 12Zm7.53-3.53a.75.75 0 0 0-1.06 1.06L10.94 12l-2.47 2.47a.75.75 0 1 0 1.06 1.06L12 13.06l2.47 2.47a.75.75 0 1 0 1.06-1.06L13.06 12l-2.47-2.47a.75.75 0 0 0-1.06-1.06L12 10.94 9.53 8.47Z" clip-rule="evenodd"/></svg></span>'
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
      '<span class="remove-filter opacity-50 hover:opacity-100 size-5 -my-0.5 -mr-1 cursor-pointer"><svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24"><path fill="#000" fill-rule="evenodd" d="M2 12C2 6.477 6.477 2 12 2s10 4.477 10 10-4.477 10-10 10S2 17.523 2 12Zm7.53-3.53a.75.75 0 0 0-1.06 1.06L10.94 12l-2.47 2.47a.75.75 0 1 0 1.06 1.06L12 13.06l2.47 2.47a.75.75 0 1 0 1.06-1.06L13.06 12l-2.47-2.47a.75.75 0 0 0-1.06-1.06L12 10.94 9.53 8.47Z" clip-rule="evenodd"/></svg></span>'
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
function generateGraph() {
  if (!viewModel.dataReady) {
    alert("Data not loaded yet. Please wait.");
    return;
  }

  $("#loading").show();
  $("#graph-container svg").remove();

  const container = document.getElementById("graph-container");
  const width = container.offsetWidth;
  const height = container.offsetHeight || window.innerHeight * 0.7;

  svg = d3
    .select("#graph-container")
    .append("svg")
    .attr("id", "graph")
    .attr("width", "100%")
    .attr("height", "100%")
    .style("display", "block")
    .style("background", "#fff");

  zoom = d3
    .zoom()
    .scaleExtent([0.1, 4])
    .filter((event) => !event.button && event.type !== "dblclick")
    .on("zoom", (event) => {
      svg.select("g").attr("transform", event.transform); // Update to select g dynamically
    });

  svg.call(zoom);

  let g = svg.append("g").attr("transform", "translate(50, 50)");

  const sankey = d3
    .sankey()
    .nodeId((d) => d.id)
    .nodeWidth(NODE_WIDTH)
    .nodePadding(NODE_PADDING)
    .linkSort(compareLinks)
    .nodeAlign(d3.sankeyCenter)
    .nodeSort(compareCharities)
    .size([width - 100, height - 100]);

  viewModel.parseQueryParams();
  if (!viewModel.matchURL()) viewModel.loadDefaultData();

  renderFocusedSankey(
    g,
    sankey,
    svg,
    width,
    height,
    viewModel.getShowList().length
      ? viewModel.getShowList()
      : [viewModel.GOV_EIN]
  );

  // Re-select g after rendering since it’s recreated in renderFocusedSankey
  g = svg.select("g");
  bindEvents(g);

  // Update zoom controls to use the reselected g
  document.getElementById("zoomIn").onclick = () =>
    svg.transition().duration(300).call(zoom.scaleBy, 1.3);
  document.getElementById("zoomOut").onclick = () =>
    svg.transition().duration(300).call(zoom.scaleBy, 0.7);
  document.getElementById("zoomFit").onclick = () => {
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
    generateGraph();
  };
  document.getElementById("scaleDown").onclick = () => {
    viewModel.graphScaleDown();
    generateGraph();
  };
  document.getElementById("scaleReset").onclick = () => {
    viewModel.graphScaleReset();
    generateGraph();
  };

  setTimeout(() => {
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
  }, 1000);
  renderActiveEINs();
  renderActiveKeywords();
  renderHideEINs();
  $("#loading").hide();
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
      } else if (event.metaKey) showControlPanel("node", d, this);
      else viewModel.clickNode(event, d, refresh);
    })
    .on("dblclick", (event, d) => {
      console.log("Node double-clicked:", d.id);
      event.stopPropagation();
      if (d.isTerminal && !event.shiftKey) {
        d.hideUp();
        Charity.addToHideList(d.ein);
      } else {
        viewModel.doubleClickNode(event, d);
      }
      refresh();
    });
  g.selectAll(".link")
    .on("click", (event, d) => {
      console.log("Link clicked:", d.id);
      event.stopPropagation();
      showControlPanel("link", d, this);
    })
    .on("dblclick", (event, d) => {
      console.log("Link double-clicked:", d.id);
      viewModel.doubleClickGrant(event, d);
      refresh();
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

function generateUniqueId(prefix = "gradient") {
  return `${prefix}-${Math.random().toString(36).substr(2, 9)}`;
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
  const r = d.inflowHeight / ((2 * Math.sqrt(2 + Math.SQRT2)) / 2);

  const c = Math.sqrt(2 + Math.SQRT2) / 2;
  const s = Math.sqrt(2 - Math.SQRT2) / 2;

  return `M${cx + r * c},${cy + r * s} L${cx + r * s},${cy + r * c} L${
    cx - r * s
  },${cy + r * c} L${cx - r * c},${cy + r * s} L${cx - r * c},${cy - r * s} L${
    cx - r * s
  },${cy - r * c} L${cx + r * s},${cy - r * c} L${cx + r * c},${cy - r * s} Z`;
}

function generatePlusPath(d) {
  const radius = OTHER_WIDTH / 2;
  const armWidth = radius * 0.4;
  let cx, circlePath;
  const cy = (d.y0 + d.y1) / 2;

  if (d.isTerminal) {
    const inflowHeight = d.inflowHeight || OTHER_WIDTH;
    cx = d.x0 - inflowHeight / 2;
    circlePath = `M${cx},${cy + radius} A${radius},${radius} 0 0 1 ${cx},${
      cy - radius
    }`; // Bottom to top, left
  } else if (d.isRight) {
    cx = d.x1;
    circlePath = `M${cx},${cy - radius} A${radius},${radius} 0 0 1 ${cx},${
      cy + radius
    }`; // Top to bottom, right
  } else {
    cx = d.x0;
    circlePath = `M${cx},${cy + radius} A${radius},${radius} 0 0 1 ${cx},${
      cy - radius
    }`; // Bottom to top, left
  }

  const plusPath = `
    M${cx - armWidth},${cy} H${cx + armWidth}
    M${cx},${cy - armWidth} V${cy + armWidth}
  `;
  return `${circlePath} ${plusPath}`;
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

function renderFocusedSankey(g, sankey, svgRef, width, height, nodeIds) {
  $("#downloadBtn").hide();
  //if (nodeIds) nodeIds.forEach((nid) => Charity.placeNode(nid));

  let currentData = viewModel.buildSankeyData();
  const graph = sankey(currentData);

  const scale = calculateScale(graph, width, height);
  calculateNodePositions(graph.nodes, scale, height);
  normalizeStrokeWidths(graph);

  // Clear the SVG
  svgRef.selectAll("*").remove();

  // Append defs for gradients
  const defs = svgRef.append("defs");
  graph.links.forEach(
    (link) => (link.gradientId = generateUniqueId("gradient"))
  );
  const gradients = defs
    .selectAll("linearGradient.dynamic")
    .data(graph.links)
    .enter()
    .append("linearGradient")
    .attr("id", (d) => d.gradientId)
    .attr("gradientUnits", "objectBoundingBox")
    .attr("x1", "0")
    .attr("y1", "0.5")
    .attr("x2", "1")
    .attr("y2", "0.5");

  gradients
    .append("stop")
    .attr("offset", "0%")
    .attr("stop-color", (d) => colorScale(d.source.id));
  gradients
    .append("stop")
    .attr("offset", "100%")
    .attr("stop-color", (d) => colorScale(d.target.id));

  // Re-append the group element g
  g = svgRef.append("g").attr("transform", "translate(50, 50)");

  // Append masterGroup to g
  const masterGroup = g
    .append("g")
    .attr("class", "graph-group")
    .attr("transform", `scale(${scale})`);

  // Links
  const link = masterGroup
    .append("g")
    .attr("fill", "none")
    .attr("stroke-opacity", 1)
    .style("mix-blend-mode", "multiply")
    .selectAll(".link")
    .data(graph.links)
    .join("path")
    .attr("d", sankeyLinkHorizontalTrapezoid())
    .attr("stroke", (d) => {
      console.log("Link visibility:", d.id, d.isVisible);
      return d.isVisible ? `url(#${d.gradientId})` : "#d3d3d3";
    })
    .style("stroke-opacity", "0.3")
    .attr("stroke-width", (d) => d.width);

  link
    .append("title")
    .text(
      (d) => `${d.source.name} → ${d.target.name}\n$${formatNumber(d.amt)}`
    );

  // Nodes
  const nodeGroup = masterGroup.append("g").attr("class", "nodes");
  const nodeElements = nodeGroup
    .selectAll("g")
    .data(graph.nodes)
    .join("g")
    .attr("class", (d) => (d.isTerminal ? "node no-grants" : "node expand"))
    .attr("data-id", (d) => d.id);

  nodeElements.each(function (d) {
    const sel = d3.select(this);
    if (d.isTerminal) {
      sel
        .append("path")
        .attr("d", generateOctagonPath)
        .attr("fill", (d) => colorScale(d.id))
        .style("cursor", "zoom-out")
        .attr("stroke", "#000")
        .append("title")
        .text((d) => d.toolTipText());
    } else {
      sel
        .append("path")
        .attr("d", generateTrapezoidPath)
        .attr("fill", (d) => colorScale(d.id))
        .style("cursor", "grab")
        .attr("stroke", "#000")
        .append("title")
        .text((d) => d.toolTipText());
    }
  });

  // Hats
  const hatGroup = masterGroup.append("g").attr("class", "expand-hats");
  const hats = hatGroup
    .selectAll("g")
    .data(
      graph.nodes.filter(
        (d) =>
          (d.canExpandInflows && d.invisibleGrantsIn.length > 0) ||
          (!d.isTerminal && d.canExpandOutflows && d.invisibleGrants.length > 0)
      )
    )
    .join("g")
    .attr("class", "hat");

  hats.each(function (d) {
    const sel = d3.select(this);
    if (d.canExpandInflows && d.invisibleGrantsIn.length > 0) {
      sel
        .append("path")
        .attr(
          "d",
          generatePlusPath({ ...d, isRight: false, isTerminal: d.isTerminal })
        )
        .attr("fill", "#ccc")
        .attr("stroke", "#000")
        .attr("class", "hat-up")
        .style("cursor", "pointer");
    }
    if (!d.isTerminal && d.canExpandOutflows && d.invisibleGrants.length > 0) {
      sel
        .append("path")
        .attr("d", generatePlusPath({ ...d, isRight: true }))
        .attr("fill", "#ccc")
        .attr("stroke", "#000")
        .attr("class", "hat-down")
        .style("cursor", "pointer");
    }
  });

  // Text
  masterGroup
    .append("g")
    .selectAll("text")
    .data(graph.nodes)
    .join("text")
    .attr("x", (d) => (d.x0 < sankey.nodeWidth() / 2 ? d.x1 + 6 : d.x0 - 6))
    .attr("y", (d) => (d.y0 + d.y1) / 2)
    .attr("dy", "0.35em")
    .attr("text-anchor", (d) =>
      d.x0 < sankey.nodeWidth() / 2 ? "start" : "end"
    )
    .on("click", (event, d) => {
      console.log("Node clicked:", d.id);
      event.stopPropagation();
      viewModel.clickNode(event, d, refresh);
    })
    .on("dblClick", (event, d) => {
      console.log("Node clicked:", d.id);
      event.stopPropagation();
      viewModel.doubleClickNode(event, d, refresh);
    })
    .text((d) => d.name);

  /*masterGroup
    .append("g")
    .selectAll("text")
    .data(graph.links)
    .join("text")
    .attr("x", (d) => {
      return (d.source.x1 + d.target.x0) / 2;
    })
    .attr("y", (d) => (d.y0 + d.y1) / 2)
    .attr("dy", "0.35em")
    .attr("text-anchor", "center")
    .on("click", (event, d) => {
      console.log("Node clicked:", d.id);
      event.stopPropagation();
      viewModel.clickNode(event, d, refresh);
    })
    .on("dblClick", (event, d) => {
      console.log("Node clicked:", d.id);
      event.stopPropagation();
      viewModel.doubleClickLink(event, d, refresh);
    })
    .text((d) => formatNumber(d.amt));*/

  viewModel.cleanAfterRender();
  $("#downloadBtn").show();
}

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
  renderHideEINs();
  updateQueryParams();
  generateGraph();
}

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
        } onclick="expandInflows('${node.ein}')">Expand Inflows</button>
        <button ${
          !node.canExpandOutflows ? 'disabled class="bg-gray-100 disabled"' : ""
        } onclick="expandOutflows('${node.ein}')">Expand Outflows</button>
      </div>
      <div class="flex-1 bg-gray-200 p-4">
        <button onclick="removeNode('${node.ein}')">Remove Node</button>
        <button ${
          !node.canCompressInflows ? "disabled class='disabled'" : ""
        } onclick="compressInflows('${node.ein}')">Compress Inflows</button>
        <button ${
          !node.canCompressOutflows ? "disabled class='disabled'" : ""
        } onclick="compressOutflows('${node.ein}')">Compress Outflows</button>
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
          <p>EIN: ${node.ein}</p>
        </div>
        <div class="flex flex-row gap-4">
          <div class="flex-1 bg-gray-200 p-4">
            <p>US Taxpayers: <b>${formatNumber(node.origOut)}</b></p>
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
        <p>From US Gov: <b>$${formatNumber(node.govt_amt)}</b></p>
        <p><a href="${node.financialsLink()}">Show me the Financials</a></p>
        <p><a href="${node.officersLink()}">Show me the Officers</a></p>
        <p><a href="${node.nonprofitsLink()}">Show me the Money!</a></p>
        <p>${node.propublicaLink("Take me to Propublica")}</p>
      `;
    }

    return `
      <div class="bg-blue-500 text-white flex-col p-4 text-center">
        <h3>${node.name}</h3>
        <p>EIN: ${node.ein}</p>
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
  generateGraph();
  closePanel();
};

const extraStyle = `
  .node { fill: #999; }
  .node.expand { cursor: grab; }
  .node.no-grants { cursor: zoom-out; }
  .link { stroke-opacity: 0.5; }
  .hat-up, .hat-down { cursor: pointer; }
  #graph { background: #fff !important; }
  text { fill: #000; }
  svg { background: #fff !important; }
  .selected { stroke: #ff0; stroke-width: 2px; }
`;
d3.select("head").append("style").text(extraStyle);
