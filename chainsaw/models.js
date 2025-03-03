
class BudgetNode {
  constructor(data, parent = null) {
    this.name = data.name || `missing name ${data.id}`;
    if (!data.name) console.log('Node created with fallback name:', this.name);
    this.parent = parent;
    this.path = parent ? [...parent.path, this.name] : [this.name];
    this.collected = false;
    this._value = undefined;
    this.baseValue = data.value || 0;
    this.descendants = {};
    this._children = undefined;
    this.id = this.path.join('/');
    this.color = null;
    this.depth = data.depth;
    if (data.children) {
      for (const [key, childData] of Object.entries(data.children)) {
        this.descendants[key] = new BudgetNode(childData, this);
      }
    }
  }

  static agencyDepth = 4;

  /** Collect all agency nodes at agencyDepth and sort them by value in descending order */
  static collectAndSortAgencies() {
    const agencyNodes = [];
    function collectAgencies(node, depth) {
      if (depth === BudgetNode.agencyDepth) {
        agencyNodes.push(node);
      } else if (depth < BudgetNode.agencyDepth) {
        for (const child of Object.values(node.descendants)) {
          collectAgencies(child, depth + 1);
        }
      }
    }
    collectAgencies(BudgetNode.root, 0);
    agencyNodes.sort((a, b) => b.value - a.value); // Sort descending by value
    return agencyNodes;
  }

  /** Assign colors to agency nodes based on their sorted index */
  static assignAgencyColors() {
    const agencyNodes = BudgetNode.collectAndSortAgencies();
    const numAgencies = agencyNodes.length;
    const hueStep = 360 / numAgencies;

    agencyNodes.forEach((node, index) => {
      const hue = (index * hueStep) % 360;
      node.color = d3.hsl(hue, 0.7, 0.5).toString(); // Base saturation and lightness
    });
  }

static setChildColors(node, depth, agencyColor) {
  // Only proceed if there’s an agency color to work with
  if (!agencyColor) return;

  // Set the current node's color based on the agency color
  if (depth > BudgetNode.agencyDepth) {
    const hsl = d3.hsl(agencyColor);
    const lightenFactor = 0.05 * (depth - BudgetNode.agencyDepth);
    hsl.l = Math.min(1, hsl.l + lightenFactor);
    node.color = hsl.toString();
  }

  // Recurse on all children, passing the agencyColor
  for (const child of node.children) {
    BudgetNode.setChildColors(child, depth + 1, agencyColor);
  }
}


static assignColors(node, depth) {
  if (depth < BudgetNode.agencyDepth) {
    // Above agency depth: recurse on children and average their colors
    const childColors = [];
    for (const child of node.children) {
      const colors = BudgetNode.assignColors(child, depth + 1);
      childColors.push(...colors);
    }
    if (childColors.length > 0) {
      node.color = BudgetNode.averageColors(childColors);
    } else {
      node.color = '#ccc'; // Fallback if no children
    }
    return childColors; // Return colors for higher-level averaging
  } else if (depth === BudgetNode.agencyDepth) {
    // At agency depth: set the node's color and propagate to children
    node.color = node.color || '#ccc'; // Use pre-assigned color or fallback
    for (const child of node.children) {
      BudgetNode.setChildColors(child, depth + 1, node.color);
    }
    return [node.color]; // Return this node's color for averaging
  } else {
    // Below agency depth: rely on setChildColors called from above
    // If agencyColor wasn’t passed (e.g., root call), set a fallback
    if (!node.color) {
      node.color = '#ccc';
    }
    return []; // No colors to return for averaging
  }
}
  /** Average colors in HSL space with reduced saturation */
  static averageColors(colors) {
    if (colors.length === 0) return '#ccc';

    let sumSin = 0, sumCos = 0, sumSat = 0, sumLight = 0;
    colors.forEach(color => {
      const hsl = d3.hsl(color);
      sumSin += Math.sin(hsl.h * Math.PI / 180);
      sumCos += Math.cos(hsl.h * Math.PI / 180);
      sumSat += hsl.s;
      sumLight += hsl.l;
    });

    const count = colors.length;
    const avgHue = (Math.atan2(sumSin / count, sumCos / count) * 180 / Math.PI + 360) % 360;
    const avgSat = Math.min(0.8, sumSat / count * 0.8); // Cap saturation and reduce by 20%
    const avgLight = sumLight / count;

    return d3.hsl(avgHue, avgSat, avgLight).toString();
  }

  /** Get children sorted by value in descending order */
  get children() {
    if (this._children === undefined) {
      this._children = Object.values(this.descendants).sort((a, b) => b.value - a.value);
    }
    return this._children;
  }

  /** Total value: baseValue plus sum of children's values */
  get value() {
    if (this.collected) return 0;
    if (this._value !== undefined) return this._value;
    const childrenSum = this.children.reduce((sum, child) => sum + child.value, 0);
    this._value = childrenSum || this.baseValue; //this.baseValue + childrenSum;
    return this._value;
  }

  /** Set collected status and invalidate caches */
  setCollected(collected) {
    this.collected = collected;
    this._value = undefined;
    this._children = undefined;
    let current = this.parent;
    while (current) {
      current._value = undefined;
      current._children = undefined;
      current = current.parent;
    }
  }

  /** Add delta to baseValue and invalidate caches */
  addToBaseValue(delta) {
    this.baseValue += delta;
    this._value = undefined;
    this._children = undefined;
    let current = this.parent;
    while (current) {
      current._value = undefined;
      current._children = undefined;
      current = current.parent;
    }
  }

 addPath(path, id, value) {
  let current = this;
  let depth = 0;
  path.forEach((level, index) => {
    if (!current.descendants[level]) {
      current.descendants[level] = new BudgetNode({ name: level, id: id, depth: depth }, current);
    }
    current = current.descendants[level];
    depth++;
    if (index === path.length - 1) { // Only the leaf node gets the value
      current.addToBaseValue(value);
    }
  });
}

  static root = new BudgetNode({ name: "Budget" });

  static addPathToRoot(path, id, value) {
    BudgetNode.root.addPath(path, id, value);
  }

  static getRoot() {
    return BudgetNode.root;
  }

  static setupModel() {
    BudgetNode.assignAgencyColors();
    BudgetNode.assignColors(BudgetNode.root, 0);
  }
}

export { BudgetNode };