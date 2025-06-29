import {
  sankeyWithCircles,
  sankeyLinkHorizontal,
} from "./d3-sankey-circular.js";

// Test data with cycles
const data = {
  nodes: [
    { id: "110303001" },
    { id: "131924236" },
    { id: "136171197" },
    { id: "150532082" },
    { id: "200849590" },
    { id: "205205488" },
    { id: "205806345" },
    { id: "232888152" },
    { id: "237174183" },
    { id: "237825575" },
    { id: "311640316" },
    { id: "341747398" },
    { id: "520595110" },
    { id: "527082731" },
    { id: "001" },
  ],
  links: [
    { source: "110303001", target: "237825575", value: 579.864737581818 },
    { source: "110303001", target: "311640316", value: 567.8774842382135 },
    { source: "110303001", target: "205806345", value: 449.4899653497332 },
    { source: "110303001", target: "200849590", value: 396.3930814410876 },
    { source: "110303001", target: "341747398", value: 386.5814859762349 },
    { source: "110303001", target: "150532082", value: 350.06986632459984 },
    { source: "205205488", target: "110303001", value: 535.7282933632539 },
    { source: "527082731", target: "110303001", value: 531.0677702275045 },
    { source: "311640316", target: "110303001", value: 494.42406504771657 },
    { source: "237825575", target: "110303001", value: 398.421705658159 },
    { source: "232888152", target: "110303001", value: 389.4521542988829 },
    { source: "150532082", target: "131924236", value: 149.99774811434168 },
    { source: "150532082", target: "136171197", value: 137.27463689127342 },
    { source: "150532082", target: "520595110", value: 129.90016002894427 },
    { source: "237174183", target: "150532082", value: 284.78008684230633 },
    { source: "001", target: "150532082", value: 262.02162671350777 },
    { source: "237825575", target: "150532082", value: 242.31150264760296 },
    { source: "150532082", target: "237825575", value: 242.31150264760296 },
  ],
};

const sankeyInstance = sankeyWithCircles()
  .nodeWidth(24)
  .nodePadding(20)
  .extent([
    [0, 0],
    [800, 600],
  ])
  .nodeId((d) => d.id)
  .nodeSort((a, b) => b.value - a.value); // Sort nodes by value (largest first)

console.log("Nodes before Sankey:", data.nodes);
console.log("Links before Sankey:", data.links);
console.log(
  "Links passed to sankey:",
  data.links.map((l) => ({
    source: l.source,
    target: l.target,
  }))
);
const graph = sankeyInstance(data);

// Adjust SVG height based on circular link margins
const baseHeight = 600;
const newHeight = baseHeight + (graph.margin.top + graph.margin.bottom);
const svg = d3
  .select("body")
  .append("svg")
  .attr("width", 800)
  .attr("height", newHeight);

// Log to verify structure
console.log(
  "Nodes with depths and layers:",
  graph.nodes.map((d) => ({ id: d.id, depth: d.depth, layer: d.layer }))
);
console.log(
  "Links with circular flag:",
  graph.links.map((l) => ({
    source: l.source.id,
    target: l.target.id,
    value: l.value,
    circular: l.circular,
    circularLinkType: l.circularLinkType,
  }))
);

// Draw links
svg
  .append("g")
  .selectAll("path")
  .data(graph.links)
  .enter()
  .append("path")
  .attr("d", (d) => d.path)
  .attr("fill", "none")
  .attr("stroke", (d) => (d.circular ? "red" : "black"))
  .attr("stroke-width", (d) => d.width);

// Draw nodes
svg
  .append("g")
  .selectAll("rect")
  .data(graph.nodes)
  .enter()
  .append("rect")
  .attr("x", (d) => d.x0)
  .attr("y", (d) => d.y0)
  .attr("height", (d) => d.y1 - d.y0)
  .attr("width", (d) => d.x1 - d.x0)
  .attr("fill", "gray");

// Add labels for nodes (optional, for debugging)
svg
  .append("g")
  .selectAll("text")
  .data(graph.nodes)
  .enter()
  .append("text")
  .attr("x", (d) => d.x0)
  .attr("y", (d) => d.y0 - 5)
  .attr("dy", "0.35em")
  .text((d) => d.id);
