import { createEditSlice } from "../app/state.js";

export function setCurrentGrid(state, idx) {
  state.currentGrid = Math.max(0, idx || 0);
}

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

export function setEditProject(state, value) {
  state.edit.project = String(value || "").trim();
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

export function setEditArtifactTimesForMu(state, muIdx, times) {
  if (!state.edit.artifactTimes) state.edit.artifactTimes = [];
  const clean = (times || [])
    .map((v) => Number(v))
    .filter((v) => Number.isFinite(v));
  state.edit.artifactTimes[muIdx] = clean;
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

export function setEditArtifactTimes(state, artifactTimes) {
  state.edit.artifactTimes = Array.isArray(artifactTimes) ? artifactTimes : [];
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
  state.edit.editHistory = state.edit.editHistory.filter(
    (e) => e.mu_uid !== muUid,
  );
}

export function setEditBackup(state, backup) {
  state.edit.backup = backup || null;
}

export function setEditBookmark(state, position) {
  state.edit.bookmarkPosition = position || null;
}

export function setShowBookmark(state, show) {
  state.edit.showBookmark = !!show;
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
  state.edit = createEditSlice();
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

export function setFsamp(state, fsamp) {
  const n = Number(fsamp);
  state.fsamp = Number.isFinite(n) && n > 0 ? n : null;
}

export function setRoiDraft(state, draft) {
  state.roiDraft = draft || null;
}

export function setRoiForIndex(state, idx, roi) {
  if (!Array.isArray(state.rois)) state.rois = [];
  if (idx < 0) return;
  while (state.rois.length <= idx) {
    state.rois.push({ start: 0, end: 0 });
  }
  state.rois[idx] = { start: roi.start, end: roi.end };
}

export function setArtifactMode(state, on) {
  state.artifactMode = !!on;
}

export function setArtifactDraft(state, draft) {
  state.artifactDraft = draft || null;
}

export function setArtifactRegions(state, regions) {
  state.artifactRegions = Array.isArray(regions)
    ? regions
        .map((r) => ({
          start: Number(r?.start ?? r?.[0]),
          end: Number(r?.end ?? r?.[1]),
        }))
        .filter((r) => Number.isFinite(r.start) && Number.isFinite(r.end))
    : [];
}

export function addArtifactRegion(state, region) {
  if (!Array.isArray(state.artifactRegions)) state.artifactRegions = [];
  const start = Number(region?.start);
  const end = Number(region?.end);
  if (!Number.isFinite(start) || !Number.isFinite(end)) return;
  state.artifactRegions.push({ start, end });
}

export function removeLastArtifactRegion(state) {
  if (!Array.isArray(state.artifactRegions) || !state.artifactRegions.length) {
    return false;
  }
  state.artifactRegions.pop();
  return true;
}

export function setDiscardMasks(state, masks) {
  if (!Array.isArray(masks)) return;
  state.discardMasks = masks.map((grid) =>
    Array.isArray(grid) ? grid.map((v) => (v ? 1 : 0)) : [],
  );
}

export function setDiscardMaskChannel(state, gridIdx, chIdx, value) {
  if (!Array.isArray(state.discardMasks)) state.discardMasks = [];
  if (!Array.isArray(state.discardMasks[gridIdx])) {
    state.discardMasks[gridIdx] = [];
  }
  state.discardMasks[gridIdx][chIdx] = value ? 1 : 0;
}

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

export function setEditSoftwareVersions(state, versions) {
  state.edit.softwareVersions = versions ?? null;
}
