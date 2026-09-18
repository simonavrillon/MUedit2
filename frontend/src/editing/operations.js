import {
  clearEditHistoryForMu,
  clearAllEditSelections,
  resetEditSlice,
  appendEditMu,
  setEditBackup,
  setEditCurrentMu,
  setEditCurrentMuGrid,
  setEditDirty,
  setEditArtifactTimesForMu,
  setEditDistimesForMu,
  setEditFlagForMu,
  setEditFlaggedArray,
  setEditPulseTrainForMu,
  setEditTotalSamples,
  popLastEditHistoryEntryForMu,
} from "../state/actions.js";
import { muUidFor } from "../state/selectors.js";

/** @typedef {import("../app/context.js").App} App */

// --- State helpers ---

export function ensureEditFlagged(state) {
  const total = state.edit.distimes?.length || 0;
  if (!state.edit.flagged || state.edit.flagged.length !== total) {
    setEditFlaggedArray(state, new Array(total).fill(false));
  }
}

export function getRawPulse(state, muIdx) {
  return state.edit.pulseTrains?.[muIdx] || [];
}

export function getDisplayPulse(state, muIdx) {
  const pulse = getRawPulse(state, muIdx);
  ensureEditFlagged(state);
  if (state.edit.flagged?.[muIdx]) {
    return new Array(pulse.length || 0).fill(0);
  }
  return pulse;
}

export function backupEditMu(state) {
  const muIdx = state.edit.currentMu ?? 0;
  ensureEditFlagged(state);
  setEditBackup(state, {
    muIdx,
    distimes: [...(state.edit.distimes?.[muIdx] || [])],
    flagged: !!state.edit.flagged?.[muIdx],
    pulseTrain: state.edit.pulseTrains?.[muIdx]
      ? [...state.edit.pulseTrains[muIdx]]
      : null,
    artifactTimes: [...(state.edit.artifactTimes?.[muIdx] || [])],
  });
}

/** @param {App} app */
export function restoreEditBackup(app) {
  const {
    state,
    setEditStatus,
    renderEditExplorer,
    recomputeEditDirty,
    ensureEditFlagged,
  } = app;

  const backup = state.edit.backup;
  if (!backup) {
    setEditStatus("Nothing to undo", "muted");
    return;
  }
  const { muIdx, distimes, flagged } = backup;
  if (!state.edit.distimes?.length) return;
  setEditDistimesForMu(state, muIdx, distimes);
  setEditArtifactTimesForMu(state, muIdx, backup.artifactTimes || []);
  ensureEditFlagged();
  setEditFlagForMu(state, muIdx, flagged);
  if (backup.pulseTrain) {
    setEditPulseTrainForMu(state, muIdx, backup.pulseTrain);
  }
  const muUid = muUidFor(state, muIdx);
  popLastEditHistoryEntryForMu(state, muUid);
  setEditBackup(state, null);
  clearAllEditSelections(state);
  recomputeEditDirty();
  renderEditExplorer();
  setEditStatus("Undo applied", "success");
}

export function recomputeEditDirty(state) {
  const current = state.edit.distimes || [];
  const baseline = state.edit.originalDistimes || [];
  setEditDirty(
    state,
    current.some((vals, idx) => {
      const base = baseline[idx] || [];
      return JSON.stringify(vals || []) !== JSON.stringify(base || []);
    }),
  );
}

export function getEditTotalSamples(state) {
  const pulse = state.edit.pulseTrains?.[0] || [];
  return state.edit.totalSamples || pulse.length || 0;
}

export function getPulseViewMeta(state) {
  const pulse = state.edit.pulseTrains?.[state.edit.currentMu] || [];
  const viewStart = state.edit.view?.start ?? 0;
  const viewEnd = state.edit.view?.end ?? pulse.length;
  const s = Math.max(0, Math.min(pulse.length, viewStart));
  const e = Math.max(s + 1, Math.min(pulse.length, viewEnd));
  const slice = pulse.slice(s, e);
  const minVal = slice.length ? Math.min(...slice) : 0;
  const maxVal = slice.length ? Math.max(...slice) : 1;
  const span = maxVal - minVal || 1;
  return { s, e, minVal, maxVal, span, slice };
}

export function refreshEditTotals(state) {
  setEditTotalSamples(state, getEditTotalSamples(state));
}

// Compute the dropdown model: which grid to select, which MU list to show,
// and whether the current grid/MU needs switching. Pure logic — no DOM,
// no state mutation. The caller applies mutations and renders.
export function buildEditDropdownModel(state, getEditMuIndices) {
  const gridNames = state.edit.gridNames || [];
  let targetGrid = state.edit.currentMuGrid || 0;
  let mus = getEditMuIndices(targetGrid);
  if (!mus.length && gridNames.length) {
    for (let g = 0; g < gridNames.length; g++) {
      const list = getEditMuIndices(g);
      if (list.length) {
        targetGrid = g;
        mus = list;
        break;
      }
    }
  }
  const currentMu = state.edit.currentMu;
  const needsMuSwitch = mus.length && !mus.includes(currentMu);
  return {
    gridNames,
    targetGrid,
    muOptions: mus,
    currentMu: needsMuSwitch ? mus[0] : currentMu,
    needsGridSwitch: targetGrid !== (state.edit.currentMuGrid || 0),
    needsMuSwitch,
  };
}

/** @param {App} app */
export function resetEditState(app) {
  const { state, refreshEditModeButtons } = app;
  resetEditSlice(state);
  refreshEditModeButtons();
}

// --- Selection coordinators (bridge canvas coordinates → API actions) ---

/** @param {App} app */
export function addSpikesInSelection(app, sel) {
  const {
    state,
    getRawPulse,
    backupEditMu,
    getPulseViewMeta,
    getPulsePlotHeight,
    requestRoiEdit,
  } = app;

  const muIdx = state.edit.currentMu ?? 0;
  const pulse = getRawPulse(muIdx);
  if (!pulse.length) return;
  backupEditMu();
  const { s, e, minVal, span } = getPulseViewMeta();
  const start = Math.max(s, Math.min(e, sel.start ?? sel[0]));
  const end = Math.max(start + 1, Math.min(e, sel.end ?? sel[1]));
  const height = getPulsePlotHeight();
  const y1 = Math.max(0, Math.min(height, sel.yMin ?? 0));
  const y2 = Math.max(0, Math.min(height, sel.yMax ?? height));
  const yLowPx = Math.max(y1, y2);
  const minHeight = minVal + (1 - yLowPx / height) * span;

  requestRoiEdit("add-spikes", {
    muIdx,
    pulse,
    xStart: start,
    xEnd: end,
    yMin: minHeight,
    fs: state.edit.fsamp || 0,
  });
}

/** @param {App} app */
export function addArtifactInSelection(app, sel) {
  const {
    state,
    getRawPulse,
    backupEditMu,
    getPulseViewMeta,
    getPulsePlotHeight,
    requestRoiEdit,
  } = app;

  const muIdx = state.edit.currentMu ?? 0;
  const pulse = getRawPulse(muIdx);
  if (!pulse.length) return;
  backupEditMu();
  const { s, e, minVal, span } = getPulseViewMeta();
  const start = Math.max(s, Math.min(e, sel.start ?? sel[0]));
  const end = Math.max(start + 1, Math.min(e, sel.end ?? sel[1]));
  const height = getPulsePlotHeight();
  const y1 = Math.max(0, Math.min(height, sel.yMin ?? 0));
  const y2 = Math.max(0, Math.min(height, sel.yMax ?? height));
  const yLowPx = Math.max(y1, y2);
  const minHeight = minVal + (1 - yLowPx / height) * span;

  requestRoiEdit("add-artifact", {
    muIdx,
    pulse,
    xStart: start,
    xEnd: end,
    yMin: minHeight,
    fs: state.edit.fsamp || 0,
  });
}

/** @param {App} app */
export function deleteSpikesInSelection(app, sel) {
  const {
    state,
    getRawPulse,
    backupEditMu,
    getPulseViewMeta,
    getPulsePlotHeight,
    requestRoiEdit,
  } = app;

  const muIdx = state.edit.currentMu ?? 0;
  const pulse = getRawPulse(muIdx);
  if (!pulse.length) return;
  backupEditMu();
  const { s, e, minVal, span } = getPulseViewMeta();
  const start = Math.max(s, Math.min(e, sel.start ?? sel[0]));
  const end = Math.max(start + 1, Math.min(e, sel.end ?? sel[1]));
  const height = getPulsePlotHeight();
  const y1 = Math.max(0, Math.min(height, sel.yMin ?? 0));
  const y2 = Math.max(0, Math.min(height, sel.yMax ?? height));
  const yVal1 = minVal + (1 - y1 / height) * span;
  const yVal2 = minVal + (1 - y2 / height) * span;
  const low = Math.min(yVal1, yVal2);
  const high = Math.max(yVal1, yVal2);

  requestRoiEdit("delete-spikes", {
    muIdx,
    pulse,
    xStart: start,
    xEnd: end,
    yMin: low,
    yMax: high,
    artifact_times: state.edit.artifactTimes?.[muIdx] || [],
  });
}

/** @param {App} app */
export function deleteDrInSelection(app, sel) {
  const { state, backupEditMu, getDrPlotHeight, getRawPulse, requestRoiEdit } =
    app;

  const muIdx = state.edit.currentMu ?? 0;
  const spikes = state.edit.distimes?.[muIdx] || [];
  if (spikes.length < 2) return;
  backupEditMu();
  const height = getDrPlotHeight();
  const yMinPx = Math.min(sel.yMin ?? 0, sel.yMax ?? height);
  const yMaxPx = Math.max(sel.yMin ?? 0, sel.yMax ?? height);
  const fs = state.edit.fsamp || 2000;
  const pulse = getRawPulse(muIdx);
  let maxDr = 0;
  for (let i = 0; i < spikes.length - 1; i++) {
    const isi = spikes[i + 1] - spikes[i];
    if (isi <= 0) continue;
    const dr = fs / isi;
    if (dr > maxDr) maxDr = dr;
  }
  const span = maxDr || 1;

  const yMin = 0 + (1 - Math.max(yMinPx, yMaxPx) / height) * span;
  requestRoiEdit("delete-dr", {
    muIdx,
    pulse,
    xStart: Math.min(sel.start ?? sel[0], sel.end ?? sel[1]),
    xEnd: Math.max(sel.start ?? sel[0], sel.end ?? sel[1]),
    yMin,
    fs,
  });
}

// --- MU mutations ---

// Compute instantaneous discharge rate series from spike times. Pure
// signal-processing — no DOM, no state. Extracted from edit-canvas.js so
// the view layer only renders, never computes domain data.
export function computeInstantaneousDr(spikes, fsamp, totalSamples) {
  const series = new Array(totalSamples).fill(0);
  const markers = [];
  const markerVals = [];
  for (let i = 0; i < spikes.length - 1; i++) {
    const isi = spikes[i + 1] - spikes[i];
    if (isi <= 0) continue;
    const dr = fsamp ? fsamp / isi : 0;
    const mid = Math.min(
      totalSamples - 1,
      Math.max(0, Math.round(spikes[i] + isi / 2)),
    );
    series[mid] = dr;
    markers.push(mid);
    markerVals.push(dr);
  }
  return { series, markers, markerVals };
}

/** @param {App} app */
export function duplicateMu(app) {
  const {
    state,
    setEditStatus,
    ensureEditFlagged,
    recomputeEditDirty,
    renderEditExplorer,
  } = app;

  const muIdx = state.edit.currentMu ?? 0;
  const pulse = state.edit.pulseTrains?.[muIdx];
  const distimes = state.edit.distimes?.[muIdx];
  if (!pulse || !pulse.length) {
    setEditStatus("No MU loaded", "muted");
    return;
  }

  const gridIdx = state.edit.muGridIndex?.[muIdx] ?? 0;

  const existingUids = state.edit.muUids || [];
  const prefix = `g${gridIdx}_mu`;
  const existingCounts = existingUids
    .filter((uid) => uid.startsWith(prefix))
    .map((uid) => parseInt(uid.slice(prefix.length), 10))
    .filter((n) => Number.isFinite(n));
  const newCount =
    existingCounts.length > 0 ? Math.max(...existingCounts) + 1 : 0;
  const newUid = `${prefix}${newCount}`;

  const newIdx = state.edit.distimes.length;
  appendEditMu(state, { distimes, pulseTrain: pulse, gridIdx, uid: newUid });
  ensureEditFlagged();

  const sourceUid = muUidFor(state, muIdx);
  app.appendEditHistory({
    type: "duplicate_mu",
    mu_uid: newUid,
    source_mu_uid: sourceUid,
  });

  setEditCurrentMuGrid(state, gridIdx, { resetView: false });
  setEditCurrentMu(state, newIdx, { resetView: false });
  recomputeEditDirty();
  renderEditExplorer();
  setEditStatus(`MU duplicated — now editing MU ${newIdx + 1}`, "success");
}

/** @param {App} app */
export function resetCurrentMuEdits(app) {
  const { state, ensureEditFlagged, recomputeEditDirty, renderEditExplorer } =
    app;

  const muIdx = state.edit.currentMu ?? 0;
  const baseline = state.edit.originalDistimes?.[muIdx];
  if (!baseline) return;
  setEditDistimesForMu(state, muIdx, baseline);
  setEditArtifactTimesForMu(state, muIdx, []);
  if (state.edit.originalPulseTrains?.[muIdx]) {
    setEditPulseTrainForMu(state, muIdx, state.edit.originalPulseTrains[muIdx]);
  }
  ensureEditFlagged();
  setEditFlagForMu(state, muIdx, false);
  const muUid = muUidFor(state, muIdx);
  clearEditHistoryForMu(state, muUid);
  setEditBackup(state, null);
  clearAllEditSelections(state);
  recomputeEditDirty();
  renderEditExplorer();
}
