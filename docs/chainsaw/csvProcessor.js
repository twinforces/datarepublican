// Assuming you have the CSV as a string from the exporter
function processBudgetCSV(csvString) {
    const rows = csvString.split('\n').map(row => row.split(','));
    const headers = rows[0];
    const data = rows.slice(1);

    // Build hierarchy
    const hierarchy = { name: 'Budget', children: [] };
    const functionMap = new Map();

    data.forEach(row => {
        const tid = row[0];
        const title = row[1];
        const discOrMand = row[2];
        const agency = row[4];
        const func = row[6];
        const subfunc = row[7];
        const ba2025 = parseFloat(row[9]) || 0; // Budget Authority 2025

        // Skip if no meaningful value
        if (!tid || isNaN(ba2025)) return;

        // Function level
        if (!functionMap.has(func)) {
            functionMap.set(func, { name: func, children: new Map() });
            hierarchy.children.push(functionMap.get(func));
        }
        const funcNode = functionMap.get(func);

        // Subfunction level
        if (!funcNode.children.has(subfunc)) {
            funcNode.children.set(subfunc, { name: subfunc, children: new Map() });
        }
        const subfuncNode = funcNode.children.get(subfunc);

        // Agency level
        if (!subfuncNode.children.has(agency)) {
            subfuncNode.children.set(agency, { name: agency, children: [] });
        }
        const agencyNode = subfuncNode.children.get(agency);

        // TID/Title level
        agencyNode.children.push({ name: title, size: Math.abs(ba2025) }); // Use absolute value for visualization
    });

    // Convert Maps to arrays
    hierarchy.children.forEach(func => {
        func.children = Array.from(func.children.values());
        func.children.forEach(subfunc => {
            subfunc.children = Array.from(subfunc.children.values());
        });
    });

    return hierarchy;
}