/**
 * Reads of `state` that need derivation; use these where one exists.
 */
/** @typedef {import("./state.js").State} State */
/** @typedef {import("../app/context.js").Span} Span */

/**
 * @param {State} state
 * @param {number} grid
 * @returns {number[]}
 */
export function getEditMuIndicesForGrid(state, grid) {
  return state.edit.muGridIndex.flatMap((g, mu) => (g === grid ? [mu] : []));
}

/**
 * The edit view: the samples on screen, or the whole recording before one is set.
 *
 * @param {State} state
 * @returns {Span}
 */
export function getEditView(state) {
  return state.edit.view || { start: 0, end: state.edit.totalSamples };
}
