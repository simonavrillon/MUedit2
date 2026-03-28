import {
  clearPreviewState,
  resetEditSlice,
  setChannelTraces,
  setFile,
  setQcWindowLoading,
  setUploadToken,
} from "./actions.js";

/**
 * Checklist transition for import -> preview flow.
 * Keeps raw file selection state updates consistent across all entry points.
 */
export function beginRawPreviewTransition(state, fileLike) {
  const previousEditBidsRoot = String(state?.edit?.bidsRoot || "");
  resetEditSlice(state);
  state.edit.bidsRoot = previousEditBidsRoot;
  setFile(state, fileLike || null);
  setUploadToken(state, null);
  setChannelTraces(state, []);
  setQcWindowLoading(state, {});
}

/**
 * Checklist transition for failed preview.
 */
export function rollbackRawPreviewTransition(state) {
  setFile(state, null);
  setUploadToken(state, null);
  clearPreviewState(state);
}
