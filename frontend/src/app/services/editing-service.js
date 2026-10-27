/**
 * Edit-stage application service. The edit session lives on the server: every
 * edit sends `{op, mu, window}` and applies the change it returns (the edited
 * MU's discharge times, flags and log entries); the pulse trains never come
 * to the page except as the window on screen. Every exported action takes the
 * `app` context and reaches the server through `app.api`. State mutations go
 * exclusively through `state/actions.js`.
 */
import { handleError } from "./error-service.js";
import { RequestTimeout } from "../http.js";
import { isToggleOn } from "../../view/controls.js";
import { inferGridCount, normalizeGridNames } from "../../io/grid.js";
import { getSuggestedNpzName } from "../../io/bids.js";
import {
  applyEditChange,
  applyEditSave,
  clearEditPulseSelections,
  setEditBookmark,
  setEditBusy,
  setEditShowBookmark,
  setEditCurrentMu,
  setEditCurrentGrid,
  setEditFilename,
  setEditSession,
  setEditView,
  setGridNames,
} from "../../state/actions.js";
import { getEditView } from "../../state/selectors.js";

/** @typedef {import("../context.js").App} App */
/** @typedef {import("../context.js").JsonObject} JsonObject */
/** @typedef {import("../context.js").RoiOp} RoiOp */
/** @typedef {import("../context.js").RoiAddPayload} RoiAddPayload */
/** @typedef {import("../context.js").RoiDeletePayload} RoiDeletePayload */
/** @typedef {import("../context.js").Tone} Tone */
/** @typedef {import("../../state/state.js").State} State */
/** @typedef {import("../../state/state.js").FileRef} FileRef */
/** @typedef {import("../../state/state.js").Bookmark} Bookmark */
/** @typedef {import("../../api/binary-payloads.js").EditSessionFrame} EditSessionFrame */

// The open session's token survives a reload of the page (not of the server),
// so a reloaded tab can pick the session up again.
const SESSION_TOKEN_KEY = "muedit.editSession";

/** Resuming at a log entry without a view shows this long either side of its edits. */
const RESUME_HALF_SPAN_S = 2;
/** The sampling rate assumed for that when the file does not give one. */
const ASSUMED_FSAMP = 2048;

/** @param {string} token */
function rememberSessionToken(token) {
  try {
    globalThis.sessionStorage?.setItem(SESSION_TOKEN_KEY, token);
  } catch {
    // Storage can be unavailable (private mode); the session just is not restored.
  }
}

/** @returns {string} */
function rememberedSessionToken() {
  try {
    return globalThis.sessionStorage?.getItem(SESSION_TOKEN_KEY) || "";
  } catch {
    return "";
  }
}

function forgetSessionToken() {
  try {
    globalThis.sessionStorage?.removeItem(SESSION_TOKEN_KEY);
  } catch {
    // See rememberSessionToken.
  }
}

/**
 * @param {State} state
 * @param {number} mu
 * @param {number} position
 */
function setEditBookmarkAndHide(state, mu, position) {
  setEditBookmark(state, { mu, position });
  setEditShowBookmark(state, false);
}

/**
 * Run one edit on the server and mirror what it changed.
 *
 * @param {App} app
 * @param {string} op
 * @param {JsonObject} [args]
 * @returns {Promise<JsonObject>} the change's fields
 */
async function requestEditOp(app, op, args = {}) {
  const { state, api } = app;
  const token = state.edit.token;
  if (!token) throw new Error("No decomposition is open");
  let frame;
  try {
    frame = await api.editOp(op, { token, ...args });
  } catch (err) {
    if (err instanceof RequestTimeout && (await resyncAfterTimeout(app, token)))
      throw new Error(
        "Request timed out; the page now shows the session as the server left it",
      );
    throw err;
  }
  requireSameSession(state, token);
  applyEditChange(state, frame);
  app.refreshEditModeButtons();
  return frame.meta;
}

/**
 * The page gave up on an edit the server may still carry out: take the
 * session as the server has it once that edit is done, so the page and the
 * server agree whether it happened.
 *
 * @param {App} app
 * @param {string} token
 * @returns {Promise<boolean>} whether the page took it
 */
async function resyncAfterTimeout(app, token) {
  const { state, api } = app;
  try {
    const frame = await api.editSessionState(token);
    if (state.edit.token !== token) return false;
    setEditSession(state, frame);
    app.refreshEditModeButtons();
    return true;
  } catch {
    // The edit's own failure is what gets reported.
    return false;
  }
}

/**
 * A change the server answered for a session the page has since left (another
 * decomposition was opened) is not this session's.
 *
 * @param {State} state
 * @param {string} token The session the request was for.
 */
function requireSameSession(state, token) {
  if (state.edit.token !== token) {
    throw new Error("Another decomposition was opened meanwhile");
  }
}

/**
 * `[text, tone]`, warning that unsaved edits are lost on a crash while the
 * server cannot log them.
 *
 * @param {State} state
 * @param {string} text
 * @param {Tone} tone
 * @returns {[string, Tone]}
 */
function withRecoveryNote(state, text, tone) {
  if (state.edit.crashRecovery) return [text, tone];
  return [
    `${text}; unsaved edits will not survive a crash: save to keep them`,
    "error",
  ];
}

/**
 * One edit the user asked for: say it is under way, run it, redraw, and say
 * how it went (`[text, tone]`, or nothing to leave the status be). A failure
 * is reported as `failed` and changes nothing.
 *
 * Edits go one at a time. One asked for while another runs is refused: it
 * was drawn on the pulse train as it was before the running edit changed it.
 *
 * @param {App} app
 * @param {{ pending?: string, failed: string, run: () => Promise<[string, Tone] | void> }} edit
 */
async function editAction(app, { pending, failed, run }) {
  const { state } = app;
  if (state.edit.busy) {
    app.setStatus("Wait for the current edit to finish", "muted");
    return;
  }
  setEditBusy(state, true);
  try {
    if (pending) app.setStatus(pending, "muted");
    const outcome = await run();
    app.renderEditExplorer();
    if (outcome) app.setStatus(...withRecoveryNote(state, ...outcome));
  } catch (err) {
    handleError(err, app.setStatus, failed);
  } finally {
    setEditBusy(state, false);
  }
}

/**
 * @param {App} app
 * @param {RoiOp} op
 * @param {RoiAddPayload | RoiDeletePayload} payload
 */
export async function requestRoiEdit(app, op, payload) {
  const { state } = app;
  return editAction(app, {
    pending: "Applying ROI...",
    failed: "ROI failed",
    async run() {
      const args = {
        mu: payload.mu,
        x_start: payload.xStart,
        x_end: payload.xEnd,
        y_min: payload.yMin,
      };
      await requestEditOp(
        app,
        op,
        "yMax" in payload ? { ...args, y_max: payload.yMax } : args,
      );
      const bookmarkPos = Math.round((payload.xStart + payload.xEnd) / 2);
      setEditBookmarkAndHide(state, payload.mu, bookmarkPos);
      clearEditPulseSelections(state);
      app.setEditMode(null);
      return ["ROI applied", "success"];
    },
  });
}

/**
 * Have the server filter a grid's EMG now, while the user looks at it, rather
 * than on the grid's first filter update. Nothing waits for it: a filter
 * update sent meanwhile waits on the server for the same filtering.
 *
 * @param {App} app
 * @param {number} grid
 */
export function prepareEditGrid(app, grid) {
  const { state, api } = app;
  if (!state.edit.token) return;
  api.editPrepareGrid(state.edit.token, grid, state.edit.project).catch(() => {
    // The filter update filters the grid itself, and reports what fails.
  });
}

/** @param {App} app */
export async function requestFilterUpdate(app) {
  const { state, els, setStatus } = app;
  if (!state.edit.spikeTimes.length) return;
  const mu = state.edit.currentMu;
  const total = state.edit.totalSamples;
  if (!total) {
    setStatus("No pulse train available", "muted");
    return;
  }
  const view = getEditView(state);
  const start = Math.max(0, view.start);
  const end = Math.min(total, view.end);
  return editAction(app, {
    pending: "Updating filter from BIDS EMG...",
    failed: "Filter update failed",
    async run() {
      await requestEditOp(app, "update-filter", {
        mu,
        view_start: start,
        view_end: end,
        use_peeloff: isToggleOn(els.editPeelOffToggle),
        lock_spikes: isToggleOn(els.editLockSpikesToggle),
        project: state.edit.project,
      });
      setEditBookmarkAndHide(state, mu, Math.round((start + end) / 2));
      return ["MU filter updated", "success"];
    },
  });
}

/** @param {App} app */
export async function removeOutliers(app) {
  const { state, setStatus } = app;
  const mu = state.edit.currentMu;
  if ((state.edit.spikeTimes[mu]?.length ?? 0) < 3) {
    setStatus("Not enough discharges for outlier removal", "muted");
    return;
  }
  return editAction(app, {
    pending: "Removing outliers...",
    failed: "Outlier removal failed",
    async run() {
      const meta = await requestEditOp(app, "remove-outliers", { mu });
      const after = state.edit.spikeTimes[mu];
      const centerPos = after.length
        ? Math.round((after[0] + after[after.length - 1]) / 2)
        : Math.round(state.edit.totalSamples / 2);
      setEditBookmarkAndHide(state, mu, centerPos);
      return (Number(meta.removed_count) || 0) > 0
        ? ["Outliers removed", "success"]
        : ["No outliers detected", "muted"];
    },
  });
}

/** @param {App} app */
export async function removeDuplicateMus(app) {
  const { state, setStatus } = app;
  if (state.edit.spikeTimes.length < 2) {
    setStatus("Need at least 2 MUs to deduplicate", "muted");
    return;
  }
  return editAction(app, {
    pending: "Removing duplicates...",
    failed: "Deduplication failed",
    async run() {
      const meta = await requestEditOp(app, "remove-duplicates");
      const removed = Number(meta.removed_count) || 0;
      if (!removed) return ["No duplicates found", "muted"];
      setEditShowBookmark(state, false);
      return [
        `${removed} duplicate${removed !== 1 ? "s" : ""} removed`,
        "success",
      ];
    },
  });
}

/** @param {App} app */
export async function flagMuForDeletion(app) {
  const { state, setStatus } = app;
  const mu = state.edit.currentMu;
  if (!state.edit.spikeTimes.length) {
    setStatus("No MU loaded", "muted");
    return;
  }
  const flag = !state.edit.flagged[mu];
  return editAction(app, {
    pending: flag ? "Flagging MU for deletion..." : "Unflagging MU...",
    failed: "Flagging failed",
    async run() {
      await requestEditOp(app, "flag", { mu, flag });
      setEditShowBookmark(state, false);
      return [flag ? "MU flagged for deletion" : "MU unflagged", "success"];
    },
  });
}

/** @param {App} app */
export async function undoEdit(app) {
  const { state, setStatus } = app;
  if (!state.edit.canUndo) {
    setStatus("Nothing to undo", "muted");
    return;
  }
  return editAction(app, {
    failed: "Undo failed",
    async run() {
      await requestEditOp(app, "undo");
      clearEditPulseSelections(state);
      return ["Undo applied", "success"];
    },
  });
}

/** @param {App} app */
export async function resetCurrentMuEdits(app) {
  const { state } = app;
  if (!state.edit.spikeTimes.length) return;
  return editAction(app, {
    failed: "Reset failed",
    async run() {
      await requestEditOp(app, "reset", { mu: state.edit.currentMu });
      clearEditPulseSelections(state);
    },
  });
}

/** @param {App} app */
export async function duplicateMu(app) {
  const { state, setStatus } = app;
  if (!state.edit.spikeTimes.length) {
    setStatus("No MU loaded", "muted");
    return;
  }
  return editAction(app, {
    failed: "Duplicate failed",
    async run() {
      const meta = await requestEditOp(app, "duplicate", {
        mu: state.edit.currentMu,
      });
      const newIdx = Number(
        meta.changed?.[0] ?? state.edit.spikeTimes.length - 1,
      );
      setEditCurrentGrid(state, state.edit.muGridIndex[newIdx] ?? 0, {
        resetView: false,
      });
      setEditCurrentMu(state, newIdx, { resetView: false });
      return [`MU duplicated — now editing MU ${newIdx + 1}`, "success"];
    },
  });
}

/** @param {App} app */
export async function saveEditedFile(app) {
  const { state, api, withBidsSaveFields, getBidsMuscleNames, setStatus } = app;

  if (!state.edit.spikeTimes.length || !state.edit.token) {
    setStatus("Load a decomposition first", "error");
    return;
  }
  if (!app.checkPlacementForm()) {
    setStatus("Describe how the electrodes were placed before saving", "error");
    return;
  }
  const token = state.edit.token;
  const filename = state.edit.filename || "decomposition";
  return editAction(app, {
    pending: "Saving edited file...",
    failed: "Save failed",
    async run() {
      const saved = await api.editSessionSave(
        withBidsSaveFields({
          token,
          muscle_names: getBidsMuscleNames(),
          file_label: getSuggestedNpzName(filename, "_edited"),
          software_versions: state.edit.softwareVersions,
        }),
      );
      requireSameSession(state, token);
      // Mirror the saved file: the backend drops flagged and duplicate MUs and logs it.
      applyEditSave(state, saved);
      app.refreshEditModeButtons();
      app.setStatus("Saved", "success");
      const where = saved.path
        ? `Edited decomposition saved to ${saved.path}`
        : "Edited decomposition saved";
      const problems = [
        ...(saved.bids_emg_error
          ? [`BIDS EMG not written: ${saved.bids_emg_error}`]
          : []),
        ...(saved.bids_warnings || []),
      ];
      if (problems.length) return [`${where}; ${problems.join("; ")}`, "error"];
      return [where, "success"];
    },
  });
}

/**
 * Where to resume: the MU, view and bookmark of the last logged edit that
 * names an MU still in the file.
 *
 * @param {State} state
 */
function resumePosition(state) {
  const total = state.edit.totalSamples;
  const lastEntry = state.edit.editHistory.findLast((e) => e.mu_uid);
  let targetMu = 0;
  let targetView = { start: 0, end: total };
  /** @type {Bookmark | null} */
  let targetBookmark = null;
  const idx = lastEntry?.mu_uid
    ? state.edit.muUids.indexOf(lastEntry.mu_uid)
    : -1;
  if (lastEntry && idx !== -1) {
    targetMu = idx;
    const viewStart = lastEntry.view_start;
    const viewEnd = lastEntry.view_end;
    if (
      typeof viewStart === "number" &&
      typeof viewEnd === "number" &&
      Number.isFinite(viewStart) &&
      Number.isFinite(viewEnd)
    ) {
      targetView = { start: viewStart, end: viewEnd };
      targetBookmark = {
        mu: idx,
        position: Math.round((viewStart + viewEnd) / 2),
      };
    } else {
      const positions = [
        ...(lastEntry.spikes_added || []),
        ...(lastEntry.spikes_removed || []),
        ...(lastEntry.artifacts_added || []),
        ...(lastEntry.artifacts_removed || []),
      ];
      if (positions.length) {
        const mean = Math.round(
          positions.reduce((a, b) => a + b, 0) / positions.length,
        );
        const halfSpan = Math.min(
          Math.round(total * 0.1),
          (state.edit.fsamp || ASSUMED_FSAMP) * RESUME_HALF_SPAN_S,
        );
        targetView = {
          start: Math.max(0, mean - halfSpan),
          end: Math.min(total, mean + halfSpan),
        };
        targetBookmark = {
          mu: idx,
          position: Math.round((targetView.start + targetView.end) / 2),
        };
      }
    }
  }
  return { targetMu, targetView, targetBookmark };
}

/**
 * Show an edit session the server sent: its fields, grids and MUs, resuming
 * where its log says the user stopped.
 *
 * @param {App} app
 * @param {FileRef} file
 * @param {EditSessionFrame} frame
 * @param {{ open?: boolean }} [options] `open: false` loads it without leaving the current page.
 */
function showEditSession(app, file, frame, { open = true } = {}) {
  const { state, applySessionInfoFromDecomposition } = app;
  const { meta } = frame;
  const resolvedGridNames = normalizeGridNames(meta.grid_names, {
    minimumCount: inferGridCount({
      muGridIndex: meta.mu_grid_index,
      muscleNames: meta.muscle_names,
    }),
  });
  setGridNames(state, resolvedGridNames);
  applySessionInfoFromDecomposition(file, meta);
  app.setBidsProject(String(meta.project || "").trim());
  setEditFilename(state, file.name || meta.file_label || "decomposition");
  setEditSession(state, frame);
  rememberSessionToken(state.edit.token);

  const { targetMu, targetView, targetBookmark } = resumePosition(state);
  const targetGrid = state.edit.muGridIndex[targetMu] ?? 0;
  setEditCurrentGrid(state, targetGrid, { resetView: false });
  setEditCurrentMu(state, targetMu, { resetView: false });
  setEditView(state, targetView);
  if (targetBookmark) {
    setEditBookmark(state, targetBookmark);
    setEditShowBookmark(state, true);
  }
  clearEditPulseSelections(state);
  app.refreshEditModeButtons();
  app.renderBidsMuscleFields();
  // The QC tabs name the decomposition's grids.
  app.populateGridTabs();
  if (open) app.showWorkspace("edit");
  else app.updateStepAvailability();
  prepareEditGrid(app, state.edit.currentGrid);
}

/**
 * Ask whether to replay the unsaved edits an earlier session left for the file.
 *
 * @param {number} count
 */
function confirmRecovery(count) {
  const ask = globalThis.window?.confirm;
  return (
    typeof ask === "function" &&
    ask(
      `${count} unsaved edit${count !== 1 ? "s" : ""} to this file were left from an ` +
        "earlier session. Restore them?",
    )
  );
}

/**
 * Open a decomposition for editing. One that fails to open leaves the one
 * open as it was; once the server has opened it, the previous one is closed.
 *
 * @param {App} app
 * @param {FileRef} file
 * @param {string} path
 * @param {{ open?: boolean }} [options] `open: false` loads it without leaving the current page.
 */
export async function loadDecompositionForEdit(app, file, path, options = {}) {
  const { api, setUploadLoading, setStatus, resetEditState } = app;

  if (!path) return;
  setUploadLoading(true);
  setStatus("Loading...", "muted");
  let opened = false;
  try {
    let frame = await api.editOpen(path);
    opened = true;
    const recoverable = Number(frame.meta.recoverable_edits) || 0;
    let recovered = 0;
    if (recoverable > 0) {
      const token = String(frame.meta.token || "");
      frame = await api.editRecover(token, confirmRecovery(recoverable));
      recovered = Number(frame.meta.recovered_edits) || 0;
    }
    showEditSession(app, file, frame, options);
    setStatus(
      ...withRecoveryNote(
        app.state,
        recovered
          ? `Loaded, with ${recovered} unsaved edit${recovered !== 1 ? "s" : ""} restored.`
          : "Loaded. You can start interacting with the pulse train.",
        "success",
      ),
    );
  } catch (err) {
    handleError(err, setStatus, "Failed to load");
    if (opened) resetEditState();
  } finally {
    setUploadLoading(false);
  }
}

/**
 * Pick up the edit session this page had open before it was reloaded;
 * nothing happens when there was none or it is gone.
 *
 * @param {App} app
 */
export async function restoreEditSession(app) {
  const token = rememberedSessionToken();
  if (!token) return;
  try {
    const frame = await app.api.editSessionState(token);
    showEditSession(app, { name: String(frame.meta.file_label || "") }, frame);
    app.setStatus(
      ...withRecoveryNote(app.state, "Edit session restored", "success"),
    );
  } catch {
    forgetSessionToken();
  }
}
