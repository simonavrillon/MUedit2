/** @typedef {import("../app/state.js").State} State */
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

/**
 * @param {State} state
 * @param {number} muIdx
 * @returns {string}
 */
export function muUidFor(state, muIdx) {
  return state.edit.muUids?.[muIdx] ?? `mu${muIdx}`;
}

// Return the MU indices belonging to a given grid. When no grid mapping is
// available, every MU is treated as belonging to the requested grid.
/**
 * @param {ArrayLike<number>[] | null | undefined} distimes one entry per MU
 * @param {number[] | null | undefined} mapping
 * @param {number} gridIdx
 * @returns {number[]}
 */
function muIndicesForGrid(distimes, mapping, gridIdx) {
  const mus = distimes || [];
  if (!mus.length) return [];
  const map = mapping || [];
  if (!map.length) {
    return mus.map((_, idx) => idx);
  }
  return map
    .map((g, idx) => ({ g, idx }))
    .filter((item) => Number(item.g) === Number(gridIdx))
    .map((item) => item.idx);
}

/**
 * @param {State} state
 * @param {number} gridIdx
 */
export function getRunMuIndicesForGrid(state, gridIdx) {
  return muIndicesForGrid(state.muDistimes, state.muGridIndex, gridIdx);
}

/**
 * @param {State} state
 * @param {number} gridIdx
 */
export function getEditMuIndicesForGrid(state, gridIdx) {
  return muIndicesForGrid(state.edit.distimes, state.edit.muGridIndex, gridIdx);
}
