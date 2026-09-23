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
 * @param {number[][] | null | undefined} pulseTrains
 * @param {number[] | null | undefined} mapping
 * @param {number} gridIdx
 * @returns {number[]}
 */
function muIndicesForGrid(pulseTrains, mapping, gridIdx) {
  const pulses = pulseTrains || [];
  if (!pulses.length) return [];
  const map = mapping || [];
  if (!map.length) {
    return pulses.map((_, idx) => idx);
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
  return muIndicesForGrid(state.muPulseTrains, state.muGridIndex, gridIdx);
}

/**
 * @param {State} state
 * @param {number} gridIdx
 */
export function getEditMuIndicesForGrid(state, gridIdx) {
  return muIndicesForGrid(
    state.edit.pulseTrains,
    state.edit.muGridIndex,
    gridIdx,
  );
}

/**
 * Server-side revision in force for a motor unit (0 = as read from the file).
 * @param {State} state
 * @param {number} muIdx
 * @returns {number}
 */
export function rowRevOf(state, muIdx) {
  return state.edit.rowRevs?.[muUidFor(state, muIdx)] ?? 0;
}

/**
 * How a request names the pulse train it acts on: the server-side session row
 * (id + revision) when there is a session, otherwise the uploaded values.
 * @param {State} state
 * @param {number} muIdx
 * @param {number[]} pulse Values held by the editor, used only without a session.
 * @returns {Record<string, unknown>}
 */
export function pulseSource(state, muIdx, pulse) {
  if (!state.edit.sessionToken) return { pulse_train: pulse };
  return {
    session_token: state.edit.sessionToken,
    mu_uid: muUidFor(state, muIdx),
    row_rev: rowRevOf(state, muIdx),
  };
}

/**
 * Whether the row held by the editor for this MU is the one the server has in
 * force (always true outside lazy mode, where every row is held).
 * @param {State} state
 * @param {number} muIdx
 * @returns {boolean}
 */
export function rowIsCurrent(state, muIdx) {
  if (!state.edit.lazyRows) return true;
  const held = state.edit.rowCacheRevs[muUidFor(state, muIdx)];
  return held !== undefined && held === rowRevOf(state, muIdx);
}
