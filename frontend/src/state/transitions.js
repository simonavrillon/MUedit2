import {
  clearPreviewState,
  resetEditSlice,
  setChannelTraces,
  setFile,
  setQcWindowLoading,
  setUploadToken,
} from "./actions.js";

/** @typedef {import("../app/state.js").State} State */
/** @typedef {import("../app/state.js").FileRef} FileRef */

/**
 * Keeps raw file selection state updates consistent across all entry points.
 *
 * @param {State} state
 * @param {FileRef | null | undefined} fileLike
 */
export function beginRawPreviewTransition(state, fileLike) {
  const previousEditBidsRoot = String(state?.edit?.bidsRoot || "");
  resetEditSlice(state);
  state.edit.bidsRoot = previousEditBidsRoot;
  setFile(state, fileLike || null);
  setUploadToken(state, null);
  setChannelTraces(state, []);
  setQcWindowLoading(state, {});
  state.discardMasks = [];
}

/**
 * @param {State} state
 */
export function rollbackRawPreviewTransition(state) {
  setFile(state, null);
  setUploadToken(state, null);
  clearPreviewState(state);
}
