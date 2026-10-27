/**
 * State changes that span several slices, such as replacing the open raw file.
 */
import {
  resetEditSlice,
  setChannelTraces,
  setFile,
  setRunLive,
  setRunResultToken,
} from "./actions.js";

/** @typedef {import("./state.js").State} State */
/** @typedef {import("./state.js").FileRef} FileRef */

/**
 * A raw file whose preview has arrived replaces the one shown: its file, and
 * nothing of the previous file's traces, run or edit session. The caller sets
 * the new upload and discard channels.
 *
 * @param {State} state
 * @param {FileRef | null | undefined} fileLike
 */
export function beginRawPreviewTransition(state, fileLike) {
  resetEditSlice(state);
  setFile(state, fileLike || null);
  setChannelTraces(state, []);
  // The previous file's run no longer applies; one still streaming keeps its page.
  if (!state.isRunning) {
    setRunLive(state, null);
    setRunResultToken(state, "");
  }
}
