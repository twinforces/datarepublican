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
          // Allow cycles of length 2 or more
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

  // Convert cycles to arrays
  let cycles = Array.from(cyclesSet).map((cycle) =>
    cycle.split(",").map(Number)
  );

  // Ensure all cycle edges are valid (redundant but safe)
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

  return cycles;
}

export { findCircuits };
