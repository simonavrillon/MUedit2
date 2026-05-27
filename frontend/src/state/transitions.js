import {
  clearPreviewState,
  resetEditSlice,
  setChannelTraces,
  setFile,
  setQcWindowLoading,
  setUploadToken,
} from "./actions.js";

// Keeps raw file selection state updates consistent across all entry points.
export function beginRawPreviewTransition(state, fileLike) {
  const previousEditBidsRoot = String(state?.edit?.bidsRoot || "");
  resetEditSlice(state);
  state.edit.bidsRoot = previousEditBidsRoot;
  setFile(state, fileLike || null);
  setUploadToken(state, null);
  setChannelTraces(state, []);
  setQcWindowLoading(state, {});
}

export function rollbackRawPreviewTransition(state) {
  setFile(state, null);
  setUploadToken(state, null);
  clearPreviewState(state);
}
