import { findCircuits } from "./circleMath.js";

// Use global d3 from CDN
const d3Array = window.d3;
const d3Shape = window.d3;
let OTHER_WIDTH = 30;

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
  let circularLinkGap = 10;

  // Constants for circular link calculations
  const verticalMargin = 25;
  const baseRadius = 10;

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

  function identifyCircles(graph) {
    graph.links.forEach((link) => {
      if (link.value === undefined) link.value = 1;
    });
    graph.links.sort((a, b) => b.value - a.value);
    const idToIndex = new Map();
    graph.nodes.forEach((node, index) => {
      idToIndex.set(node.id, index);
      node.index = index;
    });
    const n = graph.nodes.length;
    const adj_current = Array.from({ length: n }, () => []);
    const cycles = new Map();
    let cycleId = 0;

    graph.links.forEach((link) => {
      const u = idToIndex.get(link.source.id);
      const v = idToIndex.get(link.target.id);
      let has_path = false;
      let parent = new Array(n).fill(-1);

      if (u === v) {
        has_path = true;
      } else {
        const visited = new Array(n).fill(false);
        const queue = [v];
        visited[v] = true;
        let head = 0;

        while (head < queue.length && !has_path) {
          const curr = queue[head++];
          for (let neigh of adj_current[curr]) {
            if (neigh === u) {
              has_path = true;
              parent[neigh] = curr; // set parent for the found edge
              break;
            }
            if (!visited[neigh]) {
              visited[neigh] = true;
              parent[neigh] = curr;
              queue.push(neigh);
            }
          }
        }
      }

      if (has_path) {
        link.circular = true;
        let path = [];
        let current = u;
        while (current !== v && current !== -1) {
          path.push(current);
          current = parent[current];
        }
        if (current === v) {
          path.push(v);
        }
        path.reverse(); // now v to u
        const cycleNodes = [...new Set(path)];
        cycleNodes.sort((a, b) => a - b);
        const cycleKey = cycleNodes.join(",");
        if (!cycles.has(cycleKey)) {
          cycles.set(cycleKey, cycleId++);
        }
        link.circularLinkId = cycles.get(cycleKey);
      } else {
        link.circular = false;
        adj_current[u].push(v);
      }
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
  function addCircularPathData(graph, circularLinkGap, id) {
    //var baseRadius = 10
    var buffer = 25; // target/source buffer, need room for hats.
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

    calcVerticalBuffer(graph.links, circularLinkGap, id);
    calcHorizontalBuffer(graph.links, circularLinkGap, id);

    // add the base data for each link
    graph.links.forEach(function (link) {
      const width = link.width;
      if (link.circular) {
        link.circularPathData.exitTop = link.y0;
        link.circularPathData.entryTop = link.y1; //all same spot for now
        link.circularPathData.sourceBarLeft =
          link.source.x1 +
          buffer +
          link.circularPathData.horizontalSourceBuffer;
        link.circularPathData.sourceBarRight =
          link.circularPathData.sourceBarLeft + width;
        link.circularPathData.targetBarLeft =
          link.target.x0 - link.circularPathData.horizontalTargetBuffer - width;
        link.circularPathData.targetBarRight =
          link.circularPathData.targetBarLeft + width;
        link.circularPathData.top =
          minY - buffer - width - link.circularPathData.verticalBuffer;
        link.circularPathData.bottom =
          maxY + buffer + width + link.circularPathData.verticalBuffer;

        // else calculate normally
        // add target extent coordinates, based on links with same source column and circularLink type
        var thisColumn = link.source.column;
        var thisCircularLinkType = link.circularLinkType;
        var sameColumnLinks = graph.links.filter(function (l) {
          return (
            l.source.column == thisColumn &&
            l.circularLinkType == thisCircularLinkType
          );
        });

        if (link.circular) {
          link.path = createCircularPathStringSquare(link);
        }
      }
    });
  }

  function calcVerticalBuffer(links, circularLinkGap, id) {
    links.sort((a, b) => b.width - a.width); // widest closest
    let botBuffer = circularLinkGap;
    let topBuffer = circularLinkGap;
    links.forEach((link, i) => {
      if (link.circular) {
        let buffer = 0;
        link.circularPathData = {};
        if (link.circularLinkType == "top") {
          const buffer = topBuffer;
          link.circularPathData.verticalBuffer = buffer;
          topBuffer += link.width + circularLinkGap;
        } else {
          const buffer = botBuffer;
          link.circularPathData.verticalBuffer = buffer;
          botBuffer += link.width + circularLinkGap;
        }
      }
    });
  }

  function calcHorizontalBuffer(links, circularLinkGap, id) {
    links.sort((a, b) => b.width - a.width); // widest closest
    let buffersS = {};
    let buffersT = {};
    links.forEach((link, i) => {
      if (link.circular) {
        if (!buffersS[link.source.layer])
          buffersS[link.source.layer] = circularLinkGap;
        if (!buffersT[link.target.layer])
          buffersT[link.target.layer] = circularLinkGap;
        link.circularPathData.horizontalSourceBuffer =
          buffersS[link.source.layer] + link.width + circularLinkGap;
        link.circularPathData.horizontalTargetBuffer =
          buffersT[link.target.layer] + link.width + circularLinkGap;
        buffersS[link.source.layer] += link.width + circularLinkGap;
        buffersT[link.target.layer] += link.width + circularLinkGap;
      }
    });
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

  function unidentifyCircles(graph, id, sort) {
    // Post-process links to unmark cheaper side of cycles
    const cycles = new Map();
    graph.links.forEach((link) => {
      if (link.circular) {
        const cycleId = link.circularLinkId;
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
            link.circularLinkId = undefined; // Clear circular metadata
            link.circularLinkType = undefined;
          }
        });
      }
    });
  }
  sankeyWithCircles.createCircularPathStringSquare =
    createCircularPathStringSquare;

  function sankey() {
    const inputData = arguments[0]; // Assuming data is the first argument

    const graph = {
      nodes: nodes.apply(null, arguments),
      links: inputData.links.slice(),
    }; // Explicitly copy links

    computeNodeLinks(graph);
    computeNodeValues(graph);
    identifyCircles(graph, id, sort);
    //unidentifyCircles(graph, id, sort);
    computeNodeDepths(graph);
    computeNodeHeights(graph);
    const margin = computeNodeBreadths(graph);
    selectCircularLinkTypes(graph, id);
    computeLinkBreadths(graph);
    addCircularPathData(graph, circularLinkGap, id);
    graph.margin = margin;
    return graph;
  }
  sankey.update = function (graph) {
    computeLinkBreadths(graph);
    selectCircularLinkTypes(graph, id);
    addCircularPathData(graph, circularLinkGap, id);
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

// create a d path using the addCircularPathData
function createCircularPathStringSquare(link) {
  let p1 = {};
  let p2 = {};
  let p3 = {};
  let p4 = {};
  let p5 = {};
  let p6 = {};
  let p7 = {};
  let p8 = {};
  let p9 = {};
  let p10 = {};
  let p11 = {};
  let p12 = {};
  const width = link.width;
  if (link.circularLinkType == "top") {
    // top

    if (link.source.layer < link.target.layer) {
      // L->R
      p1 = { x: link.source.x1, y: link.circularPathData.exitTop + link.width };
      p2 = {
        x: link.circularPathData.sourceBarRight,
        y: link.circularPathData.exitTop + link.width,
      }; // shoot right
      p3 = {
        x: link.circularPathData.sourceBarRight,
        y: link.circularPathData.top + width,
      }; // up to bottom of cross bar
      p4 = {
        x: link.circularPathData.targetBarLeft,
        y: link.circularPathData.top + width,
      };
      p5 = {
        x: link.circularPathData.targetBarLeft,
        y: link.circularPathData.entryTop + width,
      };
      p6 = { x: link.target.x0, y: link.circularPathData.entryTop + width };
      p7 = { x: link.target.x0, y: link.circularPathData.entryTop };
      p8 = {
        x: link.circularPathData.targetBarRight,
        y: link.circularPathData.entryTop,
      };
      p9 = {
        x: link.circularPathData.targetBarRight,
        y: link.circularPathData.top,
      };
      p10 = {
        x: link.circularPathData.sourceBarLeft,
        y: link.circularPathData.top,
      };
      p11 = {
        x: link.circularPathData.sourceBarLeft,
        y: link.circularPathData.exitTop,
      };
      p11 = { x: link.source.x1, y: link.circularPathData.exitTop };
      p12 = {
        x: link.source.x1,
        y: link.circularPathData.exitTop + link.width,
      };
    } else {
      //R -> L
      p1 = { x: link.source.x1, y: link.circularPathData.exitTop };
      p2 = {
        x: link.circularPathData.sourceBarLeft,
        y: link.circularPathData.exitTop,
      }; // shoot right
      p3 = {
        x: link.circularPathData.sourceBarLeft,
        y: link.circularPathData.top + width,
      }; // up to bottom of cross bar
      p4 = {
        x: link.circularPathData.targetBarRight,
        y: link.circularPathData.top + width,
      };
      p5 = {
        x: link.circularPathData.targetBarRight,
        y: link.circularPathData.entryTop,
      };
      p6 = { x: link.target.x0, y: link.circularPathData.entryTop };
      p7 = { x: link.target.x0, y: link.circularPathData.entryTop + width };
      p8 = {
        x: link.circularPathData.targetBarLeft,
        y: link.circularPathData.entryTop + width,
      };
      p9 = {
        x: link.circularPathData.targetBarLeft,
        y: link.circularPathData.top,
      };
      p10 = {
        x: link.circularPathData.sourceBarRight,
        y: link.circularPathData.top,
      };
      p11 = {
        x: link.circularPathData.sourceBarRight,
        y: link.circularPathData.exitTop + width,
      };
      p12 = { x: link.source.x1, y: link.circularPathData.exitTop + width };
    }
  }
  if (link.circularLinkType == "bottom") {
    // bottom R->L

    if (link.source.layer >= link.target.layer) {
      p1 = { x: link.source.x1, y: link.circularPathData.exitTop + width };
      p2 = {
        x: link.circularPathData.sourceBarLeft,
        y: link.circularPathData.exitTop + width,
      }; // shoot right
      p3 = {
        x: link.circularPathData.sourceBarLeft,
        y: link.circularPathData.bottom - width,
      }; // up to bottom of cross bar
      p4 = {
        x: link.circularPathData.targetBarRight,
        y: link.circularPathData.bottom - width,
      };
      p5 = {
        x: link.circularPathData.targetBarRight,
        y: link.circularPathData.entryTop + width,
      };
      p6 = { x: link.target.x0, y: link.circularPathData.entryTop + width };
      p7 = { x: link.target.x0, y: link.circularPathData.entryTop };
      p8 = {
        x: link.circularPathData.targetBarLeft,
        y: link.circularPathData.entryTop,
      };
      p9 = {
        x: link.circularPathData.targetBarLeft,
        y: link.circularPathData.bottom,
      };
      p10 = {
        x: link.circularPathData.sourceBarRight,
        y: link.circularPathData.bottom,
      };
      p11 = {
        x: link.circularPathData.sourceBarRight,
        y: link.circularPathData.exitTop,
      };
      p12 = { x: link.source.x1, y: link.circularPathData.exitTop + width };
    } else {
      p1 = { x: link.source.x1, y: link.circularPathData.exitTop + width };
      p2 = {
        x: link.circularPathData.sourceBarLeft,
        y: link.circularPathData.exitTop + width,
      }; // shoot right
      p3 = {
        x: link.circularPathData.sourceBarLeft,
        y: link.circularPathData.bottom,
      }; // up to bottom of cross bar
      p4 = {
        x: link.circularPathData.targetBarRight,
        y: link.circularPathData.bottom,
      };
      p5 = {
        x: link.circularPathData.targetBarRight,
        y: link.circularPathData.entryTop + width,
      };
      p6 = { x: link.target.x0, y: link.circularPathData.entryTop + width };
      p7 = { x: link.target.x0, y: link.circularPathData.entryTop };
      p8 = {
        x: link.circularPathData.targetBarLeft,
        y: link.circularPathData.entryTop,
      };
      p9 = {
        x: link.circularPathData.targetBarLeft,
        y: link.circularPathData.bottom - width,
      };
      p10 = {
        x: link.circularPathData.sourceBarRight,
        y: link.circularPathData.bottom - width,
      };
      p11 = {
        x: link.circularPathData.sourceBarRight,
        y: link.circularPathData.exitTop,
      };
      p12 = { x: link.source.x1, y: link.circularPathData.exitTop };
    }
  }

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
    `${p12.x},${p12.y}`, // always return to first point
    `${p1.x},${p1.y}`,
  ].join(" ");

  return points;
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
  const radius = (d.x1 - d.x0) / 5;
  const armWidth = radius * 0.4;
  let cx, circlePath;
  const cy = (d.y0 + d.y1) / 2;

  if (d.isTerminal) {
    const inflowHeight = d.inflowHeight || d.x1 - d.x0;
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

  /*
  no plus for now
  const plusPath = `
    M${cx - armWidth},${cy} H${cx + armWidth}
    M${cx},${cy - armWidth} V${cy + armWidth} 
  `;*/
  return `${circlePath}`;
}
function adjustCircularLink(link) {
  // fix path if we moved it.
  link.circularPathData.sourceY = link.y0;
  link.path = sankeyWithCircles.createCircularPathStringSquare(link);
}
export {
  sankeyWithCircles,
  center as sankeyCenter,
  justify as sankeyJustify,
  left as sankeyLeft,
  right as sankeyRight,
  sankeyLinkHorizontal,
  adjustCircularLink,
  generateOctagonPath,
  generateTrapezoidPath,
  generatePlusPath,
};
