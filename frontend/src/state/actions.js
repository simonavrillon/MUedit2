import { createEditSlice } from "../app/state.js";

/** @typedef {import("../app/state.js").State} State */
/** @typedef {import("../app/state.js").FileRef} FileRef */
/** @typedef {import("../app/state.js").ChannelTrace} ChannelTrace */
/** @typedef {import("../app/state.js").Selection} Selection */
/** @typedef {import("../app/state.js").EditMode} EditMode */
/** @typedef {import("../app/state.js").EditBackup} EditBackup */
/** @typedef {import("../app/state.js").Bookmark} Bookmark */
/** @typedef {import("../app/state.js").EditHistoryEntry} EditHistoryEntry */
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
  if (!state.channelMeans || !state.channelMeans.length) return;
  if (
    !state.discardMasks ||
    state.discardMasks.length !== state.channelMeans.length
  ) {
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
 * @param {number} idx
 * @param {{ resetView?: boolean }} [options]
 */
export function setRunCurrentMuGrid(state, idx, { resetView = true } = {}) {
  state.currentMuGrid = Math.max(0, idx || 0);
  if (resetView) state.runView = null;
}

/**
 * @param {State} state
 * @param {number} idx
 * @param {{ resetView?: boolean }} [options]
 */
export function setRunCurrentMu(state, idx, { resetView = true } = {}) {
  state.currentMu = Number.isNaN(idx) ? 0 : idx;
  if (resetView) state.runView = null;
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
 * @param {string | null | undefined} value
 */
export function setEditSignalToken(state, value) {
  state.edit.editSignalToken = String(value || "").trim();
}

/**
 * @param {State} state
 * @param {string[] | null | undefined} muscle
 */
export function setMuscle(state, muscle) {
  state.muscle = Array.isArray(muscle) ? muscle : [];
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
 * @param {Span | null} view
 */
export function setRunView(state, view) {
  state.runView = view;
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
 * @param {Span[] | null | undefined} rois
 */
export function setRois(state, rois) {
  state.rois = Array.isArray(rois) ? rois : [];
}

/**
 * @param {State} state
 * @param {number[] | null | undefined} series
 */
export function setPreviewSeries(state, series) {
  state.previewSeries = Array.isArray(series) ? series : [];
}

/**
 * @param {State} state
 * @param {number[][] | null | undefined} series
 */
export function setGridSeries(state, series) {
  state.gridSeries = Array.isArray(series) ? series : [];
}

/**
 * @param {State} state
 * @param {string[] | null | undefined} names
 */
export function setGridNames(state, names) {
  state.gridNames = Array.isArray(names) ? names : [];
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
 * @param {number[][] | null | undefined} means
 */
export function setChannelMeans(state, means) {
  state.channelMeans = Array.isArray(means) ? means : [];
}

/**
 * @param {State} state
 * @param {number[][][] | null | undefined} coordinates
 */
export function setCoordinates(state, coordinates) {
  state.coordinates = Array.isArray(coordinates) ? coordinates : [];
}

/**
 * @param {State} state
 * @param {ChannelTrace[][] | null | undefined} traces
 */
export function setChannelTraces(state, traces) {
  state.channelTraces = Array.isArray(traces) ? traces : [];
}

/**
 * @param {State} state
 * @param {number} gridIdx
 * @param {ChannelTrace[]} trace
 */
export function setChannelTraceForGrid(state, gridIdx, trace) {
  if (!Array.isArray(state.channelTraces)) {
    state.channelTraces = [];
  }
  state.channelTraces[gridIdx] = trace;
}

/**
 * @param {State} state
 * @param {Record<number, boolean> | null | undefined} loadingMap
 */
export function setQcWindowLoading(state, loadingMap) {
  state.qcWindowLoading =
    loadingMap && typeof loadingMap === "object" ? loadingMap : {};
}

/**
 * @param {State} state
 * @param {number} gridIdx
 * @param {boolean} isLoading
 */
export function setQcWindowLoadingForGrid(state, gridIdx, isLoading) {
  if (!state.qcWindowLoading || typeof state.qcWindowLoading !== "object") {
    state.qcWindowLoading = {};
  }
  state.qcWindowLoading[gridIdx] = !!isLoading;
}

/**
 * @param {State} state
 * @param {JsonObject | null | undefined} metadata
 */
export function setMetadata(state, metadata) {
  state.metadata = metadata && typeof metadata === "object" ? metadata : {};
}

/**
 * @param {State} state
 * @param {number[][] | null | undefined} auxiliary
 * @param {string[] | null | undefined} auxiliaryNames
 */
export function setAuxData(state, auxiliary, auxiliaryNames) {
  state.auxSeries = Array.isArray(auxiliary) ? auxiliary : [];
  state.auxNames = Array.isArray(auxiliaryNames) ? auxiliaryNames : [];
}

/**
 * @param {State} state
 * @param {number[][] | null | undefined} pulseTrains
 * @param {number[][] | null | undefined} distimes
 * @param {number[] | null | undefined} gridIndex
 */
export function setMuPreviewData(state, pulseTrains, distimes, gridIndex) {
  state.muPulseTrains = Array.isArray(pulseTrains) ? pulseTrains : [];
  state.muDistimes = Array.isArray(distimes) ? distimes : [];
  state.muGridIndex = Array.isArray(gridIndex) ? gridIndex : [];
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
  state.previewSeries = [];
  state.gridSeries = [];
  state.gridNames = [];
  state.channelMeans = [];
  state.channelTraces = [];
  state.seriesLength = null;
}

/**
 * @param {State} state
 * @param {number} muIdx
 * @param {number[] | null | undefined} distimes
 */
export function setEditDistimesForMu(state, muIdx, distimes) {
  const clean = (distimes || [])
    .map((v) => Number(v))
    .filter((v) => Number.isFinite(v));
  state.edit.distimes[muIdx] = clean;
}

/**
 * @param {State} state
 * @param {number} muIdx
 * @param {number[] | null | undefined} times
 */
export function setEditArtifactTimesForMu(state, muIdx, times) {
  if (!state.edit.artifactTimes) state.edit.artifactTimes = [];
  const clean = (times || [])
    .map((v) => Number(v))
    .filter((v) => Number.isFinite(v));
  state.edit.artifactTimes[muIdx] = clean;
}

/**
 * @param {State} state
 * @param {number} muIdx
 * @param {number[] | null | undefined} pulseTrain
 */
export function setEditPulseTrainForMu(state, muIdx, pulseTrain) {
  const clean = Array.isArray(pulseTrain)
    ? pulseTrain.map((v) => Number(v))
    : [];
  state.edit.pulseTrains[muIdx] = clean;
}

/**
 * @param {State} state
 * @param {number} muIdx
 * @param {boolean} flagged
 */
export function setEditFlagForMu(state, muIdx, flagged) {
  state.edit.flagged[muIdx] = !!flagged;
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
 * @param {number[][] | null | undefined} pulseTrains
 */
export function setEditPulseTrains(state, pulseTrains) {
  state.edit.pulseTrains = Array.isArray(pulseTrains) ? pulseTrains : [];
}

/**
 * @param {State} state
 * @param {number[][] | null | undefined} pulseTrains
 */
export function setEditOriginalPulseTrains(state, pulseTrains) {
  state.edit.originalPulseTrains = Array.isArray(pulseTrains)
    ? pulseTrains
    : [];
}

/**
 * @param {State} state
 * @param {number[][] | null | undefined} distimes
 */
export function setEditDistimes(state, distimes) {
  state.edit.distimes = Array.isArray(distimes) ? distimes : [];
}

/**
 * @param {State} state
 * @param {number[][] | null | undefined} distimes
 */
export function setEditOriginalDistimes(state, distimes) {
  state.edit.originalDistimes = Array.isArray(distimes) ? distimes : [];
}

/**
 * @param {State} state
 * @param {string[] | null | undefined} gridNames
 */
export function setEditGridNames(state, gridNames) {
  state.edit.gridNames = Array.isArray(gridNames) ? gridNames : [];
}

/**
 * @param {State} state
 * @param {number[] | null | undefined} muGridIndex
 */
export function setEditMuGridIndex(state, muGridIndex) {
  state.edit.muGridIndex = Array.isArray(muGridIndex) ? muGridIndex : [];
}

/**
 * @param {State} state
 * @param {number | null | undefined} fsamp
 */
export function setEditFsamp(state, fsamp) {
  state.edit.fsamp = fsamp ?? null;
}

/**
 * @param {State} state
 * @param {JsonObject | null | undefined} parameters
 */
export function setEditParameters(state, parameters) {
  state.edit.parameters = parameters || {};
}

/**
 * @param {State} state
 * @param {number | null | undefined} totalSamples
 */
export function setEditTotalSamples(state, totalSamples) {
  state.edit.totalSamples = Number(totalSamples) || 0;
}

/**
 * @param {State} state
 * @param {boolean[] | null | undefined} flagged
 */
export function setEditFlaggedArray(state, flagged) {
  state.edit.flagged = Array.isArray(flagged) ? flagged : [];
}

/**
 * @param {State} state
 * @param {string[] | null | undefined} uids
 */
export function setEditMuUids(state, uids) {
  state.edit.muUids = Array.isArray(uids) ? uids : [];
}

/**
 * @param {State} state
 * @param {EditHistoryEntry[] | null | undefined} history
 */
export function setEditHistory(state, history) {
  state.edit.editHistory = Array.isArray(history) ? history : [];
}

/**
 * @param {State} state
 * @param {number[][] | null | undefined} artifactTimes
 */
export function setEditArtifactTimes(state, artifactTimes) {
  state.edit.artifactTimes = Array.isArray(artifactTimes) ? artifactTimes : [];
}

/**
 * @param {State} state
 * @param {EditHistoryEntry} entry
 */
export function appendEditHistoryEntry(state, entry) {
  if (!Array.isArray(state.edit.editHistory)) state.edit.editHistory = [];
  state.edit.editHistory.push(entry);
}

/**
 * @param {State} state
 * @param {string} muUid
 */
export function popLastEditHistoryEntryForMu(state, muUid) {
  if (!Array.isArray(state.edit.editHistory)) return;
  const idx = state.edit.editHistory.findLastIndex((e) => e.mu_uid === muUid);
  if (idx !== -1) state.edit.editHistory.splice(idx, 1);
}

/**
 * @param {State} state
 * @param {string} muUid
 */
export function clearEditHistoryForMu(state, muUid) {
  if (!Array.isArray(state.edit.editHistory)) return;
  state.edit.editHistory = state.edit.editHistory.filter(
    (e) => e.mu_uid !== muUid,
  );
}

/**
 * @param {State} state
 * @param {EditBackup | null} backup
 */
export function setEditBackup(state, backup) {
  state.edit.backup = backup || null;
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
 * @param {boolean} dirty
 */
export function setEditDirty(state, dirty) {
  state.edit.dirty = !!dirty;
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
  if (!Array.isArray(state.rois)) state.rois = [];
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
 * @param {Span[] | null | undefined} regions
 */
export function setArtifactRegions(state, regions) {
  state.artifactRegions = Array.isArray(regions) ? regions : [];
}

/**
 * @param {State} state
 * @param {Partial<Span> | null | undefined} region
 */
export function addArtifactRegion(state, region) {
  if (!Array.isArray(state.artifactRegions)) state.artifactRegions = [];
  const start = Number(region?.start);
  const end = Number(region?.end);
  if (!Number.isFinite(start) || !Number.isFinite(end)) return;
  state.artifactRegions.push({ start, end });
}

/**
 * @param {State} state
 */
export function removeLastArtifactRegion(state) {
  if (!Array.isArray(state.artifactRegions) || !state.artifactRegions.length) {
    return false;
  }
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
  if (!Array.isArray(state.discardMasks)) state.discardMasks = [];
  if (!Array.isArray(state.discardMasks[gridIdx])) {
    state.discardMasks[gridIdx] = [];
  }
  state.discardMasks[gridIdx][chIdx] = value ? 1 : 0;
}

/**
 * @param {State} state
 * @param {{ distimes?: number[], pulseTrain?: number[], gridIdx: number, uid: string }} mu
 */
export function appendEditMu(state, { distimes, pulseTrain, gridIdx, uid }) {
  state.edit.distimes.push([...(distimes || [])]);
  state.edit.pulseTrains.push([...(pulseTrain || [])]);
  if (!state.edit.originalDistimes) state.edit.originalDistimes = [];
  state.edit.originalDistimes.push([...(distimes || [])]);
  if (!state.edit.originalPulseTrains) state.edit.originalPulseTrains = [];
  state.edit.originalPulseTrains.push([...(pulseTrain || [])]);
  state.edit.muGridIndex.push(gridIdx);
  if (!Array.isArray(state.edit.flagged)) state.edit.flagged = [];
  state.edit.flagged.push(false);
  if (!Array.isArray(state.edit.muUids)) state.edit.muUids = [];
  state.edit.muUids.push(uid);
  if (!state.edit.artifactTimes) state.edit.artifactTimes = [];
  state.edit.artifactTimes.push([]);
}

/**
 * @param {State} state
 * @param {string | null | undefined} versions
 */
export function setEditSoftwareVersions(state, versions) {
  state.edit.softwareVersions = versions ?? null;
}
