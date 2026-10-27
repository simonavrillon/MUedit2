/**
 * Reads of `state` that need defaults or derivation; use these where one exists.
 */
/** @typedef {import("./state.js").State} State */
/** @typedef {import("../app/context.js").Span} Span */

/**
 * @param {State} state
 */
export function getCurrentGrid(state) {
  return state.currentGrid || 0;
}

/**
 * @param {Partial<Span> | null | undefined} roi
 * @param {number} [fallback]
 * @returns {number}
 */
export function roiStart(roi, fallback = 0) {
  const v = roi?.start;
  return typeof v === "number" && Number.isFinite(v) ? v : fallback;
}

/**
 * @template {number | null} [F=number]
 * @param {Partial<Span> | null | undefined} roi
 * @param {F} [fallback]
 * @returns {number | F}
 */
export function roiEnd(roi, fallback = /** @type {F} */ (0)) {
  const v = roi?.end;
  return typeof v === "number" && Number.isFinite(v) ? v : fallback;
}

// Return the MU indices belonging to a given grid. When no grid mapping is
// available, every MU is treated as belonging to the requested grid.
/**
 * @param {ArrayLike<number>[] | null | undefined} spikeTimes one entry per MU
 * @param {number[] | null | undefined} mapping
 * @param {number} grid
 * @returns {number[]}
 */
function muIndicesForGrid(spikeTimes, mapping, grid) {
  const mus = spikeTimes || [];
  if (!mus.length) return [];
  const map = mapping || [];
  if (!map.length) {
    return mus.map((_, idx) => idx);
  }
  return map
    .map((g, idx) => ({ g, idx }))
    .filter((item) => Number(item.g) === Number(grid))
    .map((item) => item.idx);
}

/**
 * @param {State} state
 * @param {number} grid
 */
export function getEditMuIndicesForGrid(state, grid) {
  return muIndicesForGrid(state.edit.spikeTimes, state.edit.muGridIndex, grid);
}
