import { findCircuits } from "./circleMath.js";

// Use global d3 from CDN
const d3Array = window.d3;
const d3Shape = window.d3;

// For a given link, return the target node's depth
function targetDepth(d) {
  return d.target.depth;
}

// The depth of a node when the nodeAlign (align) is set to 'left'
function left(node) {
  return node.depth;
}

// The depth of a node when the nodeAlign (align) is set to 'right'
function right(node, n) {
  return n - 1 - node.height;
}

// The depth of a node when the nodeAlign (align) is set to 'justify'
function justify(node, n) {
  return node.sourceLinks.length ? node.depth : n - 1;
}

// The depth of a node when the nodeAlign (align) is set to 'center'
function center(node) {
  return node.targetLinks.length
    ? node.depth
    : node.sourceLinks.length
    ? d3Array.min(node.sourceLinks, targetDepth) - 1
    : 0;
}

// Returns a function, using the parameter given to the sankey setting
function constant(x) {
  return function () {
    return x;
  };
}

function ascendingSourceBreadth(a, b) {
  return ascendingBreadth(a.source, b.source) || a.index - b.index;
}

function ascendingTargetBreadth(a, b) {
  return ascendingBreadth(a.target, b.target) || a.index - b.index;
}

function ascendingBreadth(a, b) {
  if (a.partOfCycle === b.partOfCycle) {
    return a.y0 - b.y0;
  } else {
    if (a.circularLinkType === "top" || b.circularLinkType === "bottom") {
      return -1;
    } else {
      return 1;
    }
  }
}

function value(d) {
  return d.value;
}

function defaultId(d) {
  return d.index;
}

function defaultNodes(graph) {
  return graph.nodes;
}

function defaultLinks(graph) {
  return graph.links;
}

function find(nodeById, id) {
  const node = nodeById.get(id);
  if (!node) throw new Error("missing: " + id);
  return node;
}

function sankeyWithCircles() {
  let x0 = 0,
    y0 = 0,
    x1 = 1,
    y1 = 1; // extent
  let dx = 24; // nodeWidth
  let dy = 20,
    py; // nodePadding
  let id = defaultId;
  let align = justify;
  let sort;
  let linkSort;
  let nodes = defaultNodes;
  let links = defaultLinks;
  let iterations = 6;
  let circularLinkGap = 1;

  // Constants for circular link calculations
  const verticalMargin = 5;
  const baseRadius = 3;

  function computeLinkBreadths({ nodes }) {
    for (const node of nodes) {
      let y0 = node.y0;
      let y1 = y0;
      for (const link of node.sourceLinks) {
        link.y0 = y0 + link.width / 2;
        y0 += link.width;
      }
      for (const link of node.targetLinks) {
        link.y1 = y1 + link.width / 2;
        y1 += link.width;
      }
    }
  }

  function computeNodeLinks({ nodes, links }) {
    for (const [i, node] of nodes.entries()) {
      node.index = i;
      node.sourceLinks = [];
      node.targetLinks = [];
    }
    const nodeById = new Map(nodes.map((d, i) => [id(d, i, nodes), d]));
    for (const [i, link] of links.entries()) {
      link.index = i;
      let { source, target } = link;
      if (!source || !target) {
        throw new Error(
          `Link at index ${i} is missing source or target property`
        );
      }
      if (typeof source !== "object")
        source = link.source = find(nodeById, source);
      if (typeof target !== "object")
        target = link.target = find(nodeById, target);
      source.sourceLinks.push(link);
      target.targetLinks.push(link);
    }
    if (linkSort != null) {
      for (const { sourceLinks, targetLinks } of nodes) {
        sourceLinks.sort(linkSort);
        targetLinks.sort(linkSort);
      }
    }
  }

  function computeNodeValues({ nodes }) {
    for (const node of nodes) {
      node.partOfCycle = false;
      node.value =
        node.fixedValue === undefined
          ? Math.max(
              d3Array.sum(node.sourceLinks, value),
              d3Array.sum(node.targetLinks, value)
            )
          : node.fixedValue;
      node.sourceLinks.forEach(function (link) {
        if (link.circular) {
          node.partOfCycle = true;
          node.circularLinkType = link.circularLinkType;
        }
      });
      node.targetLinks.forEach(function (link) {
        if (link.circular) {
          node.partOfCycle = true;
          node.circularLinkType = link.circularLinkType;
        }
      });
    }
  }

  function computeNodeDepths({ nodes }) {
    const n = nodes.length;
    let current = new Set(nodes);
    let next = new Set();
    let x = 0;
    while (current.size) {
      for (const node of current) {
        node.depth = x;
        for (const link of node.sourceLinks) {
          if (!link.circular && !next.has(link.target)) {
            next.add(link.target);
          }
        }
      }
      if (++x > n) throw new Error("circular link in depth computation");
      current = next;
      next = new Set();
    }
    for (const node of nodes) {
      if (node.depth === undefined) {
        node.depth = 0;
      }
    }
  }

  function computeNodeHeights({ nodes }) {
    const n = nodes.length;
    let current = new Set(nodes);
    let next = new Set();
    let x = 0;
    while (current.size) {
      for (const node of current) {
        node.height = x;
        for (const link of node.targetLinks) {
          if (!link.circular && !next.has(link.source)) {
            next.add(link.source);
          }
        }
      }
      if (++x > n) throw new Error("circular link in height computation");
      current = next;
      next = new Set();
    }
    for (const node of nodes) {
      if (node.height === undefined) {
        node.height = 0;
      }
    }
  }

  function computeNodeLayers({ nodes }) {
    const x = d3Array.max(nodes, (d) => d.depth) + 1 || 1; // Number of columns
    const columns = new Array(x);
    for (const node of nodes) {
      // Directly use depth to avoid issues with align function
      let i =
        node.depth !== undefined && !isNaN(node.depth)
          ? Math.max(0, Math.min(x - 1, node.depth))
          : 0;
      node.layer = i;
      if (!columns[i]) columns[i] = [];
      columns[i].push(node);
    }
    if (sort) {
      for (const column of columns) {
        if (column) column.sort(sort);
      }
    }
    return columns;
  }

  function getCircleMargins(graph) {
    let totalTopLinksWidth = 0,
      totalBottomLinksWidth = 0;
    const maxColumn = d3Array.max(graph.nodes, (node) => node.layer) || 0;
    graph.links.forEach((link) => {
      if (link.circular) {
        if (link.circularLinkType === "top") {
          totalTopLinksWidth += link.width;
        } else {
          totalBottomLinksWidth += link.width;
        }
      }
    });
    totalTopLinksWidth =
      totalTopLinksWidth > 0
        ? totalTopLinksWidth + verticalMargin + baseRadius
        : 0;
    totalBottomLinksWidth =
      totalBottomLinksWidth > 0
        ? totalBottomLinksWidth + verticalMargin + baseRadius
        : 0;
    return { top: totalTopLinksWidth, bottom: totalBottomLinksWidth };
  }

  function scaleSankeySize(graph) {
    const margin = getCircleMargins(graph);
    const currentHeight = y1 - y0;
    const newHeight = currentHeight + margin.top + margin.bottom;
    const scaleY = currentHeight / newHeight;
    y0 = y0 * scaleY + margin.top;
    y1 = y1 * scaleY;
    return { scaleY, margin };
  }

  function initializeNodeBreadths(columns) {
    const ky = d3Array.min(columns, (c) =>
      c ? (y1 - y0 - (c.length - 1) * py) / d3Array.sum(c, value) : Infinity
    );
    for (const nodes of columns) {
      if (!nodes) continue;
      let y = y0;
      for (const node of nodes) {
        node.y0 = y;
        node.y1 = y + node.value * ky;
        y = node.y1 + py;
        for (const link of node.sourceLinks) {
          link.width = link.value * ky;
        }
      }
      y = (y1 - y + py) / (nodes.length + 1);
      for (let i = 0; i < nodes.length; ++i) {
        const node = nodes[i];
        node.y0 += y * (i + 1);
        node.y1 += y * (i + 1);
      }
      reorderLinks(nodes);
    }
    return ky;
  }

  function computeNodeBreadths(graph) {
    const columns = computeNodeLayers(graph);
    py = Math.min(
      dy,
      (y1 - y0) / (d3Array.max(columns, (c) => (c ? c.length : 0)) - 1)
    );
    if (isNaN(py) || py <= 0) py = dy;

    // Initialize x-positions based on columns
    const kx = (x1 - x0 - dx) / (columns.length - 1 || 1);
    columns.forEach((column, i) => {
      if (!column) return;
      column.forEach((node) => {
        node.x0 = x0 + i * kx;
        node.x1 = node.x0 + dx;
      });
    });

    const ky = initializeNodeBreadths(columns);
    const { scaleY, margin } = scaleSankeySize(graph);
    const adjustedKy = ky * scaleY;
    graph.links.forEach((link) => {
      link.width = link.value * adjustedKy;
    });

    // Relaxation steps from v7
    for (let i = 0; i < iterations; ++i) {
      const alpha = Math.pow(0.99, i);
      const beta = Math.max(1 - alpha, (i + 1) / iterations);
      relaxRightToLeft(columns, alpha, beta);
      relaxLeftToRight(columns, alpha, beta);
    }

    // Resolve collisions (already present in your code)
    columns.forEach((column) => {
      if (column) resolveCollisions(column, 1);
    });

    // Compute link breadths and circular path data
    computeLinkBreadths(graph);
    return margin;
  }

  function relaxLeftToRight(columns, alpha, beta) {
    for (let i = 1, n = columns.length; i < n; ++i) {
      const column = columns[i];
      if (!column) continue;
      for (const target of column) {
        let y = 0;
        let w = 0;
        for (const { source, value } of target.targetLinks) {
          let v = value * (target.layer - source.layer);
          y += targetTop(source, target) * v;
          w += v;
        }
        if (!(w > 0)) continue;
        let dy = (y / w - target.y0) * alpha;
        target.y0 += dy;
        target.y1 += dy;
        reorderNodeLinks(target);
      }
      if (sort === undefined) column.sort(ascendingBreadth);
      resolveCollisions(column, beta);
    }
  }

  function relaxRightToLeft(columns, alpha, beta) {
    for (let n = columns.length, i = n - 2; i >= 0; --i) {
      const column = columns[i];
      if (!column) continue;
      for (const source of column) {
        let y = 0;
        let w = 0;
        for (const { target, value } of source.sourceLinks) {
          let v = value * (target.layer - source.layer);
          y += sourceTop(source, target) * v;
          w += v;
        }
        if (!(w > 0)) continue;
        let dy = (y / w - source.y0) * alpha;
        source.y0 += dy;
        source.y1 += dy;
        reorderNodeLinks(source);
      }
      if (sort === undefined) column.sort(ascendingBreadth);
      resolveCollisions(column, beta);
    }
  }

  function resolveCollisions(nodes, alpha) {
    const i = nodes.length >> 1;
    const subject = nodes[i];
    resolveCollisionsBottomToTop(nodes, subject.y0 - py, i - 1, alpha);
    resolveCollisionsTopToBottom(nodes, subject.y1 + py, i + 1, alpha);
    resolveCollisionsBottomToTop(nodes, y1, nodes.length - 1, alpha);
    resolveCollisionsTopToBottom(nodes, y0, 0, alpha);
  }

  function resolveCollisionsTopToBottom(nodes, y, i, alpha) {
    for (; i < nodes.length; ++i) {
      const node = nodes[i];
      const dy = (y - node.y0) * alpha;
      if (dy > 1e-6) (node.y0 += dy), (node.y1 += dy);
      y = node.y1 + py;
    }
  }

  function resolveCollisionsBottomToTop(nodes, y, i, alpha) {
    for (; i >= 0; --i) {
      const node = nodes[i];
      const dy = (node.y1 - y) * alpha;
      if (dy > 1e-6) (node.y0 -= dy), (node.y1 -= dy);
      y = node.y0 - py;
    }
  }

  function reorderNodeLinks({ sourceLinks, targetLinks }) {
    if (linkSort === undefined) {
      for (const {
        source: { sourceLinks },
      } of targetLinks) {
        sourceLinks.sort(ascendingTargetBreadth);
      }
      for (const {
        target: { targetLinks },
      } of sourceLinks) {
        targetLinks.sort(ascendingSourceBreadth);
      }
    }
  }

  function reorderLinks(nodes) {
    if (linkSort === undefined) {
      for (const { sourceLinks, targetLinks } of nodes) {
        sourceLinks.sort(ascendingTargetBreadth);
        targetLinks.sort(ascendingSourceBreadth);
      }
    }
  }

  function targetTop(source, target) {
    let y = source.y0 - ((source.sourceLinks.length - 1) * py) / 2;
    for (const { target: node, width } of source.sourceLinks) {
      if (node === target) break;
      y += width + py;
    }
    for (const { source: node, width } of target.targetLinks) {
      if (node === source) break;
      y -= width;
    }
    return y;
  }

  function sourceTop(source, target) {
    let y = target.y0 - ((target.targetLinks.length - 1) * py) / 2;
    for (const { source: node, width } of target.targetLinks) {
      if (node === source) break;
      y += width + py;
    }
    for (const { target: node, width } of source.sourceLinks) {
      if (node === target) break;
      y -= width;
    }
    return y;
  }

  function identifyCircles(graph, id, sortNodes) {
    let circularLinkID = 0;
    const adjList = [];
    let processedLinks = 0;
    for (const link of graph.links) {
      processedLinks++;
      if (!link.source || !link.target) {
        link.circular = false;
        continue;
      }
      const source = link.source.index;
      const target = link.target.index;
      if (source === undefined || target === undefined) {
        link.circular = false;
        continue;
      }

      if (!adjList[source]) adjList[source] = [];
      if (!adjList[target]) adjList[target] = [];
      if (adjList[source].indexOf(target) === -1) {
        adjList[source].push(target);
      } else {
        console.log(
          `Edge ${source} -> ${target} already exists in adjList[${source}]:`,
          adjList[source]
        );
      }
    }
    const cycles = findCircuits(adjList);

    // Assign a unique circularLinkID to each cycle
    const cycleToId = new Map();
    const cycleEdgesMap = new Map(); // Map cycle ID to its edges
    cycles.forEach((cycle, cycleIndex) => {
      const cycleId = cycleIndex; // Use cycle index as the unique ID
      cycleToId.set(cycle.join(","), cycleId);
      const cycleEdges = new Set();
      for (let i = 0; i < cycle.length - 1; i++) {
        const source = cycle[i];
        const target = cycle[i + 1];
        cycleEdges.add(`${source},${target}`);
      }
      cycleEdgesMap.set(cycleId, cycleEdges);
    });

    graph.links.forEach((link) => {
      if (!link.source || !link.target) {
        link.circular = false;
        return;
      }
      const source = link.source.index;
      const target = link.target.index;
      let foundCycleId = null;
      for (const [cycleId, edges] of cycleEdgesMap) {
        if (edges.has(`${source},${target}`)) {
          foundCycleId = cycleId;
          break;
        }
      }
      if (foundCycleId !== null) {
        link.circular = true;
        link.circularLinkID = foundCycleId; // Assign the cycle ID
      } else {
        link.circular = false;
        link.circularLinkID = undefined;
      }
    });

    console.log("Links after identifyCircles:");
    graph.links.forEach((link) => {
      console.log(
        `Link ${link.source.id} -> ${link.target.id}: circular=${link.circular}, circularLinkID=${link.circularLinkID}`
      );
    });
  }

  function selectCircularLinkTypes(graph, id) {
    graph.links.forEach((link) => {
      if (link.circular) {
        // Compute the midpoint of the source node
        const sourceMidY = (link.source.y0 + link.source.y1) / 2;
        const sourceY = link.y0;

        // Assign "top" or "bottom" based on the link's position relative to the source node's midpoint
        if (sourceY < sourceMidY) {
          link.circularLinkType = "top";
        } else {
          link.circularLinkType = "bottom";
        }

        // If source and target are the same node, ensure consistency
        if (id(link.source) === id(link.target)) {
          link.circularLinkType = link.circularLinkType; // Already set, just for clarity
        }
      }
    });

    // Optionally sort links to avoid overlap (already handled in addCircularPathData)
    const topLinks = graph.links.filter(
      (l) => l.circular && l.circularLinkType === "top"
    );
    const bottomLinks = graph.links.filter(
      (l) => l.circular && l.circularLinkType === "bottom"
    );
    topLinks.sort((a, b) => a.y0 - b.y0);
    bottomLinks.sort((a, b) => b.y0 - a.y0);
  }
  function addCircularPathData(graph, circularLinkGap, y1, id) {
    //var baseRadius = 10
    var buffer = 20; // left/right buffer, need room for hats.
    //var verticalMargin = 25

    var minY = d3Array.min(graph.links, function (link) {
      return link.source.y0;
    });
    var maxY = d3Array.max(graph.links, function (link) {
      return link.source.y1;
    });

    // create object for circular Path Data
    graph.links.forEach(function (link) {
      if (link.circular) {
        link.circularPathData = {};
      }
    });

    // calc vertical offsets per top/bottom links
    var topLinks = graph.links.filter(function (l) {
      return l.circularLinkType == "top";
    });
    /* topLinks = */ calcVerticalBuffer(topLinks, circularLinkGap, id);

    var bottomLinks = graph.links.filter(function (l) {
      return l.circularLinkType == "bottom";
    });
    /* bottomLinks = */ calcVerticalBuffer(bottomLinks, circularLinkGap, id);

    // add the base data for each link
    graph.links.forEach(function (link) {
      if (link.circular) {
        link.circularPathData.arcRadius = link.width + baseRadius;
        link.circularPathData.leftNodeBuffer = buffer;
        link.circularPathData.rightNodeBuffer = buffer;
        link.circularPathData.sourceWidth = link.source.x1 - link.source.x0;
        link.circularPathData.sourceX =
          link.source.x0 + link.circularPathData.sourceWidth;
        link.circularPathData.targetX = link.target.x0;
        link.circularPathData.sourceY = link.y0;
        link.circularPathData.targetY = link.y1;

        // else calculate normally
        // add left extent coordinates, based on links with same source column and circularLink type
        var thisColumn = link.source.column;
        var thisCircularLinkType = link.circularLinkType;
        var sameColumnLinks = graph.links.filter(function (l) {
          return (
            l.source.column == thisColumn &&
            l.circularLinkType == thisCircularLinkType
          );
        });

        var radiusOffset = 0;
        sameColumnLinks.forEach(function (l, i) {
          if (l.circularLinkID == link.circularLinkID) {
            link.circularPathData.leftSmallArcRadius =
              baseRadius + link.width / 2 + radiusOffset;
            link.circularPathData.leftLargeArcRadius =
              baseRadius + link.width / 2 + i * circularLinkGap + radiusOffset;
          }
          radiusOffset = radiusOffset + l.width;
        });

        // add right extent coordinates, based on links with same target column and circularLink type
        thisColumn = link.target.column;
        sameColumnLinks = graph.links.filter(function (l) {
          return (
            l.target.column == thisColumn &&
            l.circularLinkType == thisCircularLinkType
          );
        });

        radiusOffset = 0;
        sameColumnLinks.forEach(function (l, i) {
          if (l.circularLinkID == link.circularLinkID) {
            link.circularPathData.rightSmallArcRadius =
              baseRadius + link.width / 2 + radiusOffset;
            link.circularPathData.rightLargeArcRadius =
              baseRadius + link.width / 2 + i * circularLinkGap + radiusOffset;
          }
          radiusOffset = radiusOffset + l.width;
        });

        // bottom links
        if (link.circularLinkType == "bottom") {
          link.circularPathData.verticalFullExtent =
            maxY + verticalMargin + link.circularPathData.verticalBuffer;
          link.circularPathData.verticalLeftInnerExtent =
            link.circularPathData.verticalFullExtent -
            link.circularPathData.leftLargeArcRadius;
          link.circularPathData.verticalRightInnerExtent =
            link.circularPathData.verticalFullExtent -
            link.circularPathData.rightLargeArcRadius;
        } else {
          // top links
          link.circularPathData.verticalFullExtent =
            minY - verticalMargin - link.circularPathData.verticalBuffer;
          link.circularPathData.verticalLeftInnerExtent =
            link.circularPathData.verticalFullExtent +
            link.circularPathData.leftLargeArcRadius;
          link.circularPathData.verticalRightInnerExtent =
            link.circularPathData.verticalFullExtent +
            link.circularPathData.rightLargeArcRadius;
        }

        // all links
        link.circularPathData.leftInnerExtent =
          link.circularPathData.sourceX + link.circularPathData.leftNodeBuffer;
        link.circularPathData.rightInnerExtent =
          link.circularPathData.targetX - link.circularPathData.rightNodeBuffer;
        link.circularPathData.leftFullExtent =
          link.circularPathData.sourceX +
          link.circularPathData.leftLargeArcRadius +
          link.circularPathData.leftNodeBuffer;
        link.circularPathData.rightFullExtent =
          link.circularPathData.targetX -
          link.circularPathData.rightLargeArcRadius -
          link.circularPathData.rightNodeBuffer;
      }

      if (link.circular) {
        link.path = createCircularPathStringArc(link);
      }
    });
  }

  // create a d path using the addCircularPathData
  function createCircularPathStringArc(link) {
    var pathString = "";
    // 'pathData' is assigned a value but never used
    // var pathData = {}

    if (link.circularLinkType == "top") {
      pathString =
        // start at the right of the source node
        "M" +
        link.circularPathData.sourceX +
        " " +
        link.circularPathData.sourceY +
        " " +
        // line right to buffer point
        "L" +
        link.circularPathData.leftInnerExtent +
        " " +
        link.circularPathData.sourceY +
        " " +
        // Arc around: Centre of arc X and  //Centre of arc Y
        "A" +
        link.circularPathData.leftLargeArcRadius +
        " " +
        link.circularPathData.leftSmallArcRadius +
        " 0 0 0 " +
        // End of arc X //End of arc Y
        link.circularPathData.leftFullExtent +
        " " +
        (link.circularPathData.sourceY -
          link.circularPathData.leftSmallArcRadius) +
        " " + // End of arc X
        // line up to buffer point
        "L" +
        link.circularPathData.leftFullExtent +
        " " +
        link.circularPathData.verticalLeftInnerExtent +
        " " +
        // Arc around: Centre of arc X and  //Centre of arc Y
        "A" +
        link.circularPathData.leftLargeArcRadius +
        " " +
        link.circularPathData.leftLargeArcRadius +
        " 0 0 0 " +
        // End of arc X //End of arc Y
        link.circularPathData.leftInnerExtent +
        " " +
        link.circularPathData.verticalFullExtent +
        " " + // End of arc X
        // line left to buffer point
        "L" +
        link.circularPathData.rightInnerExtent +
        " " +
        link.circularPathData.verticalFullExtent +
        " " +
        // Arc around: Centre of arc X and  //Centre of arc Y
        "A" +
        link.circularPathData.rightLargeArcRadius +
        " " +
        link.circularPathData.rightLargeArcRadius +
        " 0 0 0 " +
        // End of arc X //End of arc Y
        link.circularPathData.rightFullExtent +
        " " +
        link.circularPathData.verticalRightInnerExtent +
        " " + // End of arc X
        // line down
        "L" +
        link.circularPathData.rightFullExtent +
        " " +
        (link.circularPathData.targetY -
          link.circularPathData.rightSmallArcRadius) +
        " " +
        // Arc around: Centre of arc X and  //Centre of arc Y
        "A" +
        link.circularPathData.rightLargeArcRadius +
        " " +
        link.circularPathData.rightSmallArcRadius +
        " 0 0 0 " +
        // End of arc X //End of arc Y
        link.circularPathData.rightInnerExtent +
        " " +
        link.circularPathData.targetY +
        " " + // End of arc X
        // line to end
        "L" +
        link.circularPathData.targetX +
        " " +
        link.circularPathData.targetY;
    } else {
      // bottom path
      pathString =
        // start at the right of the source node
        "M" +
        link.circularPathData.sourceX +
        " " +
        link.circularPathData.sourceY +
        " " +
        // line right to buffer point
        "L" +
        link.circularPathData.leftInnerExtent +
        " " +
        link.circularPathData.sourceY +
        " " +
        // Arc around: Centre of arc X and  //Centre of arc Y
        "A" +
        link.circularPathData.leftLargeArcRadius +
        " " +
        link.circularPathData.leftSmallArcRadius +
        " 0 0 1 " +
        // End of arc X //End of arc Y
        link.circularPathData.leftFullExtent +
        " " +
        (link.circularPathData.sourceY +
          link.circularPathData.leftSmallArcRadius) +
        " " + // End of arc X
        // line down to buffer point
        "L" +
        link.circularPathData.leftFullExtent +
        " " +
        link.circularPathData.verticalLeftInnerExtent +
        " " +
        // Arc around: Centre of arc X and  //Centre of arc Y
        "A" +
        link.circularPathData.leftLargeArcRadius +
        " " +
        link.circularPathData.leftLargeArcRadius +
        " 0 0 1 " +
        // End of arc X //End of arc Y
        link.circularPathData.leftInnerExtent +
        " " +
        link.circularPathData.verticalFullExtent +
        " " + // End of arc X
        // line left to buffer point
        "L" +
        link.circularPathData.rightInnerExtent +
        " " +
        link.circularPathData.verticalFullExtent +
        " " +
        // Arc around: Centre of arc X and  //Centre of arc Y
        "A" +
        link.circularPathData.rightLargeArcRadius +
        " " +
        link.circularPathData.rightLargeArcRadius +
        " 0 0 1 " +
        // End of arc X //End of arc Y
        link.circularPathData.rightFullExtent +
        " " +
        link.circularPathData.verticalRightInnerExtent +
        " " + // End of arc X
        // line up
        "L" +
        link.circularPathData.rightFullExtent +
        " " +
        (link.circularPathData.targetY +
          link.circularPathData.rightSmallArcRadius) +
        " " +
        // Arc around: Centre of arc X and  //Centre of arc Y
        "A" +
        link.circularPathData.rightLargeArcRadius +
        " " +
        link.circularPathData.rightSmallArcRadius +
        " 0 0 1 " +
        // End of arc X //End of arc Y
        link.circularPathData.rightInnerExtent +
        " " +
        link.circularPathData.targetY +
        " " + // End of arc X
        // line to end
        "L" +
        link.circularPathData.targetX +
        " " +
        link.circularPathData.targetY;
    }

    return pathString;
  }
  // customized to assume trapezoidal destinations
  function addCircularPathDataOrig(graph, circularLinkGap, y1, id) {
    const buffer = 5;
    const minY = d3Array.min(graph.nodes, (n) => n.y0) || y0;
    const maxY = d3Array.max(graph.nodes, (n) => n.y1) || y1;

    graph.links.forEach((link) => {
      link.circularPathData = link.circularPathData || {};
    });

    const topLinks = graph.links.filter((l) => l.circularLinkType === "top");
    calcVerticalBuffer(topLinks, circularLinkGap, id);

    const bottomLinks = graph.links.filter(
      (l) => l.circularLinkType === "bottom"
    );
    calcVerticalBuffer(bottomLinks, circularLinkGap, id);

    // Compute initial stacking offsets
    let globalTopOffset = 0; // Global offset for "top" links (vertical extent)
    let globalBottomOffset = 0; // Global offset for "bottom" links (vertical extent)
    let globalRightXOffset = 0; // Global x-offset for staggering right vertical segments
    let globalLeftXOffset = 0; // Global x-offset for staggering left vertical segments
    const padding = 10; // Padding between paths (for both x and y offsets)
    const leftEdgePadding = 20; // Desired padding from the left edge of the graph

    // Calculate the maximum width of circular links to adjust the initial left offset
    let circularLinks = graph.links.filter((l) => l.circular);
    const maxCircularWidth = d3Array.max(circularLinks, (l) => l.width) || 0;
    globalLeftXOffset = -leftEdgePadding + maxCircularWidth / 2; // Adjust initial offset to ensure leftmost edge is padded

    // Post-process cycles: unmark smaller-width links as circular (already implemented)
    const cycles = new Map();
    circularLinks.forEach((link) => {
      const cycleId = link.circularLinkID;
      if (!cycles.has(cycleId)) {
        cycles.set(cycleId, []);
      }
      cycles.get(cycleId).push(link);
    });

    cycles.forEach((links, cycleId) => {
      if (links.length > 1) {
        const maxWidthLink = links.reduce(
          (max, link) => (link.width > max.width ? link : max),
          links[0]
        );
        links.forEach((link) => {
          if (link !== maxWidthLink) {
            link.circular = false;
            link.circularLinkType = undefined;
            link.circularLinkID = undefined;
          }
        });
      }
    });

    // Recompute circular links after unmarking
    circularLinks = graph.links.filter((l) => l.circular);

    graph.nodes.forEach((node) => {
      // Sort sourceLinks and targetLinks by width (largest to smallest)
      node.sourceLinks.sort((a, b) => b.width - a.width);
      node.targetLinks.sort((a, b) => b.width - a.width);

      // Compute cumulative sourceY for each link, starting from y0 and stacking downward
      let currentSourceY = node.y0; // Start from the top of the trapezoid
      node.sourceLinks.forEach((link) => {
        link.circularPathData.sourceWidth = link.source.x1 - link.source.x0;
        link.circularPathData.sourceX =
          link.source.x0 + link.circularPathData.sourceWidth;
        link.circularPathData.sourceY = currentSourceY; // Set y0 to the current position
        link.y0 = link.circularPathData.sourceY;
        currentSourceY += link.width; // Stack downward by adding the width
        console.log(
          `SourceY for ${link.source.id} -> ${link.target.id}: ${link.circularPathData.sourceY}`
        );
      });

      // Compute cumulative targetY for each link, starting from y0
      let cumulativeTargetWidth = 0;
      node.targetLinks.forEach((link) => {
        link.circularPathData.targetX = link.target.x0;
        const targetTopY = link.target.y0;
        link.circularPathData.targetY = targetTopY + cumulativeTargetWidth;
        link.y1 = link.circularPathData.targetY;
        cumulativeTargetWidth += link.width;
        console.log(
          `TargetY for ${link.source.id} -> ${link.target.id}: ${link.circularPathData.targetY}`
        );
      });
    });

    // Process all links to apply circular path data only to circular links
    graph.links.forEach((link) => {
      console.log(
        `Processing link ${link.source.id} -> ${link.target.id}: circular=${link.circular}`
      );

      if (link.circular) {
        link.circularPathData.arcRadius = link.width + baseRadius;
        link.circularPathData.leftNodeBuffer = buffer;
        link.circularPathData.rightNodeBuffer = buffer;

        if (id(link.source) === id(link.target) && onlyCircularLink(link)) {
          link.circularPathData.leftSmallArcRadius =
            baseRadius + link.width / 2;
          link.circularPathData.leftLargeArcRadius =
            baseRadius + link.width / 2;
          link.circularPathData.rightSmallArcRadius =
            baseRadius + link.width / 2;
          link.circularPathData.rightLargeArcRadius =
            baseRadius + link.width / 2;

          if (link.circularLinkType === "bottom") {
            link.circularPathData.verticalFullExtent = link.source.y1; // No extra margin or buffer
            link.circularPathData.verticalLeftInnerExtent =
              link.circularPathData.verticalFullExtent -
              link.circularPathData.leftLargeArcRadius;
            link.circularPathData.verticalRightInnerExtent =
              link.circularPathData.verticalFullExtent -
              link.circularPathData.rightLargeArcRadius;
          } else {
            link.circularPathData.verticalFullExtent = link.source.y0; // No extra margin or buffer
            link.circularPathData.verticalLeftInnerExtent =
              link.circularPathData.verticalFullExtent +
              link.circularPathData.leftLargeArcRadius;
            link.circularPathData.verticalRightInnerExtent =
              link.circularPathData.verticalFullExtent +
              link.circularPathData.rightLargeArcRadius;
          }
        } else {
          let thisColumn = link.source.layer;
          let thisCircularLinkType = link.circularLinkType;
          let sameColumnLinks = graph.links.filter(
            (l) =>
              l.source.layer === thisColumn &&
              l.circularLinkType === thisCircularLinkType &&
              l.circular
          );

          if (link.circularLinkType === "bottom") {
            sameColumnLinks.sort((a, b) => b.y0 - a.y0);
          } else {
            sameColumnLinks.sort((a, b) => a.y0 - b.y0);
          }

          let radiusOffset = 0;
          sameColumnLinks.forEach((l, i) => {
            if (l.circularLinkID === link.circularLinkID) {
              link.circularPathData.leftSmallArcRadius =
                baseRadius + link.width / 2 + radiusOffset;
              link.circularPathData.leftLargeArcRadius =
                baseRadius +
                link.width / 2 +
                i * circularLinkGap +
                radiusOffset;
            }
            radiusOffset += l.width;
          });

          thisColumn = link.target.layer;
          sameColumnLinks = graph.links.filter(
            (l) =>
              l.target.layer === thisColumn &&
              l.circularLinkType === thisCircularLinkType &&
              l.circular
          );
          if (link.circularLinkType === "bottom") {
            sameColumnLinks.sort((a, b) => b.y1 - a.y1);
          } else {
            sameColumnLinks.sort((a, b) => a.y1 - b.y1);
          }

          radiusOffset = 0;
          sameColumnLinks.forEach((l, i) => {
            if (l.circularLinkID === link.circularLinkID) {
              link.circularPathData.rightSmallArcRadius =
                baseRadius + link.width / 2 + radiusOffset;
              link.circularPathData.rightLargeArcRadius =
                baseRadius +
                link.width / 2 +
                i * circularLinkGap +
                radiusOffset;
            }
            radiusOffset += l.width;
          });

          // Compute verticalFullExtent with global stacking, including padding
          if (link.circularLinkType === "bottom") {
            link.circularPathData.verticalFullExtent =
              maxY + globalBottomOffset;
            globalBottomOffset += link.width + padding;
          } else {
            link.circularPathData.verticalFullExtent = minY - globalTopOffset;
            globalTopOffset += link.width + padding;
          }
          link.circularPathData.verticalLeftInnerExtent =
            link.circularPathData.verticalFullExtent -
            link.circularPathData.leftLargeArcRadius;
          link.circularPathData.verticalRightInnerExtent =
            link.circularPathData.verticalFullExtent -
            link.circularPathData.rightLargeArcRadius;
        }

        // Stagger the rightOffset and leftOffset for each link using the path width plus padding
        link.circularPathData.rightOffset = globalRightXOffset;
        globalRightXOffset += link.width + padding; // Increase x-coordinate for the right side with padding
        link.circularPathData.leftOffset = globalLeftXOffset;
        globalLeftXOffset -= link.width + padding; // Decrease x-coordinate for the left side with padding

        link.circularPathData.leftInnerExtent = Math.max(
          link.circularPathData.sourceX + link.circularPathData.leftNodeBuffer,
          x0
        );
        link.circularPathData.rightInnerExtent = Math.min(
          link.circularPathData.targetX - link.circularPathData.rightNodeBuffer,
          x1
        );
        link.circularPathData.leftFullExtent = Math.max(
          link.circularPathData.sourceX +
            link.circularPathData.leftLargeArcRadius +
            link.circularPathData.leftNodeBuffer,
          x0
        );
        link.circularPathData.rightFullExtent = Math.min(
          link.circularPathData.targetX -
            link.circularPathData.rightLargeArcRadius -
            link.circularPathData.rightNodeBuffer,
          x1
        );

        link.path = createCircularPathStringArc(graph, link);
      } else {
        // Render as a regular link using sankeyLinkHorizontal
        const normalPath = d3Shape
          .linkHorizontal()
          .source((d) => [d.source.x1, d.y0])
          .target((d) => [d.target.x0, d.y1]);
        link.path = normalPath(link);
      }
    });
  }

  function calcVerticalBuffer(links, circularLinkGap, id) {
    links.sort(
      (a, b) =>
        b.target.layer - b.source.layer - (a.target.layer - a.source.layer)
    );
    links.forEach((link, i) => {
      let buffer = 10;
      if (id(link.source) === id(link.target) && onlyCircularLink(link)) {
        link.circularPathData.verticalBuffer = buffer + link.width / 2;
      } else {
        for (let j = 0; j < i; j++) {
          if (circularLinksCross(links[i], links[j])) {
            const bufferOverThisLink =
              links[j].circularPathData.verticalBuffer +
              links[j].width / 2 +
              circularLinkGap;
            buffer = bufferOverThisLink > buffer ? bufferOverThisLink : buffer;
          }
        }
        link.circularPathData.verticalBuffer = buffer + link.width / 2;
      }
    });
  }

  function circularLinksCross(link1, link2) {
    if (link1.source.layer < link2.target.layer) {
      return false;
    } else if (link1.target.layer > link2.source.layer) {
      return false;
    } else {
      return true;
    }
  }

  function onlyCircularLink(link) {
    let sourceCount = 0;
    link.source.sourceLinks.forEach((l) => {
      if (l.circular) sourceCount++;
    });

    let targetCount = 0;
    link.target.targetLinks.forEach((l) => {
      if (l.circular) targetCount++;
    });

    return sourceCount <= 1 && targetCount <= 1;
  }

  // create a d path using the addCircularPathData
  function createCircularPathString(link) {
    var pathString = "";
    // 'pathData' is assigned a value but never used
    // var pathData = {}

    if (link.circularLinkType == "top") {
      pathString =
        // start at the right of the source node
        "M" +
        link.circularPathData.sourceX +
        " " +
        link.circularPathData.sourceY +
        " " +
        // line right to buffer point
        "L" +
        link.circularPathData.leftInnerExtent +
        " " +
        link.circularPathData.sourceY +
        " " +
        // Arc around: Centre of arc X and  //Centre of arc Y
        "A" +
        link.circularPathData.leftLargeArcRadius +
        " " +
        link.circularPathData.leftSmallArcRadius +
        " 0 0 0 " +
        // End of arc X //End of arc Y
        link.circularPathData.leftFullExtent +
        " " +
        (link.circularPathData.sourceY -
          link.circularPathData.leftSmallArcRadius) +
        " " + // End of arc X
        // line up to buffer point
        "L" +
        link.circularPathData.leftFullExtent +
        " " +
        (link.circularPathData.verticalLeftInnerExtent +
          link.circularPathData.leftSmallArcRadius) +
        " " +
        // Arc around: Centre of arc X and  //Centre of arc Y
        "A" +
        link.circularPathData.leftLargeArcRadius +
        " " +
        link.circularPathData.leftLargeArcRadius +
        " 0 0 0 " +
        // End of arc X //End of arc Y
        link.circularPathData.leftInnerExtent +
        " " +
        link.circularPathData.verticalFullExtent +
        " " + // End of arc X
        // line left to buffer point
        "L" +
        link.circularPathData.rightInnerExtent +
        " " +
        link.circularPathData.verticalFullExtent +
        " " +
        // Arc around: Centre of arc X and  //Centre of arc Y
        "A" +
        link.circularPathData.rightLargeArcRadius +
        " " +
        link.circularPathData.rightLargeArcRadius +
        " 0 0 0 " +
        // End of arc X //End of arc Y
        link.circularPathData.rightFullExtent +
        " " +
        link.circularPathData.verticalRightInnerExtent +
        " " + // End of arc X
        // line down
        "L" +
        link.circularPathData.rightFullExtent +
        " " +
        (link.circularPathData.targetY -
          link.circularPathData.rightSmallArcRadius) +
        " " +
        // Arc around: Centre of arc X and  //Centre of arc Y
        "A" +
        link.circularPathData.rightLargeArcRadius +
        " " +
        link.circularPathData.rightSmallArcRadius +
        " 0 0 0 " +
        // End of arc X //End of arc Y
        link.circularPathData.rightInnerExtent +
        " " +
        link.circularPathData.targetY +
        " " + // End of arc X
        // line to end
        "L" +
        link.circularPathData.targetX +
        " " +
        link.circularPathData.targetY;
    } else {
      // bottom path
      pathString =
        // start at the right of the source node
        "M" +
        link.circularPathData.sourceX +
        " " +
        link.circularPathData.sourceY +
        " " +
        // line right to buffer point
        "L" +
        link.circularPathData.leftInnerExtent +
        " " +
        link.circularPathData.sourceY +
        " " +
        // Arc around: Centre of arc X and  //Centre of arc Y
        "A" +
        link.circularPathData.leftLargeArcRadius +
        " " +
        link.circularPathData.leftSmallArcRadius +
        " 0 0 1 " +
        // End of arc X //End of arc Y
        link.circularPathData.leftFullExtent +
        " " +
        (link.circularPathData.sourceY +
          link.circularPathData.leftSmallArcRadius) +
        " " + // End of arc X
        // line down to buffer point
        "L" +
        link.circularPathData.leftFullExtent +
        " " +
        link.circularPathData.verticalLeftInnerExtent +
        " " +
        // Arc around: Centre of arc X and  //Centre of arc Y
        "A" +
        link.circularPathData.leftLargeArcRadius +
        " " +
        link.circularPathData.leftLargeArcRadius +
        " 0 0 1 " +
        // End of arc X //End of arc Y
        link.circularPathData.leftInnerExtent +
        " " +
        link.circularPathData.verticalFullExtent +
        " " + // End of arc X
        // line left to buffer point
        "L" +
        link.circularPathData.rightInnerExtent +
        " " +
        link.circularPathData.verticalFullExtent +
        " " +
        // Arc around: Centre of arc X and  //Centre of arc Y
        "A" +
        link.circularPathData.rightLargeArcRadius +
        " " +
        link.circularPathData.rightLargeArcRadius +
        " 0 0 1 " +
        // End of arc X //End of arc Y
        link.circularPathData.rightFullExtent +
        " " +
        link.circularPathData.verticalRightInnerExtent +
        " " + // End of arc X
        // line up
        "L" +
        link.circularPathData.rightFullExtent +
        " " +
        (link.circularPathData.targetY +
          link.circularPathData.rightSmallArcRadius) +
        " " +
        // Arc around: Centre of arc X and  //Centre of arc Y
        "A" +
        link.circularPathData.rightLargeArcRadius +
        " " +
        link.circularPathData.rightSmallArcRadius +
        " 0 0 1 " +
        // End of arc X //End of arc Y
        link.circularPathData.rightInnerExtent +
        " " +
        link.circularPathData.targetY +
        " " + // End of arc X
        // line to end
        "L" +
        link.circularPathData.targetX +
        " " +
        link.circularPathData.targetY;
    }

    return pathString;
  }

  function createCircularPathStringSquare(graph, link) {
    // Compute padding adjustments based on the link's position in the list of circular links
    const circularLinks = link.source.sourceLinks.filter(
      (l) => l.circular && l.circularLinkType === link.circularLinkType
    ); // All circular links from the source that match bottom/top
    const linkIndex = link.source.sourceLinks.indexOf(link); // Index of this link among circular links
    const basePadding = 10; // Base padding value

    const { sourceX, targetX } = link.circularPathData;
    const Fexit0 = link.y0; // Starting y-position
    const Nentry0 = link.y1; // Top of target trapezoid
    let Fx1 = link.source.x1; // Right edge of FIDELITY trapezoid
    let Nx0 = targetX; // Left edge of target trapezoid
    if (sourceX < targetX) {
      // this doesn't work have to switch trap sides.
      // reverse draw
      Fx1 = targetX;
      Nx0 = sourceX;
    }
    const W = link.width || link.amt || 10; // Ensure width is defined
    const graphTop = d3Array.min(graph.nodes, (n) => n.y0);
    const graphBottom = d3Array.max(graph.nodes, (n) => n.y1);

    // Compute offset for this link based on its position (links after it)
    let offsetBefore = basePadding;
    circularLinks.forEach((otherLink, idx) => {
      const otherW = otherLink.width || otherLink.amt || 0;
      if (idx < linkIndex) {
        // Links after this one (outer)
        offsetBefore += otherW + basePadding; // Width/height of outer links
      }
    });
    //offsetBefore -= basePadding; // reverse the last padding
    const outerWidth = offsetBefore + W;
    // Compute outermost corners (maxX1, minX0, minY0)
    const maxX1 = Fx1 + outerWidth; // FIDELITY x1 + padding + total width + padding
    const minX0 = Nx0 - outerWidth; // c01 x - total width - padding
    const minY0 = graphTop - outerWidth; // FIDELITY y0 - total height - padding
    const minY1 = graphTop - offsetBefore; //crossbarBottom
    const entryBottom = Nentry0 + W;

    // Compute positions
    const leftX = Nx0 - outerWidth;

    // Forward path points (based on your suggestion)
    const p1 = { x: Fx1, y: Fexit0 }; // Exit point top (Fx1, Fexit0)
    const p2 = { x: Fx1, y: Fexit0 + W }; // down across face
    let p3 = { x: maxX1, y: Fexit0 + W }; // bottom-right
    let p4 = { x: maxX1, y: minY0 }; // Top-right corner
    let p5 = { x: minX0, y: minY0 }; // top-left corner at target
    let p6 = { x: minX0, y: entryBottom }; // Bottom-left corner at target
    const p7 = { x: Nx0, y: entryBottom }; // touch target
    const p8 = { x: Nx0, y: Nentry0 }; // Touch target trap (top)
    let p9 = { x: Nx0 - offsetBefore, y: Nentry0 }; // Back from target
    let p10 = { x: Nx0 - offsetBefore, y: minY1 }; // Up to crossbar bottom
    let p11 = { x: maxX1 - W, y: minY1 }; // accross crossbar bottom
    let p12 = { x: maxX1 - W, y: Fexit0 }; // Down to trap level
    const p13 = { x: Fx1, y: Fexit0 }; // Touch source at start

    /*if (link.circularLinkType == "bottom") {
      p3.x -= -W;
      p5.y += W;
      p6.x += W;
      p9.y -= W;
      p10.y -= W;
      p11.y -= W;
      p12.x = maxX1;
    }*/

    // Create a string of points for the polygon
    const points = [
      `${p1.x},${p1.y}`,
      `${p2.x},${p2.y}`,
      `${p3.x},${p3.y}`,
      `${p4.x},${p4.y}`,
      `${p5.x},${p5.y}`,
      `${p6.x},${p6.y}`,
      `${p7.x},${p7.y}`,
      `${p8.x},${p8.y}`,
      `${p9.x},${p9.y}`,
      `${p10.x},${p10.y}`,
      `${p11.x},${p11.y}`,
      `${p12.x},${p12.y}`,
      `${p13.x},${p13.y}`,
    ].join(" ");

    return points;
  }
  function unidentifyCircles(graph, id, sort) {
    // Post-process links to unmark cheaper side of cycles
    const cycles = new Map();
    graph.links.forEach((link) => {
      if (link.circular) {
        const cycleId = link.circularLinkID;
        if (!cycles.has(cycleId)) {
          cycles.set(cycleId, []);
        }
        cycles.get(cycleId).push(link);
      }
    });

    cycles.forEach((links, cycleId) => {
      if (links.length > 1) {
        // Find the link with the largest width
        const maxWidthLink = links.reduce(
          (max, link) => (link.width > max.width ? link : max),
          links[0]
        );
        // Unmark all links except the one with the largest width
        links.forEach((link) => {
          if (link !== maxWidthLink) {
            link.circular = false; // Unmark as circular
            link.circularLinkID = undefined; // Clear circular metadata
            link.circularLinkType = undefined;
          }
        });
      }
    });
  }

  function sankey() {
    const inputData = arguments[0]; // Assuming data is the first argument
    console.log(
      "Input data.links:",
      inputData.links.map((l) => ({
        source: l.source,
        target: l.target,
      }))
    );
    const graph = {
      nodes: nodes.apply(null, arguments),
      links: inputData.links.slice(),
    }; // Explicitly copy links
    console.log(
      "Initial graph.links:",
      graph.links.map((l) => ({
        source: typeof l.source === "object" ? l.source.id : l.source,
        target: typeof l.target === "object" ? l.target.id : l.target,
      }))
    );
    computeNodeLinks(graph);
    console.log(
      "After computeNodeLinks:",
      graph.links.map((l) => ({
        source: l.source.id,
        target: l.target.id,
        sourceIndex: l.source.index,
        targetIndex: l.target.index,
      }))
    );
    computeNodeValues(graph);
    console.log(
      "After computeNodeValues:",
      graph.links.map((l) => ({
        source: l.source.id,
        target: l.target.id,
        sourceIndex: l.source.index,
        targetIndex: l.target.index,
      }))
    );
    identifyCircles(graph, id, sort);
    unidentifyCircles(graph, id, sort);
    computeNodeDepths(graph);
    computeNodeHeights(graph);
    const margin = computeNodeBreadths(graph);
    selectCircularLinkTypes(graph, id);
    computeLinkBreadths(graph);
    addCircularPathData(graph, circularLinkGap, y1, id);
    graph.margin = margin;
    return graph;
  }
  sankey.update = function (graph) {
    computeLinkBreadths(graph);
    selectCircularLinkTypes(graph, id);
    addCircularPathData(graph, circularLinkGap, y1, id);
    return graph;
  };

  sankey.nodeId = function (_) {
    return arguments.length
      ? ((id = typeof _ === "function" ? _ : constant(_)), sankey)
      : id;
  };

  sankey.nodeAlign = function (_) {
    return arguments.length
      ? ((align = typeof _ === "function" ? _ : constant(_)), sankey)
      : align;
  };

  sankey.nodeSort = function (_) {
    return arguments.length ? ((sort = _), sankey) : sort;
  };

  sankey.nodeWidth = function (_) {
    return arguments.length ? ((dx = +_), sankey) : dx;
  };

  sankey.nodePadding = function (_) {
    return arguments.length ? ((dy = py = +_), sankey) : dy;
  };

  sankey.nodes = function (_) {
    return arguments.length
      ? ((nodes = typeof _ === "function" ? _ : constant(_)), sankey)
      : nodes;
  };

  sankey.links = function (_) {
    return arguments.length
      ? ((links = typeof _ === "function" ? _ : constant(_)), sankey)
      : links;
  };

  sankey.linkSort = function (_) {
    return arguments.length ? ((linkSort = _), sankey) : linkSort;
  };

  sankey.size = function (_) {
    return arguments.length
      ? ((x0 = y0 = 0), (x1 = +_[0]), (y1 = +_[1]), sankey)
      : [x1 - x0, y1 - y0];
  };

  sankey.extent = function (_) {
    return arguments.length
      ? ((x0 = +_[0][0]),
        (x1 = +_[1][0]),
        (y0 = +_[0][1]),
        (y1 = +_[1][1]),
        sankey)
      : [
          [x0, y0],
          [x1, y1],
        ];
  };

  sankey.iterations = function (_) {
    return arguments.length ? ((iterations = +_), sankey) : iterations;
  };

  sankey.circularLinkGap = function (_) {
    return arguments.length
      ? ((circularLinkGap = +_), sankey)
      : circularLinkGap;
  };

  return sankey;
}

function sankeyLinkHorizontal() {
  return d3Shape
    .linkHorizontal()
    .source((d) => [d.source.x1, d.y0])
    .target((d) => [d.target.x0, d.y1]);
}

export {
  sankeyWithCircles,
  center as sankeyCenter,
  justify as sankeyJustify,
  left as sankeyLeft,
  right as sankeyRight,
  sankeyLinkHorizontal,
};
