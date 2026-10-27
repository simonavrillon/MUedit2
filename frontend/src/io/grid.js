/**
 * Grid counts, names and column counts, as the catalogue and the backend name them.
 */
/**
 * @param {number} value
 * @returns {number | null}
 */
function safeNonNegativeInt(value) {
  const n = Number(value);
  return Number.isFinite(n) && n >= 0 ? Math.floor(n) : null;
}

/**
 * @param {{ gridNames?: string[], muGridIndex?: number[], muscleNames?: string[] }} [options]
 * @returns {number}
 */
export function inferGridCount({
  gridNames = [],
  muGridIndex = [],
  muscleNames = [],
} = {}) {
  let count = 1;
  if (Array.isArray(gridNames)) count = Math.max(count, gridNames.length);
  if (Array.isArray(muscleNames)) count = Math.max(count, muscleNames.length);
  if (Array.isArray(muGridIndex) && muGridIndex.length) {
    const maxIdx = Math.max(
      ...muGridIndex.map(safeNonNegativeInt).filter((v) => v !== null),
      0,
    );
    count = Math.max(count, maxIdx + 1);
  }
  return count;
}

/**
 * @param {(string | null | undefined)[] | null | undefined} gridNames
 * @param {{ minimumCount?: number }} [options]
 * @returns {string[]}
 */
export function normalizeGridNames(gridNames, { minimumCount = 1 } = {}) {
  const source = Array.isArray(gridNames) ? gridNames : [];
  const count = Math.max(minimumCount, source.length);
  return Array.from({ length: count }, (_, i) => {
    const raw = String(source[i] || "").trim();
    return raw || `Grid${i + 1}`; // the backend's fallback_grid_name
  });
}

/**
 * Columns are one past the largest zero-based column of a grid's `[row, col]` coordinates.
 *
 * @param {number[][] | null | undefined} coords
 * @returns {number}
 */
export function gridColumnCount(coords) {
  let maxCol = 0;
  (coords || []).forEach((c) => {
    if (Array.isArray(c) && c.length >= 2) maxCol = Math.max(maxCol, c[1]);
  });
  return maxCol + 1;
}
