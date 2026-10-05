import {
  resetEditSlice,
  setChannelTraces,
  setDiscardMasks,
  setFile,
  setRunLive,
  setRunResultToken,
  setUploadToken,
} from "./actions.js";

/** @typedef {import("./state.js").State} State */
/** @typedef {import("./state.js").FileRef} FileRef */

/**
 * A raw file whose preview has arrived replaces the one shown: its file, and
 * nothing of the previous file's upload, traces or edit session.
 *
 * @param {State} state
 * @param {FileRef | null | undefined} fileLike
 */
export function beginRawPreviewTransition(state, fileLike) {
  resetEditSlice(state);
  setFile(state, fileLike || null);
  setUploadToken(state, null);
  setChannelTraces(state, []);
  setDiscardMasks(state, []);
  // The previous file's run no longer applies; one still streaming keeps its page.
  if (!state.isRunning) {
    setRunLive(state, null);
    setRunResultToken(state, "");
  }
}
