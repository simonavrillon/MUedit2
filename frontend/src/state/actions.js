import { createEditSlice } from "./state.js";

/** @typedef {import("./state.js").State} State */
/** @typedef {import("./state.js").FileRef} FileRef */
/** @typedef {import("./state.js").ChannelTrace} ChannelTrace */
/** @typedef {import("./state.js").Selection} Selection */
/** @typedef {import("./state.js").EditMode} EditMode */
/** @typedef {import("./state.js").PulseView} PulseView */
/** @typedef {import("../api/binary-payloads.js").EditSessionFrame} EditSessionFrame */
/** @typedef {import("./state.js").Bookmark} Bookmark */
/** @typedef {import("./state.js").EditHistoryEntry} EditHistoryEntry */
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
 */
export function ensureDiscardMasks(state) {
  if (!state.channelMeans.length) return;
  if (state.discardMasks.length !== state.channelMeans.length) {
    const badPerGrid = state.metadata?.bad_channels_per_grid;
    state.discardMasks = state.channelMeans.map((cm, gridIdx) => {
      const bad = badPerGrid?.[gridIdx];
      if (Array.isArray(bad) && bad.length === cm.length) {
        return bad.map((v) => (v === 1 ? 1 : 0));
      }
      return cm.map(() => 0);
    });
  }
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
export function setEditCurrentMuGrid(state, idx, { resetView = true } = {}) {
  state.edit.currentMuGrid = Math.max(0, idx || 0);
  if (resetView) state.edit.view = null;
}

/**
 * @param {State} state
 * @param {number} idx
 * @param {{ resetView?: boolean }} [options]
 */
export function setEditCurrentMu(state, idx, { resetView = true } = {}) {
  state.edit.currentMu = Number.isNaN(idx) ? 0 : idx;
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
 * @param {string[]} muscle
 */
export function setMuscle(state, muscle) {
  state.muscle = muscle;
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
  state.file = file || null;
}

/**
 * @param {State} state
 * @param {string | null} token
 */
export function setUploadToken(state, token) {
  state.uploadToken = token || null;
}

/**
 * @param {State} state
 * @param {number | null | undefined} totalSamples
 */
export function setSeriesLength(state, totalSamples) {
  state.seriesLength = totalSamples ?? null;
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
  if (!state.gridNames.length) {
    state.currentGrid = 0;
    return;
  }
  if (!Number.isFinite(state.currentGrid) || state.currentGrid < 0) {
    state.currentGrid = 0;
    return;
  }
  if (state.currentGrid >= state.gridNames.length) {
    state.currentGrid = state.gridNames.length - 1;
  }
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
 * @param {number} gridIdx
 * @param {ChannelTrace[]} trace
 */
export function setChannelTraceForGrid(state, gridIdx, trace) {
  state.channelTraces[gridIdx] = trace;
}

/**
 * @param {State} state
 * @param {Record<number, boolean>} loadingMap
 */
export function setQcWindowLoading(state, loadingMap) {
  state.qcWindowLoading = loadingMap;
}

/**
 * @param {State} state
 * @param {number} gridIdx
 * @param {boolean} isLoading
 */
export function setQcWindowLoadingForGrid(state, gridIdx, isLoading) {
  state.qcWindowLoading[gridIdx] = isLoading;
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
 * @param {ChannelTrace[]} auxiliary
 * @param {string[]} auxiliaryNames
 */
export function setAuxData(state, auxiliary, auxiliaryNames) {
  state.auxSeries = auxiliary;
  state.auxNames = auxiliaryNames;
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
export function clearPreviewState(state) {
  state.gridSeries = [];
  state.gridNames = [];
  state.channelMeans = [];
  state.channelTraces = [];
  state.seriesLength = null;
}

/**
 * @param {State} state
 */
export function clearEditPulseSelections(state) {
  state.edit.selectionPulse = null;
  state.edit.draftSelectionPulse = null;
}

/**
 * @param {State} state
 */
export function clearEditDrSelections(state) {
  state.edit.selectionDr = null;
  state.edit.draftSelectionDr = null;
}

/**
 * @param {State} state
 */
export function clearAllEditSelections(state) {
  clearEditPulseSelections(state);
  clearEditDrSelections(state);
}

/**
 * @param {State} state
 * @param {FileRef | null | undefined} file
 */
export function setEditFile(state, file) {
  state.edit.file = file || null;
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
 * @param {string[]} gridNames
 */
export function setEditGridNames(state, gridNames) {
  state.edit.gridNames = gridNames;
}

/**
 * @param {State} state
 * @param {Bookmark | null} position
 */
export function setEditBookmark(state, position) {
  state.edit.bookmarkPosition = position || null;
}

/**
 * @param {State} state
 * @param {boolean} show
 */
export function setShowBookmark(state, show) {
  state.edit.showBookmark = !!show;
}

/**
 * @param {State} state
 * @param {Selection | null} selection
 */
export function setEditPulseSelection(state, selection) {
  state.edit.selectionPulse = selection || null;
}

/**
 * @param {State} state
 * @param {Selection | null} selection
 */
export function setEditPulseDraftSelection(state, selection) {
  state.edit.draftSelectionPulse = selection || null;
}

/**
 * @param {State} state
 * @param {Selection | null} selection
 */
export function setEditDrSelection(state, selection) {
  state.edit.selectionDr = selection || null;
}

/**
 * @param {State} state
 * @param {Selection | null} selection
 */
export function setEditDrDraftSelection(state, selection) {
  state.edit.draftSelectionDr = selection || null;
}

/**
 * @param {State} state
 */
export function resetEditSlice(state) {
  state.edit = createEditSlice();
}

/**
 * @param {State} state
 * @param {boolean} inFlight
 */
export function setRunDownloadInFlight(state, inFlight) {
  state.runDownloadInFlight = !!inFlight;
}

/**
 * @param {State} state
 * @param {string} key
 */
export function setLastRunDownloadKey(state, key) {
  state.lastRunDownloadKey = String(key || "");
}

/**
 * @param {State} state
 * @param {unknown} token  Server handle on the finished run's pulse trains.
 */
export function setRunResultToken(state, token) {
  state.runResultToken = typeof token === "string" ? token : "";
}

/**
 * @param {State} state
 * @param {boolean} isRunning
 */
export function setIsRunning(state, isRunning) {
  state.isRunning = !!isRunning;
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
  state.roiDraft = draft || null;
}

/**
 * @param {State} state
 * @param {number} idx
 * @param {Span} roi
 */
export function setRoiForIndex(state, idx, roi) {
  if (idx < 0) return;
  while (state.rois.length <= idx) {
    state.rois.push({ start: 0, end: 0 });
  }
  state.rois[idx] = { start: roi.start, end: roi.end };
}

/**
 * @param {State} state
 * @param {boolean} on
 */
export function setArtifactMode(state, on) {
  state.artifactMode = !!on;
}

/**
 * @param {State} state
 * @param {Span | null} draft
 */
export function setArtifactDraft(state, draft) {
  state.artifactDraft = draft || null;
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
export function setDiscardMasks(state, masks) {
  if (!Array.isArray(masks)) return;
  state.discardMasks = masks.map((grid) =>
    Array.isArray(grid) ? grid.map((v) => (v ? 1 : 0)) : [],
  );
}

/**
 * @param {State} state
 * @param {number} gridIdx
 * @param {number} chIdx
 * @param {number} value
 */
export function setDiscardMaskChannel(state, gridIdx, chIdx, value) {
  (state.discardMasks[gridIdx] ??= [])[chIdx] = value ? 1 : 0;
}

/**
 * Keep only the MUs at `keptIdx`, in that order, across every per-MU array.
 *
 * @param {State} state
 * @param {number[]} keptIdx
 */
export function keepEditMus(state, keptIdx) {
  const e = state.edit;
  /**
   * @template T
   * @param {T[] | null | undefined} arr
   * @param {(i: number) => T} fallback
   */
  const pick = (arr, fallback) => keptIdx.map((i) => arr?.[i] ?? fallback(i));
  e.distimes = pick(e.distimes, () => new Int32Array(0));
  e.artifactTimes = pick(e.artifactTimes, () => new Int32Array(0));
  e.muGridIndex = pick(e.muGridIndex, () => 0);
  e.flagged = pick(e.flagged, () => false);
  e.muUids = pick(e.muUids, (i) => `mu${i}`);
  e.versions = pick(e.versions, () => 0);
  e.hasPulse = pick(e.hasPulse, () => false);
  e.currentMu = Math.max(0, keptIdx.indexOf(e.currentMu ?? 0));
  const bookmarkIdx = e.bookmarkPosition
    ? keptIdx.indexOf(e.bookmarkPosition.muIdx)
    : -1;
  e.bookmarkPosition =
    bookmarkIdx === -1 || !e.bookmarkPosition
      ? null
      : { ...e.bookmarkPosition, muIdx: bookmarkIdx };
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
  e.hasPulse = fill(meta.has_pulse, () => false).map(Boolean);
  e.dirty = !!meta.dirty;
  e.canUndo = !!meta.can_undo;
}

/**
 * Take over an edit session's whole state (on open, recovery or a reload).
 *
 * @param {State} state
 * @param {EditSessionFrame} frame
 */
export function setEditSession(state, { meta, spikes, artifacts }) {
  const e = state.edit;
  e.token = String(meta.token || "");
  e.distimes = spikes;
  e.artifactTimes = spikes.map((_, i) => artifacts[i] ?? new Int32Array(0));
  setEditPerMu(state, meta);
  e.editHistory = Array.isArray(meta.edit_history) ? meta.edit_history : [];
  e.fsamp = meta.fsamp ?? null;
  e.totalSamples = Number(meta.total_samples) || 0;
  e.parameters =
    meta.parameters && typeof meta.parameters === "object"
      ? meta.parameters
      : {};
  e.pulseView = null;
}

/**
 * Apply what one edit changed: MUs kept (when some were removed), the
 * changed MUs' times, the per-MU fields and the new log entries.
 *
 * @param {State} state
 * @param {EditSessionFrame} frame
 */
export function applyEditChange(state, { meta, spikes, artifacts }) {
  const e = state.edit;
  if (Array.isArray(meta.kept_indices)) keepEditMus(state, meta.kept_indices);
  const n = Number(meta.n_mu) || 0;
  while (e.distimes.length < n) e.distimes.push(new Int32Array(0));
  while (e.artifactTimes.length < n) e.artifactTimes.push(new Int32Array(0));
  e.distimes.length = n;
  e.artifactTimes.length = n;
  (Array.isArray(meta.changed) ? meta.changed : []).forEach((mu, i) => {
    e.distimes[mu] = spikes[i] ?? new Int32Array(0);
    e.artifactTimes[mu] = artifacts[i] ?? new Int32Array(0);
  });
  setEditPerMu(state, meta);
  const start = Math.max(0, Number(meta.history_start) || 0);
  e.editHistory = [
    ...(e.editHistory || []).slice(0, start),
    ...(Array.isArray(meta.history) ? meta.history : []),
  ];
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
  }
}

/**
 * @param {State} state
 * @param {PulseView | null} view
 */
export function setEditPulseView(state, view) {
  state.edit.pulseView = view || null;
}

/**
 * @param {State} state
 * @param {string | null | undefined} versions
 */
export function setEditSoftwareVersions(state, versions) {
  state.edit.softwareVersions = versions ?? null;
}
