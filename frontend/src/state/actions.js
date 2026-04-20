export function setCurrentGrid(state, idx) {
  state.currentGrid = Math.max(0, idx || 0);
}

export function ensureDiscardMasks(state) {
  if (!state.channelMeans || !state.channelMeans.length) return;
  if (
    !state.discardMasks ||
    state.discardMasks.length !== state.channelMeans.length
  ) {
    state.discardMasks = state.channelMeans.map((cm) => cm.map(() => 0));
  }
}

export function setCurrentStage(state, stage) {
  state.currentStage = stage;
}

export function setEditMode(state, mode) {
  state.edit.mode = mode;
}

export function setEditCurrentMuGrid(state, idx, { resetView = true } = {}) {
  state.edit.currentMuGrid = Math.max(0, idx || 0);
  if (resetView) state.edit.view = null;
}

export function setEditCurrentMu(state, idx, { resetView = true } = {}) {
  state.edit.currentMu = Number.isNaN(idx) ? 0 : idx;
  if (resetView) state.edit.view = null;
}

export function setRunCurrentMuGrid(state, idx, { resetView = true } = {}) {
  state.currentMuGrid = Math.max(0, idx || 0);
  if (resetView) state.runView = null;
}

export function setRunCurrentMu(state, idx, { resetView = true } = {}) {
  state.currentMu = Number.isNaN(idx) ? 0 : idx;
  if (resetView) state.runView = null;
}

export function setEditBidsRoot(state, value) {
  state.edit.bidsRoot = String(value || "").trim();
}

export function setEditSignalToken(state, value) {
  state.edit.editSignalToken = String(value || "").trim();
}

export function setMuscle(state, muscle) {
  state.muscle = Array.isArray(muscle) ? muscle : [];
}

export function setEditView(state, view) {
  state.edit.view = view;
}

export function setRunView(state, view) {
  state.runView = view;
}

export function setFile(state, file) {
  state.file = file || null;
}

export function setUploadToken(state, token) {
  state.uploadToken = token || null;
}

export function setSeriesLength(state, totalSamples) {
  state.seriesLength = totalSamples ?? null;
}

export function setRois(state, rois) {
  state.rois = Array.isArray(rois) ? rois : [];
}

export function setPreviewSeries(state, series) {
  state.previewSeries = Array.isArray(series) ? series : [];
}

export function setGridSeries(state, series) {
  state.gridSeries = Array.isArray(series) ? series : [];
}

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

export function setChannelMeans(state, means) {
  state.channelMeans = Array.isArray(means) ? means : [];
}

export function setCoordinates(state, coordinates) {
  state.coordinates = Array.isArray(coordinates) ? coordinates : [];
}

export function setChannelTraces(state, traces) {
  state.channelTraces = Array.isArray(traces) ? traces : [];
}

export function setChannelTraceForGrid(state, gridIdx, trace) {
  if (!Array.isArray(state.channelTraces)) {
    state.channelTraces = [];
  }
  state.channelTraces[gridIdx] = trace;
}

export function setQcWindowLoading(state, loadingMap) {
  state.qcWindowLoading =
    loadingMap && typeof loadingMap === "object" ? loadingMap : {};
}

export function setQcWindowLoadingForGrid(state, gridIdx, isLoading) {
  if (!state.qcWindowLoading || typeof state.qcWindowLoading !== "object") {
    state.qcWindowLoading = {};
  }
  state.qcWindowLoading[gridIdx] = !!isLoading;
}

export function setMetadata(state, metadata) {
  state.metadata = metadata && typeof metadata === "object" ? metadata : {};
}

export function setAuxData(state, auxiliary, auxiliaryNames) {
  state.auxSeries = Array.isArray(auxiliary) ? auxiliary : [];
  state.auxNames = Array.isArray(auxiliaryNames) ? auxiliaryNames : [];
}

export function setMuPreviewData(state, pulseTrains, distimes, gridIndex) {
  state.muPulseTrains = Array.isArray(pulseTrains) ? pulseTrains : [];
  state.muDistimes = Array.isArray(distimes) ? distimes : [];
  state.muGridIndex = Array.isArray(gridIndex) ? gridIndex : [];
}

export function setParameters(state, parameters) {
  state.parameters = parameters || null;
}

export function clearPreviewState(state) {
  state.previewSeries = [];
  state.gridSeries = [];
  state.gridNames = [];
  state.channelMeans = [];
  state.channelTraces = [];
  state.seriesLength = null;
}

export function setEditDistimesForMu(state, muIdx, distimes) {
  const clean = (distimes || [])
    .map((v) => Number(v))
    .filter((v) => Number.isFinite(v));
  state.edit.distimes[muIdx] = clean;
}

export function setEditPulseTrainForMu(state, muIdx, pulseTrain) {
  const clean = Array.isArray(pulseTrain)
    ? pulseTrain.map((v) => Number(v))
    : [];
  state.edit.pulseTrains[muIdx] = clean;
}

export function setEditFlagForMu(state, muIdx, flagged) {
  state.edit.flagged[muIdx] = !!flagged;
}

export function clearEditPulseSelections(state) {
  state.edit.selectionPulse = null;
  state.edit.draftSelectionPulse = null;
}

export function clearEditDrSelections(state) {
  state.edit.selectionDr = null;
  state.edit.draftSelectionDr = null;
}

export function clearAllEditSelections(state) {
  clearEditPulseSelections(state);
  clearEditDrSelections(state);
}

export function setEditFile(state, file) {
  state.edit.file = file || null;
}

export function setEditFilename(state, filename) {
  state.edit.filename = String(filename || "");
}

export function setEditPulseTrains(state, pulseTrains) {
  state.edit.pulseTrains = Array.isArray(pulseTrains) ? pulseTrains : [];
}

export function setEditOriginalPulseTrains(state, pulseTrains) {
  state.edit.originalPulseTrains = Array.isArray(pulseTrains)
    ? pulseTrains
    : [];
}

export function setEditDistimes(state, distimes) {
  state.edit.distimes = Array.isArray(distimes) ? distimes : [];
}

export function setEditOriginalDistimes(state, distimes) {
  state.edit.originalDistimes = Array.isArray(distimes) ? distimes : [];
}

export function setEditGridNames(state, gridNames) {
  state.edit.gridNames = Array.isArray(gridNames) ? gridNames : [];
}

export function setEditMuGridIndex(state, muGridIndex) {
  state.edit.muGridIndex = Array.isArray(muGridIndex) ? muGridIndex : [];
}

export function setEditFsamp(state, fsamp) {
  state.edit.fsamp = fsamp ?? null;
}

export function setEditParameters(state, parameters) {
  state.edit.parameters = parameters || {};
}

export function setEditTotalSamples(state, totalSamples) {
  state.edit.totalSamples = Number(totalSamples) || 0;
}

export function setEditFlaggedArray(state, flagged) {
  state.edit.flagged = Array.isArray(flagged) ? flagged : [];
}

export function setEditMuUids(state, uids) {
  state.edit.muUids = Array.isArray(uids) ? uids : [];
}

export function setEditHistory(state, history) {
  state.edit.editHistory = Array.isArray(history) ? history : [];
}

export function appendEditHistoryEntry(state, entry) {
  if (!Array.isArray(state.edit.editHistory)) state.edit.editHistory = [];
  state.edit.editHistory.push(entry);
}

export function popLastEditHistoryEntryForMu(state, muUid) {
  if (!Array.isArray(state.edit.editHistory)) return;
  const idx = state.edit.editHistory.findLastIndex((e) => e.mu_uid === muUid);
  if (idx !== -1) state.edit.editHistory.splice(idx, 1);
}

export function clearEditHistoryForMu(state, muUid) {
  if (!Array.isArray(state.edit.editHistory)) return;
  state.edit.editHistory = state.edit.editHistory.filter((e) => e.mu_uid !== muUid);
}

export function setEditBackup(state, backup) {
  state.edit.backup = backup || null;
}

export function setEditDirty(state, dirty) {
  state.edit.dirty = !!dirty;
}

export function setEditPulseSelection(state, selection) {
  state.edit.selectionPulse = selection || null;
}

export function setEditPulseDraftSelection(state, selection) {
  state.edit.draftSelectionPulse = selection || null;
}

export function setEditDrSelection(state, selection) {
  state.edit.selectionDr = selection || null;
}

export function setEditDrDraftSelection(state, selection) {
  state.edit.draftSelectionDr = selection || null;
}

export function resetEditSlice(state) {
  state.edit = {
    file: null,
    filename: "",
    pulseTrains: [],
    originalPulseTrains: [],
    distimes: [],
    originalDistimes: [],
    gridNames: [],
    muGridIndex: [],
    fsamp: null,
    totalSamples: 0,
    currentMuGrid: 0,
    currentMu: 0,
    view: null,
    selectionPulse: null,
    selectionDr: null,
    draftSelectionPulse: null,
    draftSelectionDr: null,
    mode: null,
    dirty: false,
    parameters: null,
    flagged: [],
    backup: null,
    bidsRoot: "",
    editSignalToken: "",
    muUids: [],
    editHistory: [],
  };
}

export function setRunDownloadInFlight(state, inFlight) {
  state.runDownloadInFlight = !!inFlight;
}

export function setLastRunDownloadKey(state, key) {
  state.lastRunDownloadKey = String(key || "");
}

export function setIsRunning(state, isRunning) {
  state.isRunning = !!isRunning;
}
