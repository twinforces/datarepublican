/**
 * Pure graph identity — no DOM, IDB, or D3.
 * Charity kinds and keys for /browse (EIN, GIN, leftover stub).
 */

export function formatNumber(num) {
  if (num >= 1e12) return (num / 1e12).toFixed(1) + "T";
  if (num >= 1e9) return (num / 1e9).toFixed(1) + "B";
  if (num >= 1e6) return (num / 1e6).toFixed(1) + "M";
  if (num >= 1e3) return (num / 1e3).toFixed(1) + "K";
  return num == null ? "N/A" : num.toString();
}

/** 9-digit EIN, GIN (70+sha), or leftover stub etcXXXXXXXXX */
export function isGraphKey(id) {
  if (!id) return false;
  if (/^[0-9]{3,9}$/.test(id)) return true;
  if (/^70[0-9a-fA-F]{64}$/.test(id)) return true;
  if (/^70[0-9a-fA-F]{128}$/.test(id)) return true;
  if (/^etc[0-9]{9}$/.test(id)) return true;
  return false;
}

export function kindFrom(row, ein) {
  const org = (row && row.org_type) || "";
  const xml = (row && row.xml_name) || "";
  if (org === "ghost" || xml === "ghost") return "ghost";
  if (org === "leftover" || xml === "leftover") return "leftover";
  if (org === "backfill" || xml === "backfill") return "bmf";
  if (ein && /^etc[0-9]{9}$/.test(ein)) return "leftover";
  if (ein && (ein.length === 66 || ein.length === 130) && ein.startsWith("70"))
    return "ghost";
  return "charity";
}

export function compareCharities(a, b) {
  return (
    b.grantsInTotal + b.grantsTotal - (a.grantsInTotal + a.grantsTotal) ||
    a.name.localeCompare(b.name)
  );
}

export function compareLinks(a, b) {
  return b.value - a.value;
}
