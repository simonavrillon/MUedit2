/**
 * The only setters of `state`: every change to the app state goes through
 * a function here.
 */
import { createEditSlice } from "./state.js";

/** @typedef {import("./state.js").State} State */
/** @typedef {import("./state.js").FileRef} FileRef */
/** @typedef {import("./state.js").ChannelTrace} ChannelTrace */
/** @typedef {import("./state.js").Selection} Selection */
/** @typedef {import("./state.js").TargetSegment} TargetSegment */
/** @typedef {import("./state.js").EditMode} EditMode */
/** @typedef {import("./state.js").PulseView} PulseView */
/** @typedef {import("../api/binary-payloads.js").EditSessionFrame} EditSessionFrame */
/** @typedef {import("./state.js").Bookmark} Bookmark */
/** @typedef {import("../app/context.js").Span} Span */
/** @typedef {import("../app/context.js").StageKey} StageKey */
/** @typedef {import("../app/context.js").JsonObject} JsonObject */

/**
 * @param {State} state
 * @param {number} idx
 */
export function setCurrentGrid(state, idx) {
  state.currentGrid = Math.max(0, idx || 0);
}

/**
 * @param {State} state
 * @param {StageKey} stage
 */
export function setCurrentStage(state, stage) {
  state.currentStage = stage;
}

/**
 * @param {State} state
 * @param {EditMode | null} mode
 */
export function setEditMode(state, mode) {
  state.edit.mode = mode;
}

/**
 * @param {State} state
 * @param {number} idx
 * @param {{ resetView?: boolean }} [options]
 */
export function setEditCurrentGrid(state, idx, { resetView = true } = {}) {
  state.edit.currentGrid = Math.max(0, idx || 0);
  if (resetView) state.edit.view = null;
}

/**
 * @param {State} state
 * @param {number} idx
 * @param {{ resetView?: boolean }} [options]
 */
export function setEditCurrentMu(state, idx, { resetView = true } = {}) {
  state.edit.currentMu = Math.max(0, idx || 0);
  if (resetView) state.edit.view = null;
}

/**
 * @param {State} state
 * @param {string | null | undefined} value
 */
export function setEditProject(state, value) {
  state.edit.project = String(value || "").trim();
}

/**
 * @param {State} state
 * @param {string[]} muscleNames
 */
export function setMuscleNames(state, muscleNames) {
  state.muscleNames = muscleNames;
}

/**
 * @param {State} state
 * @param {Span | null} view
 */
export function setEditView(state, view) {
  state.edit.view = view;
}

/**
 * @param {State} state
 * @param {FileRef | null} file
 */
export function setFile(state, file) {
  state.file = file;
}

/**
 * @param {State} state
 * @param {string | null} token
 */
export function setUploadToken(state, token) {
  state.uploadToken = token;
}

/**
 * @param {State} state
 * @param {number | null | undefined} totalSamples
 */
export function setTotalSamples(state, totalSamples) {
  state.totalSamples = totalSamples ?? null;
}

/**
 * @param {State} state
 * @param {Span[]} rois
 */
export function setRois(state, rois) {
  state.rois = rois;
}

/**
 * @param {State} state
 * @param {ChannelTrace[]} series
 */
export function setGridSeries(state, series) {
  state.gridSeries = series;
}

/**
 * @param {State} state
 * @param {string[]} names
 */
export function setGridNames(state, names) {
  state.gridNames = names;
  state.currentGrid = Math.min(
    state.currentGrid,
    Math.max(0, names.length - 1),
  );
}

/**
 * @param {State} state
 * @param {number[][]} means
 */
export function setChannelMeans(state, means) {
  state.channelMeans = means;
}

/**
 * @param {State} state
 * @param {number[][][]} coordinates
 */
export function setCoordinates(state, coordinates) {
  state.coordinates = coordinates;
}

/**
 * @param {State} state
 * @param {ChannelTrace[][]} traces
 */
export function setChannelTraces(state, traces) {
  state.channelTraces = traces;
}

/**
 * @param {State} state
 * @param {number} grid
 * @param {ChannelTrace[]} trace
 */
export function setChannelTraceForGrid(state, grid, trace) {
  state.channelTraces[grid] = trace;
}

/**
 * @param {State} state
 * @param {JsonObject} metadata
 */
export function setMetadata(state, metadata) {
  state.metadata = metadata;
}

/**
 * @param {State} state
 * @param {ChannelTrace[]} auxSeries
 * @param {string[]} auxNames
 */
export function setAuxData(state, auxSeries, auxNames) {
  state.auxSeries = auxSeries;
  state.auxNames = auxNames;
}

/**
 * @param {State} state
 * @param {TargetSegment[]} segments
 */
export function setSegments(state, segments) {
  state.segments = segments;
}

/**
 * @param {State} state
 * @param {number | null | undefined} index
 */
export function setTargetIndex(state, index) {
  state.targetIndex = Number.isInteger(index) ? Number(index) : null;
}

/**
 * @param {State} state
 * @param {import("../decomp/live.js").RunLive | null} live
 */
export function setRunLive(state, live) {
  state.runLive = live;
}

/**
 * @param {State} state
 * @param {JsonObject | null | undefined} parameters
 */
export function setParameters(state, parameters) {
  state.parameters = parameters || null;
}

/**
 * @param {State} state
 */
export function clearEditPulseSelections(state) {
  state.edit.pulseSelection = null;
  state.edit.pulseSelectionDraft = null;
}

/**
 * @param {State} state
 * @param {string | null | undefined} filename
 */
export function setEditFilename(state, filename) {
  state.edit.filename = String(filename || "");
}

/**
 * @param {State} state
 * @param {boolean} busy
 */
export function setEditBusy(state, busy) {
  state.edit.busy = busy;
}

/**
 * @param {State} state
 * @param {Bookmark | null} bookmark
 */
export function setEditBookmark(state, bookmark) {
  state.edit.bookmark = bookmark;
}

/**
 * @param {State} state
 * @param {boolean} show
 */
export function setEditShowBookmark(state, show) {
  state.edit.showBookmark = show;
}

/**
 * @param {State} state
 * @param {Selection | null} selection
 */
export function setEditPulseSelection(state, selection) {
  state.edit.pulseSelection = selection;
}

/**
 * @param {State} state
 * @param {Selection | null} selection
 */
export function setEditPulseSelectionDraft(state, selection) {
  state.edit.pulseSelectionDraft = selection;
}

/**
 * @param {State} state
 */
export function resetEditSlice(state) {
  state.edit = createEditSlice();
}

/**
 * @param {State} state
 * @param {string} token  The run result token of the run last saved.
 */
export function setSavedRunToken(state, token) {
  state.savedRunToken = token;
}

/**
 * @param {State} state
 * @param {string} token  Server handle on the finished run's pulse trains.
 */
export function setRunResultToken(state, token) {
  state.runResultToken = token;
}

/**
 * @param {State} state
 * @param {boolean} isRunning
 */
export function setIsRunning(state, isRunning) {
  state.isRunning = isRunning;
}

/**
 * @param {State} state
 * @param {number | string | null | undefined} fsamp
 */
export function setFsamp(state, fsamp) {
  const n = Number(fsamp);
  state.fsamp = Number.isFinite(n) && n > 0 ? n : null;
}

/**
 * @param {State} state
 * @param {Span | null} draft
 */
export function setRoiDraft(state, draft) {
  state.roiDraft = draft;
}

/**
 * @param {State} state
 * @param {number} idx
 * @param {Span} roi
 */
export function setRoiForIndex(state, idx, roi) {
  state.rois = state.rois.map((r, i) =>
    i === idx ? { start: roi.start, end: roi.end } : r,
  );
}

/**
 * @param {State} state
 * @param {boolean} on
 */
export function setArtifactMode(state, on) {
  state.artifactMode = on;
}

/**
 * @param {State} state
 * @param {Span | null} draft
 */
export function setArtifactDraft(state, draft) {
  state.artifactDraft = draft;
}

/**
 * @param {State} state
 * @param {Span[]} regions
 */
export function setArtifactRegions(state, regions) {
  state.artifactRegions = regions;
}

/**
 * @param {State} state
 * @param {Span} region
 */
export function addArtifactRegion(state, region) {
  state.artifactRegions.push({ start: region.start, end: region.end });
}

/**
 * @param {State} state
 */
export function removeLastArtifactRegion(state) {
  if (!state.artifactRegions.length) return false;
  state.artifactRegions.pop();
  return true;
}

/**
 * @param {State} state
 * @param {(number | boolean)[][] | null | undefined} masks
 */
export function setDiscardChannels(state, masks) {
  if (!Array.isArray(masks)) return;
  state.discardChannels = masks.map((grid) =>
    Array.isArray(grid) ? grid.map((v) => (v ? 1 : 0)) : [],
  );
}

/**
 * @param {State} state
 * @param {number} grid
 * @param {number} channel
 * @param {number} value
 */
export function setDiscardChannel(state, grid, channel, value) {
  (state.discardChannels[grid] ??= [])[channel] = value ? 1 : 0;
}

/**
 * Keep only the MUs at `keptIdx`, in that order: their discharge times, and the
 * current MU and bookmark moved to their new places. The other per-MU fields
 * come in full with the change that removed MUs.
 *
 * @param {State} state
 * @param {number[]} keptIdx
 */
function keepEditMus(state, keptIdx) {
  const e = state.edit;
  e.spikeTimes = keptIdx.map((i) => e.spikeTimes[i] ?? new Int32Array(0));
  e.currentMu = Math.max(0, keptIdx.indexOf(e.currentMu));
  const bookmarkIdx = e.bookmark ? keptIdx.indexOf(e.bookmark.mu) : -1;
  e.bookmark =
    bookmarkIdx === -1 || !e.bookmark
      ? null
      : { ...e.bookmark, mu: bookmarkIdx };
}

/**
 * Per-MU fields every edit-session response carries in full.
 *
 * @param {State} state
 * @param {JsonObject} meta
 */
function setEditPerMu(state, meta) {
  const e = state.edit;
  const n = Number(meta.n_mu) || 0;
  /** @param {unknown} arr @param {(i: number) => any} fallback */
  const fill = (arr, fallback) =>
    Array.from({ length: n }, (_, i) =>
      Array.isArray(arr) && arr[i] !== undefined ? arr[i] : fallback(i),
    );
  e.flagged = fill(meta.flagged, () => false).map(Boolean);
  e.muUids = fill(meta.mu_uids, (i) => `mu${i}`).map(String);
  e.muGridIndex = fill(meta.mu_grid_index, () => 0).map(Number);
  e.versions = fill(meta.versions, () => 0).map(Number);
  e.dirty = !!meta.dirty;
  e.canUndo = !!meta.can_undo;
  e.crashRecovery = meta.crash_recovery !== false;
}

/**
 * Take over an edit session's whole state (on open, recovery or a reload).
 *
 * @param {State} state
 * @param {EditSessionFrame} frame
 */
export function setEditSession(state, { meta, spikeTimes }) {
  const e = state.edit;
  e.token = String(meta.token || "");
  e.spikeTimes = spikeTimes;
  setEditPerMu(state, meta);
  e.editHistory = Array.isArray(meta.edit_history) ? meta.edit_history : [];
  e.editHistoryStart = Number(meta.edit_history_start) || 0;
  e.fsamp = meta.fsamp ?? null;
  e.totalSamples = Number(meta.total_samples) || 0;
  e.pulseView = null;
}

/**
 * Apply what one edit changed: MUs kept (when some were removed), the
 * changed MUs' discharge times, the per-MU fields and the new log entries.
 *
 * @param {State} state
 * @param {EditSessionFrame} frame
 */
export function applyEditChange(state, { meta, spikeTimes }) {
  const e = state.edit;
  if (Array.isArray(meta.kept_indices)) keepEditMus(state, meta.kept_indices);
  const n = Number(meta.n_mu) || 0;
  while (e.spikeTimes.length < n) e.spikeTimes.push(new Int32Array(0));
  e.spikeTimes.length = n;
  (Array.isArray(meta.changed) ? meta.changed : []).forEach((mu, i) => {
    e.spikeTimes[mu] = spikeTimes[i] ?? new Int32Array(0);
  });
  setEditPerMu(state, meta);
  const start = Math.max(0, Number(meta.history_start) || 0);
  const offset = e.editHistoryStart;
  e.editHistory = [
    ...e.editHistory.slice(0, Math.max(0, start - offset)),
    ...(Array.isArray(meta.history) ? meta.history : []),
  ];
  e.editHistoryStart = Math.min(offset, start);
}

/**
 * Mirror a session save: the file keeps `kept_indices`, logged as it returns.
 *
 * @param {State} state
 * @param {JsonObject} saved
 */
export function applyEditSave(state, saved) {
  if (Array.isArray(saved.kept_indices)) {
    keepEditMus(state, saved.kept_indices);
  }
  setEditPerMu(state, saved);
  if (Array.isArray(saved.edit_history)) {
    state.edit.editHistory = saved.edit_history;
    state.edit.editHistoryStart = Number(saved.edit_history_start) || 0;
  }
}

/**
 * @param {State} state
 * @param {PulseView | null} view
 */
export function setEditPulseView(state, view) {
  state.edit.pulseView = view;
}

/**
 * @param {State} state
 * @param {string | null | undefined} versions
 */
export function setEditSoftwareVersions(state, versions) {
  state.edit.softwareVersions = versions ?? null;
}
