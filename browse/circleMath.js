function findCircuits(adj) {
  const n = adj.length;
  const cyclesSet = new Set();

  function circuit(v, adj, path, onPath) {
    path.push(v);
    onPath[v] = true;

    const neighbors = adj[v] || [];
    for (const w of neighbors) {
      if (path.includes(w)) {
        if (w === path[0] && path.length >= 2) {
          // Allow cycles of length 3 or more
          const cycle = [...path, w];
          const minIdx = cycle.indexOf(Math.min(...cycle));
          const normalized = [
            ...cycle.slice(minIdx),
            ...cycle.slice(0, minIdx),
          ];
          cyclesSet.add(normalized.join(","));
        }
      } else {
        circuit(w, adj, path, onPath);
      }
    }

    path.pop();
    onPath[v] = false;
  }

  // Collect all cycles
  for (let s = 0; s < n; s++) {
    const onPath = new Array(n).fill(false);
    const path = [];
    circuit(s, adj, path, onPath);
  }

  // Convert cycles to arrays and validate edges
  let cycles = Array.from(cyclesSet).map((cycle) =>
    cycle.split(",").map(Number)
  );
  cycles = cycles.filter((cycle) => {
    for (let i = 0; i < cycle.length - 1; i++) {
      const from = cycle[i];
      const to = cycle[i + 1];
      if (!(adj[from] || []).includes(to)) {
        return false;
      }
    }
    return true;
  });

  // Filter sub-cycles: reject a cycle if it is a strict sub-path of a larger cycle
  const elementaryCycles = [];
  cycles.sort((a, b) => a.length - b.length); // Sort by length (ascending)
  for (let i = 0; i < cycles.length; i++) {
    const cycle = cycles[i];
    let isElementary = true;
    const cycleVertices = new Set(cycle.slice(0, -1)); // Exclude the repeated end vertex
    for (let j = 0; j < cycles.length; j++) {
      if (i === j) continue;
      const other = cycles[j];
      if (other.length <= cycle.length) continue;

      const otherVertices = new Set(other.slice(0, -1));
      let isSubset = true;
      for (const vertex of cycleVertices) {
        if (!otherVertices.has(vertex)) {
          isSubset = false;
          break;
        }
      }
      // Only reject if the smaller cycle is a strict sub-path (check edges)
      if (isSubset) {
        let isSubPath = false;
        const cycleLen = cycle.length - 1;
        for (let k = 0; k < other.length; k++) {
          let matches = true;
          for (let m = 0; m < cycleLen; m++) {
            const otherIdx = (k + m) % (other.length - 1);
            if (cycle[m] !== other[otherIdx]) {
              matches = false;
              break;
            }
          }
          if (matches) {
            isSubPath = true;
            break;
          }
        }
        if (isSubPath) {
          isElementary = false;
          break;
        }
      }
    }
    if (isElementary) {
      elementaryCycles.push(cycle);
    }
  }

  return elementaryCycles;
}

export { findCircuits };
