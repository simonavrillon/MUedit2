import {
  appendEditHistoryEntry,
  clearEditHistoryForMu,
  clearAllEditSelections,
  clearEditDrSelections,
  clearEditPulseSelections,
  resetEditSlice,
  setEditBackup,
  setEditBidsRoot,
  setEditCurrentMu,
  setEditCurrentMuGrid,
  setEditDirty,
  setEditDistimes,
  setEditDistimesForMu,
  setEditDrDraftSelection,
  setEditDrSelection,
  setEditFile,
  setEditFilename,
  setEditFlagForMu,
  setEditFlaggedArray,
  setEditFsamp,
  setEditGridNames,
  setEditHistory,
  setEditMuGridIndex,
  setEditMuUids,
  popLastEditHistoryEntryForMu,
  setEditOriginalDistimes,
  setEditOriginalPulseTrains,
  setEditParameters,
  setEditPulseTrainForMu,
  setEditPulseTrains,
  setEditPulseDraftSelection,
  setEditPulseSelection,
  setEditSignalToken,
  setEditTotalSamples,
  setEditView,
  setGridNames,
  setMuscle,
} from "../state/actions.js";
import { COLORS, UNIFORM_PULSE_COLOR } from "../config.js";
import { decodeEditLoadPayload } from "../api/binary_payloads.js";
import { normalizeEditLoadPayload } from "../contracts/payloads.js";
import { inferGridCount, normalizeGridNames } from "./grid_names.js";

// --- Private helpers ---

function generateMuUids(muGridIndex) {
  const counts = {};
  return (muGridIndex || []).map((gridIdx) => {
    const count = counts[gridIdx] || 0;
    counts[gridIdx] = count + 1;
    return `g${gridIdx}_mu${count}`;
  });
}

function spikesDiff(before, after) {
  const afterSet = new Set(after);
  const beforeSet = new Set(before);
  return {
    added: after.filter((s) => !beforeSet.has(s)),
    removed: before.filter((s) => !afterSet.has(s)),
  };
}

function clampY(py, canvas, getCanvasPlotMetrics) {
  const metrics = getCanvasPlotMetrics(canvas, true);
  const clamped = Math.max(
    metrics.padding.top,
    Math.min(metrics.padding.top + metrics.plotHeight, py),
  );
  return clamped - metrics.padding.top;
}

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
  });
}

export function restoreEditBackup(deps) {
  const {
    state,
    setEditStatus,
    renderEditExplorer,
    recomputeEditDirty,
    ensureEditFlagged: ensureFlaggedFn,
  } = deps;

  const backup = state.edit.backup;
  if (!backup) {
    setEditStatus("Nothing to undo", "muted");
    return;
  }
  const { muIdx, distimes, flagged } = backup;
  if (!state.edit.distimes?.length) return;
  setEditDistimesForMu(state, muIdx, distimes);
  ensureFlaggedFn();
  setEditFlagForMu(state, muIdx, flagged);
  if (backup.pulseTrain) {
    setEditPulseTrainForMu(state, muIdx, backup.pulseTrain);
  }
  const muUid = state.edit.muUids?.[muIdx] ?? `mu${muIdx}`;
  popLastEditHistoryEntryForMu(state, muUid);
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

export function resetEditState(deps) {
  const { state, els, refreshEditModeButtons } = deps;
  resetEditSlice(state);
  if (els.editSaveBtn) els.editSaveBtn.disabled = true;
  if (els.editBidsRoot) els.editBidsRoot.value = "";
  refreshEditModeButtons();
}

// --- Rendering ---

export function renderEditExplorer(deps) {
  const {
    els,
    state,
    drawSeries,
    renderEditDropdowns,
    getDisplayPulse,
    renderInstantaneousDr,
  } = deps;

  renderEditDropdowns();
  const muIdx = state.edit.currentMu ?? 0;
  const pulse = getDisplayPulse(muIdx);
  const spikes = state.edit.distimes?.[muIdx] || [];
  if (!state.edit.view || (pulse && state.edit.view.end > pulse.length)) {
    setEditView(state, { start: 0, end: pulse.length || 0 });
  }
  const overlays = [];
  if (state.edit.selectionPulse) overlays.push(state.edit.selectionPulse);
  if (state.edit.draftSelectionPulse)
    overlays.push(state.edit.draftSelectionPulse);
  const markerVals = spikes.map((s) => pulse?.[s] ?? 0);
  const pulseCanvas = els?.editPulseCanvas || "editPulseCanvas";
  drawSeries(
    pulseCanvas,
    pulse,
    UNIFORM_PULSE_COLOR,
    spikes,
    overlays,
    pulse.length,
    state.edit.view,
    markerVals,
    true,
    {
      showAxes: true,
      hideYAxis: false,
      fsamp: state.edit.fsamp,
      markerColor: COLORS.muPurple,
    },
  );
  renderInstantaneousDr();
}

export function renderInstantaneousDr(deps) {
  const {
    state,
    els,
    COLORS,
    drawSeries,
    getEditTotalSamples,
    ensureEditFlagged,
  } = deps;

  const canvas = els?.editDrCanvas || "editDrCanvas";
  const pulse = state.edit.pulseTrains?.[state.edit.currentMu] || [];
  const spikes = state.edit.distimes?.[state.edit.currentMu] || [];
  ensureEditFlagged();
  if (state.edit.flagged?.[state.edit.currentMu]) {
    drawSeries(canvas, [], COLORS.warning);
    return;
  }
  const total = getEditTotalSamples();
  if (!pulse.length || !spikes.length) {
    drawSeries(canvas, [], COLORS.warning);
    return;
  }
  const series = new Array(total).fill(0);
  const markers = [];
  const markerVals = [];
  for (let i = 0; i < spikes.length - 1; i++) {
    const isi = spikes[i + 1] - spikes[i];
    if (isi <= 0) continue;
    const dr = state.edit.fsamp ? state.edit.fsamp / isi : 0;
    const mid = Math.min(
      total - 1,
      Math.max(0, Math.round(spikes[i] + isi / 2)),
    );
    series[mid] = dr;
    markers.push(mid);
    markerVals.push(dr);
  }
  const drSelection = state.edit.selectionDr || state.edit.draftSelectionDr;
  drawSeries(
    canvas,
    series,
    COLORS.warning,
    markers,
    drSelection ? [drSelection] : [],
    total,
    state.edit.view,
    markerVals,
    false,
    {
      showAxes: true,
      hideYAxis: false,
      fsamp: state.edit.fsamp,
      markerColor: COLORS.muPurple,
    },
  );
}

// --- Edit actions ---

export async function requestRoiEdit(deps, action, payload) {
  const {
    state,
    API_BASE,
    apiJson,
    setEditStatus,
    ensureEditFlagged,
    setEditMode,
    recomputeEditDirty,
    renderEditExplorer,
  } = deps;

  const distimesBefore = [...(state.edit.distimes?.[payload.muIdx] || [])];
  try {
    setEditStatus("Applying ROI...", "muted");
    const data = await apiJson(`${API_BASE}/edit/${action}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        distimes: state.edit.distimes,
        mu_index: payload.muIdx,
        pulse_train: payload.pulse,
        fsamp: payload.fs,
        x_start: payload.xStart,
        x_end: payload.xEnd,
        y_min: payload.yMin,
        y_max: payload.yMax,
      }),
    });
    setEditDistimesForMu(state, payload.muIdx, data.distimes || []);
    ensureEditFlagged();
    setEditFlagForMu(state, payload.muIdx, false);
    if (deps.appendEditHistory) {
      const muUid = state.edit.muUids?.[payload.muIdx] ?? `mu${payload.muIdx}`;
      const distimesAfter = state.edit.distimes?.[payload.muIdx] || [];
      const { added, removed } = spikesDiff(distimesBefore, distimesAfter);
      const typeMap = { "add-spikes": "add_spikes", "delete-spikes": "delete_spikes", "delete-dr": "delete_dr" };
      const entry = { type: typeMap[action] || action, mu_uid: muUid };
      if (added.length) entry.spikes_added = added;
      if (removed.length) entry.spikes_removed = removed;
      deps.appendEditHistory(entry);
    }
    if (action === "delete-dr") {
      clearEditDrSelections(state);
      setEditMode(null);
    } else {
      clearEditPulseSelections(state);
      setEditMode(null);
    }
    recomputeEditDirty();
    renderEditExplorer();
    setEditStatus("ROI applied", "success");
  } catch (err) {
    console.error(err);
    setEditStatus(`ROI failed: ${err.message}`, "error");
  }
}

export async function requestFilterUpdate(deps, mode) {
  const {
    state,
    els,
    API_BASE,
    apiJson,
    setEditStatus,
    getBidsRoot,
    getRawPulse,
    backupEditMu,
    buildEntityLabelFromSession,
    ensureEditFlagged,
    recomputeEditDirty,
    refreshEditTotals,
    renderEditExplorer,
  } = deps;

  const bidsRoot = state.edit.bidsRoot || getBidsRoot();
  if (!state.edit.distimes?.length) return;
  const muIdx = state.edit.currentMu ?? 0;
  const pulse = getRawPulse(muIdx);
  if (!pulse.length) {
    setEditStatus("No pulse train available", "muted");
    return;
  }
  const view = state.edit.view || { start: 0, end: pulse.length };
  const start = Math.max(0, view.start ?? 0);
  const end = Math.min(pulse.length, view.end ?? pulse.length);
  const gridIndex =
    state.edit.muGridIndex?.[muIdx] ?? state.edit.currentMuGrid ?? 0;

  const distimesBefore = [...(state.edit.distimes?.[muIdx] || [])];
  try {
    backupEditMu();
    setEditStatus("Updating filter from BIDS EMG...", "muted");
    const data = await apiJson(
      `${API_BASE}/edit/${mode}`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          bids_root: bidsRoot,
          edit_signal_token: state.edit.editSignalToken || "",
          file_label: state.edit.filename,
          grid_index: gridIndex,
          mu_index: muIdx,
          distimes: state.edit.distimes,
          mu_grid_index: state.edit.muGridIndex || [],
          pulse_train: pulse,
          view_start: start,
          view_end: end,
          use_peeloff: els.editPeelOffToggle?.dataset.state === "on",
        }),
      },
      120000,
    );
    setEditDistimesForMu(state, muIdx, data.distimes || []);
    if (data.pulse_train && Array.isArray(data.pulse_train)) {
      setEditPulseTrainForMu(state, muIdx, data.pulse_train);
    }
    ensureEditFlagged();
    setEditFlagForMu(state, muIdx, false);
    if (deps.appendEditHistory) {
      const muUid = state.edit.muUids?.[muIdx] ?? `mu${muIdx}`;
      const distimesAfter = state.edit.distimes?.[muIdx] || [];
      const { added, removed } = spikesDiff(distimesBefore, distimesAfter);
      const peeloff = els.editPeelOffToggle?.dataset.state === "on";
      const entry = { type: "update_filter", mu_uid: muUid, view_start: start, view_end: end, use_peeloff: peeloff };
      if (added.length) entry.spikes_added = added;
      if (removed.length) entry.spikes_removed = removed;
      deps.appendEditHistory(entry);
    }
    recomputeEditDirty();
    refreshEditTotals();
    renderEditExplorer();
    setEditStatus("MU filter updated", "success");
  } catch (err) {
    console.error(err);
    setEditStatus(`Filter update failed: ${err.message}`, "error");
  }
}

export function addSpikesInSelection(deps, sel) {
  const {
    state,
    els,
    getRawPulse,
    backupEditMu,
    getPulseViewMeta,
    getCanvasPlotMetrics,
    requestRoiEditFn,
  } = deps;

  const muIdx = state.edit.currentMu ?? 0;
  const pulse = getRawPulse(muIdx);
  if (!pulse.length) return;
  backupEditMu();
  const { s, e, minVal, span } = getPulseViewMeta();
  const start = Math.max(s, Math.min(e, sel.start ?? sel[0]));
  const end = Math.max(start + 1, Math.min(e, sel.end ?? sel[1]));
  const canvas = els.editPulseCanvas;
  const metrics = canvas
    ? getCanvasPlotMetrics(canvas, true)
    : { plotHeight: 1 };
  const height = metrics.plotHeight || 1;
  const y1 = Math.max(0, Math.min(height, sel.yMin ?? 0));
  const y2 = Math.max(0, Math.min(height, sel.yMax ?? height));
  const yLowPx = Math.max(y1, y2);
  const minHeight = minVal + (1 - yLowPx / height) * span;

  requestRoiEditFn("add-spikes", {
    muIdx,
    pulse,
    xStart: start,
    xEnd: end,
    yMin: minHeight,
    fs: state.edit.fsamp || 0,
  });
}

export function deleteSpikesInSelection(deps, sel) {
  const {
    state,
    els,
    getRawPulse,
    backupEditMu,
    getPulseViewMeta,
    getCanvasPlotMetrics,
    requestRoiEditFn,
  } = deps;

  const muIdx = state.edit.currentMu ?? 0;
  const pulse = getRawPulse(muIdx);
  if (!pulse.length) return;
  backupEditMu();
  const { s, e, minVal, span } = getPulseViewMeta();
  const start = Math.max(s, Math.min(e, sel.start ?? sel[0]));
  const end = Math.max(start + 1, Math.min(e, sel.end ?? sel[1]));
  const canvas = els.editPulseCanvas;
  const metrics = canvas
    ? getCanvasPlotMetrics(canvas, true)
    : { plotHeight: 1 };
  const height = metrics.plotHeight || 1;
  const y1 = Math.max(0, Math.min(height, sel.yMin ?? 0));
  const y2 = Math.max(0, Math.min(height, sel.yMax ?? height));
  const yVal1 = minVal + (1 - y1 / height) * span;
  const yVal2 = minVal + (1 - y2 / height) * span;
  const low = Math.min(yVal1, yVal2);
  const high = Math.max(yVal1, yVal2);

  requestRoiEditFn("delete-spikes", {
    muIdx,
    pulse,
    xStart: start,
    xEnd: end,
    yMin: low,
    yMax: high,
  });
}

export function deleteDrInSelection(deps, sel) {
  const {
    state,
    els,
    backupEditMu,
    getCanvasPlotMetrics,
    getRawPulse,
    requestRoiEditFn,
  } = deps;

  const muIdx = state.edit.currentMu ?? 0;
  const spikes = state.edit.distimes?.[muIdx] || [];
  if (spikes.length < 2) return;
  backupEditMu();
  const canvas = els.editDrCanvas;
  const metrics = canvas
    ? getCanvasPlotMetrics(canvas, true)
    : { plotHeight: 1 };
  const height = metrics.plotHeight || 1;
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
  requestRoiEditFn("delete-dr", {
    muIdx,
    pulse,
    xStart: Math.min(sel.start ?? sel[0], sel.end ?? sel[1]),
    xEnd: Math.max(sel.start ?? sel[0], sel.end ?? sel[1]),
    yMin,
    fs,
  });
}

export async function removeOutliers(deps) {
  const {
    state,
    API_BASE,
    apiJson,
    setEditStatus,
    getRawPulse,
    backupEditMu,
    ensureEditFlagged,
    recomputeEditDirty,
    renderEditExplorer,
  } = deps;

  const muIdx = state.edit.currentMu ?? 0;
  const spikes = state.edit.distimes?.[muIdx] || [];
  if (spikes.length < 3) {
    setEditStatus("Not enough spikes for outlier removal", "muted");
    return;
  }
  backupEditMu();
  const pulse = getRawPulse(muIdx);
  if (!pulse.length) {
    setEditStatus("No pulse train available", "muted");
    return;
  }
  try {
    setEditStatus("Removing outliers...", "muted");
    const data = await apiJson(`${API_BASE}/edit/remove-outliers`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        distimes: state.edit.distimes,
        mu_index: muIdx,
        pulse_train: pulse,
        fsamp: state.edit.fsamp || 0,
      }),
    });
    setEditDistimesForMu(state, muIdx, data.distimes || []);
    ensureEditFlagged();
    setEditFlagForMu(state, muIdx, false);
    if (deps.appendEditHistory && (data.removed_count || 0) > 0) {
      const muUid = state.edit.muUids?.[muIdx] ?? `mu${muIdx}`;
      const distimesAfter = state.edit.distimes?.[muIdx] || [];
      const { removed } = spikesDiff(spikes, distimesAfter);
      deps.appendEditHistory({ type: "remove_outliers", mu_uid: muUid, spikes_removed: removed });
    }
    recomputeEditDirty();
    renderEditExplorer();
    if ((data.removed_count || 0) > 0) {
      setEditStatus("Outliers removed", "success");
    } else {
      setEditStatus("No outliers detected", "muted");
    }
  } catch (err) {
    console.error(err);
    setEditStatus(`Outlier removal failed: ${err.message}`, "error");
  }
}

export async function removeDuplicateMus(deps) {
  const {
    state,
    API_BASE,
    apiJson,
    setEditStatus,
    ensureEditFlagged,
    recomputeEditDirty,
    renderEditExplorer,
  } = deps;

  const distimes = state.edit.distimes || [];
  if (distimes.length < 2) {
    setEditStatus("Need at least 2 MUs to deduplicate", "muted");
    return;
  }
  try {
    setEditStatus("Removing duplicates...", "muted");
    const data = await apiJson(`${API_BASE}/edit/remove-duplicates`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        distimes: state.edit.distimes,
        pulse_trains: state.edit.pulseTrains || [],
        fsamp: state.edit.fsamp,
        total_samples: state.edit.totalSamples || 0,
        mu_grid_index: state.edit.muGridIndex || [],
        parameters: state.edit.parameters || {},
      }),
    });

    const keptIdx = data.kept_indices || [];
    if (keptIdx.length === distimes.length) {
      setEditStatus("No duplicates found", "muted");
      return;
    }

    const keptSet = new Set(keptIdx);
    ensureEditFlagged();
    setEditDistimes(state, keptIdx.map((i) => data.distimes[keptIdx.indexOf(i)] || distimes[i]));
    setEditPulseTrains(state, keptIdx.map((i, pos) =>
      (data.pulse_trains && data.pulse_trains[pos]) ? data.pulse_trains[pos] : (state.edit.pulseTrains?.[i] || [])
    ));
    setEditOriginalDistimes(state, (state.edit.originalDistimes || []).filter((_, i) => keptSet.has(i)));
    setEditOriginalPulseTrains(state, (state.edit.originalPulseTrains || []).filter((_, i) => keptSet.has(i)));
    setEditMuGridIndex(state, (state.edit.muGridIndex || []).filter((_, i) => keptSet.has(i)));
    setEditFlaggedArray(state, (state.edit.flagged || []).filter((_, i) => keptSet.has(i)));
    setEditMuUids(state, (state.edit.muUids || []).filter((_, i) => keptSet.has(i)));

    const removedCount = data.removed_count || (distimes.length - keptIdx.length);
    if (deps.appendEditHistory) {
      const removedUids = (state.edit.muUids || []).filter((_, i) => !keptSet.has(i));
      deps.appendEditHistory({ type: "remove_duplicates", removed_count: removedCount, removed_mu_uids: removedUids });
    }

    // Keep current MU if it survived, otherwise fall back to first MU
    const currentMu = state.edit.currentMu ?? 0;
    const newCurrentMu = keptIdx.includes(currentMu)
      ? keptIdx.indexOf(currentMu)
      : 0;
    setEditCurrentMu(state, newCurrentMu, { resetView: false });

    recomputeEditDirty();
    renderEditExplorer();
    setEditStatus(`${removedCount} duplicate${removedCount !== 1 ? "s" : ""} removed`, "success");
  } catch (err) {
    console.error(err);
    setEditStatus(`Deduplication failed: ${err.message}`, "error");
  }
}

export function duplicateMu(deps) {
  const {
    state,
    setEditStatus,
    ensureEditFlagged,
    recomputeEditDirty,
    renderEditExplorer,
  } = deps;

  const muIdx = state.edit.currentMu ?? 0;
  const pulse = state.edit.pulseTrains?.[muIdx];
  const distimes = state.edit.distimes?.[muIdx];
  if (!pulse || !pulse.length) {
    setEditStatus("No MU loaded", "muted");
    return;
  }

  const gridIdx = state.edit.muGridIndex?.[muIdx] ?? 0;

  // Generate a UID for the new MU that doesn't conflict with existing ones
  const existingUids = state.edit.muUids || [];
  const prefix = `g${gridIdx}_mu`;
  const existingCounts = existingUids
    .filter((uid) => uid.startsWith(prefix))
    .map((uid) => parseInt(uid.slice(prefix.length), 10))
    .filter((n) => Number.isFinite(n));
  const newCount = existingCounts.length > 0 ? Math.max(...existingCounts) + 1 : 0;
  const newUid = `${prefix}${newCount}`;

  const newIdx = state.edit.distimes.length;
  state.edit.distimes.push([...(distimes || [])]);
  state.edit.pulseTrains.push([...(pulse || [])]);
  state.edit.muGridIndex.push(gridIdx);
  ensureEditFlagged();
  state.edit.flagged.push(false);
  state.edit.muUids.push(newUid);

  if (deps.appendEditHistory) {
    const sourceUid = state.edit.muUids?.[muIdx] ?? `mu${muIdx}`;
    deps.appendEditHistory({ type: "duplicate_mu", mu_uid: newUid, source_mu_uid: sourceUid });
  }

  setEditCurrentMuGrid(state, gridIdx, { resetView: false });
  setEditCurrentMu(state, newIdx, { resetView: false });
  recomputeEditDirty();
  renderEditExplorer();
  setEditStatus(`MU duplicated — now editing MU ${newIdx + 1}`, "success");
}

export async function flagMuForDeletion(deps) {
  const {
    state,
    API_BASE,
    apiJson,
    setEditStatus,
    getRawPulse,
    backupEditMu,
    ensureEditFlagged,
    recomputeEditDirty,
    renderEditExplorer,
  } = deps;

  const muIdx = state.edit.currentMu ?? 0;
  const pulse = getRawPulse(muIdx);
  if (!pulse.length) {
    setEditStatus("No MU loaded", "muted");
    return;
  }
  backupEditMu();
  try {
    setEditStatus("Flagging MU for deletion...", "muted");
    const data = await apiJson(`${API_BASE}/edit/flag-mu`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        distimes: state.edit.distimes,
        mu_index: muIdx,
      }),
    });
    if (Array.isArray(data.distimes)) {
      setEditDistimesForMu(state, muIdx, data.distimes);
    }
    ensureEditFlagged();
    setEditFlagForMu(state, muIdx, data.flagged !== false);
    if (deps.appendEditHistory) {
      const muUid = state.edit.muUids?.[muIdx] ?? `mu${muIdx}`;
      deps.appendEditHistory({ type: "flag_mu", mu_uid: muUid, flagged: data.flagged !== false });
    }
    recomputeEditDirty();
    renderEditExplorer();
    setEditStatus("MU flagged for deletion", "success");
  } catch (err) {
    console.error(err);
    setEditStatus(`Flagging failed: ${err.message}`, "error");
  }
}

export function resetCurrentMuEdits(deps) {
  const { state, ensureEditFlagged, recomputeEditDirty, renderEditExplorer } =
    deps;

  const muIdx = state.edit.currentMu ?? 0;
  const baseline = state.edit.originalDistimes?.[muIdx];
  if (!baseline) return;
  setEditDistimesForMu(state, muIdx, baseline);
  if (state.edit.originalPulseTrains?.[muIdx]) {
    setEditPulseTrainForMu(state, muIdx, state.edit.originalPulseTrains[muIdx]);
  }
  ensureEditFlagged();
  setEditFlagForMu(state, muIdx, false);
  const muUid = state.edit.muUids?.[muIdx] ?? `mu${muIdx}`;
  clearEditHistoryForMu(state, muUid);
  clearAllEditSelections(state);
  recomputeEditDirty();
  renderEditExplorer();
}

// --- Canvas interactions ---

export function bindEditCanvas(deps) {
  const {
    els,
    state,
    getRawPulse,
    getCanvasPlotMetrics,
    renderEditExplorer,
    setEditStatus,
    addSpikesInSelection,
    deleteSpikesInSelection,
    setEditMode,
  } = deps;

  const canvas = els.editPulseCanvas;
  if (!canvas) return;
  let dragging = false;
  let startPx = 0;
  let endPx = 0;
  let startPy = 0;
  let endPy = 0;

  const getPulse = () => getRawPulse(state.edit.currentMu ?? 0);

  const pxToSample = (px) => {
    const pulse = getPulse();
    const metrics = getCanvasPlotMetrics(canvas, true, { hideYAxis: false });
    const view = state.edit.view || { start: 0, end: pulse.length || 0 };
    const clamped = Math.max(
      metrics.padding.left,
      Math.min(metrics.padding.left + metrics.plotWidth, px),
    );
    const frac = metrics.plotWidth
      ? (clamped - metrics.padding.left) / metrics.plotWidth
      : 0;
    return Math.round(view.start + frac * Math.max(0, view.end - view.start));
  };

  canvas.addEventListener("mousedown", (e) => {
    if (!getPulse().length) return;
    dragging = true;
    const rect = canvas.getBoundingClientRect();
    startPx = e.clientX - rect.left;
    endPx = startPx;
    startPy = e.clientY - rect.top;
    endPy = startPy;
    setEditPulseDraftSelection(state, null);
  });

  canvas.addEventListener("mousemove", (e) => {
    if (!dragging) return;
    const rect = canvas.getBoundingClientRect();
    endPx = e.clientX - rect.left;
    endPy = e.clientY - rect.top;
    const startSample = pxToSample(Math.min(startPx, endPx));
    const endSample = pxToSample(Math.max(startPx, endPx));
    setEditPulseDraftSelection(state, {
      start: Math.max(0, startSample),
      end: Math.max(startSample + 1, endSample),
      yMin: clampY(Math.min(startPy, endPy), canvas, getCanvasPlotMetrics),
      yMax: clampY(Math.max(startPy, endPy), canvas, getCanvasPlotMetrics),
    });
    renderEditExplorer();
  });

  window.addEventListener("mouseup", () => {
    if (!dragging) return;
    dragging = false;
    const delta = Math.abs(endPx - startPx);
    const sel = {
      start: pxToSample(Math.min(startPx, endPx)),
      end: pxToSample(Math.max(startPx, endPx)),
      yMin: clampY(Math.min(startPy, endPy), canvas, getCanvasPlotMetrics),
      yMax: clampY(Math.max(startPy, endPy), canvas, getCanvasPlotMetrics),
    };
    if (delta < 6) {
      if (state.edit.mode === "add") {
        setEditStatus("Drag a box to add spikes", "muted");
        return;
      }
      if (state.edit.mode === "delete_spikes") {
        const windowSel = {
          ...sel,
          start: Math.max(0, sel.start - 2),
          end: sel.start + 2,
        };
        deleteSpikesInSelection(windowSel);
      }
      return;
    }

    if (state.edit.mode === "add") {
      addSpikesInSelection(sel);
      setEditMode(null);
      setEditPulseDraftSelection(state, null);
      return;
    }
    if (state.edit.mode === "delete_spikes") {
      deleteSpikesInSelection(sel);
      setEditMode(null);
      setEditPulseDraftSelection(state, null);
      return;
    }

    const startSample = pxToSample(Math.min(startPx, endPx));
    const endSample = pxToSample(Math.max(startPx, endPx));
    setEditPulseSelection(state, {
      start: Math.max(0, startSample),
      end: Math.max(startSample + 1, endSample),
      yMin: sel.yMin,
      yMax: sel.yMax,
    });
    setEditPulseDraftSelection(state, null);
    renderEditExplorer();
  });

  canvas.addEventListener("dblclick", () => {
    const pulse = getPulse();
    if (!pulse.length) return;
    setEditView(state, { start: 0, end: pulse.length });
    clearEditPulseSelections(state);
    renderEditExplorer();
  });
}

export function bindEditDrCanvas(deps) {
  const {
    els,
    state,
    getCanvasPlotMetrics,
    getEditTotalSamples,
    renderEditExplorer,
    deleteDrInSelection,
  } = deps;

  const canvas = els.editDrCanvas;
  if (!canvas) return;
  let dragging = false;
  let startPx = 0;
  let endPx = 0;
  let startPy = 0;
  let endPy = 0;

  const pxToSample = (px) => {
    const metrics = getCanvasPlotMetrics(canvas, true, { hideYAxis: false });
    const total = getEditTotalSamples();
    const view = state.edit.view || { start: 0, end: total };
    const clamped = Math.max(
      metrics.padding.left,
      Math.min(metrics.padding.left + metrics.plotWidth, px),
    );
    const frac = metrics.plotWidth
      ? (clamped - metrics.padding.left) / metrics.plotWidth
      : 0;
    return Math.round(view.start + frac * Math.max(0, view.end - view.start));
  };

  canvas.addEventListener("mousedown", (e) => {
    dragging = true;
    const rect = canvas.getBoundingClientRect();
    startPx = e.clientX - rect.left;
    endPx = startPx;
    startPy = e.clientY - rect.top;
    endPy = startPy;
    setEditDrDraftSelection(state, null);
  });

  canvas.addEventListener("mousemove", (e) => {
    if (!dragging) return;
    const rect = canvas.getBoundingClientRect();
    endPx = e.clientX - rect.left;
    endPy = e.clientY - rect.top;
    const startSample = pxToSample(Math.min(startPx, endPx));
    const endSample = pxToSample(Math.max(startPx, endPx));
    setEditDrDraftSelection(state, {
      start: Math.max(0, startSample),
      end: Math.max(startSample + 1, endSample),
      yMin: clampY(Math.min(startPy, endPy), canvas, getCanvasPlotMetrics),
      yMax: clampY(Math.max(startPy, endPy), canvas, getCanvasPlotMetrics),
    });
    renderEditExplorer();
  });

  window.addEventListener("mouseup", () => {
    if (!dragging) return;
    dragging = false;
    const sel = {
      start: pxToSample(Math.min(startPx, endPx)),
      end: pxToSample(Math.max(startPx, endPx)),
      yMin: clampY(Math.min(startPy, endPy), canvas, getCanvasPlotMetrics),
      yMax: clampY(Math.max(startPy, endPy), canvas, getCanvasPlotMetrics),
    };
    clearEditDrSelections(state);
    setEditDrSelection(state, {
      start: Math.max(0, sel.start),
      end: Math.max(sel.start + 1, sel.end),
      yMin: sel.yMin,
      yMax: sel.yMax,
    });
    renderEditExplorer();
    if (state.edit.mode === "delete_dr") {
      deleteDrInSelection(sel);
    }
  });
}

// --- Load / save ---

export async function saveEditedFile(deps) {
  const {
    state,
    getSuggestedNpzName,
    persistNpzBySaveTarget,
    getBidsMuscleNames,
    setEditStatus,
    recomputeEditDirty,
  } = deps;

  const distimes = state.edit.distimes || [];
  if (!distimes.length) {
    setEditStatus("Load a decomposition first", "error");
    return;
  }
  const muscleNames =
    typeof getBidsMuscleNames === "function" ? getBidsMuscleNames() : [];
  const maxSpike = Math.max(
    0,
    ...distimes
      .flatMap((d) => d || [])
      .map((v) => (Number.isFinite(v) ? v : 0)),
  );
  const totalSamples =
    state.edit.totalSamples ||
    (state.edit.pulseTrains?.[0]?.length ?? 0) ||
    maxSpike + 1;
  const originalFilename = state.edit.filename || "decomposition";
  const originalStem = originalFilename.replace(/\.[^.]+$/, "");
  const entityLabel = originalStem.includes("_grid-")
    ? originalStem.split("_grid-")[0]
    : originalStem.replace(/(_decomp|_edited)+$/, "");
  const payload = {
    distimes,
    flagged: state.edit.flagged || [],
    pulse_trains: state.edit.pulseTrains || [],
    total_samples: totalSamples,
    fsamp: state.edit.fsamp,
    grid_names: state.edit.gridNames,
    mu_grid_index: state.edit.muGridIndex,
    mu_uids: state.edit.muUids || [],
    parameters: state.edit.parameters,
    muscle_names: muscleNames,
    edit_history: state.edit.editHistory || [],
    entity_label: entityLabel,
    file_label: getSuggestedNpzName(
      state.edit.filename || "decomposition",
      "_edited",
    ),
  };
  try {
    setEditStatus("Saving edited file...", "muted");
    const saved = await persistNpzBySaveTarget(payload, payload.file_label);
    setEditOriginalDistimes(
      state,
      (state.edit.distimes || []).map((d) => [...d]),
    );
    recomputeEditDirty();
    setEditStatus(
      saved?.path
        ? `Edited decomposition saved to ${saved.path}`
        : "Edited decomposition saved",
      "success",
    );
  } catch (err) {
    console.error(err);
    setEditStatus(`Save failed: ${err.message}`, "error");
  }
}

export async function loadDecompositionForEdit(deps, file, filepath = null) {
  const {
    state,
    apiFetch,
    apiJson,
    API_BASE,
    applySessionInfoFromDecomposition,
    ensureEditFlagged,
    recomputeEditDirty,
    showWorkspace,
    switchStage,
    renderEditExplorer,
    renderBidsMuscleFields,
    setUploadLoading,
    setEditStatus,
    resetEditState,
    els,
  } = deps;

  if (!file && !filepath) return;
  setUploadLoading(true);
  setEditStatus("Loading...", "muted");
  // Clear previous Session Info rows while loading a new decomposition so stale
  // raw-file labels cannot bleed into edit mode if payload fields are sparse.
  setEditGridNames(state, []);
  setMuscle(state, []);
  try {
    let data;
    if (filepath) {
      const res = await apiFetch(
        `${API_BASE}/edit/load-by-path`,
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "x-muedit-binary": "1",
          },
          body: JSON.stringify({ path: filepath }),
        },
        120000,
      );
      data = decodeEditLoadPayload(
        await res.arrayBuffer(),
        res.headers.get("x-muedit-format"),
      );
    } else if (typeof apiFetch === "function") {
      const formData = new FormData();
      formData.append("file", file);
      const res = await apiFetch(
        `${API_BASE}/edit/load`,
        {
          method: "POST",
          headers: { "x-muedit-binary": "1" },
          body: formData,
        },
        120000,
      );
      data = decodeEditLoadPayload(
        await res.arrayBuffer(),
        res.headers.get("x-muedit-format"),
      );
    } else {
      const formData = new FormData();
      formData.append("file", file);
      data = await apiJson(
        `${API_BASE}/edit/load`,
        { method: "POST", body: formData },
        120000,
      );
    }
    data = normalizeEditLoadPayload(data);
    const resolvedGridNames = normalizeGridNames(data.grid_names, {
      minimumCount: inferGridCount({
        gridNames: data.grid_names,
        muGridIndex: data.mu_grid_index,
        muscles: data.muscle,
      }),
    });
    setGridNames(state, resolvedGridNames);
    setEditGridNames(state, resolvedGridNames);
    applySessionInfoFromDecomposition(file, data);
    const loadedBidsRoot = String(data?.bids_root || "").trim();
    if (loadedBidsRoot) {
      if (els.editBidsRoot) els.editBidsRoot.value = loadedBidsRoot;
      setEditBidsRoot(state, loadedBidsRoot);
    }
    setEditSignalToken(state, data.edit_signal_token || "");
    setEditFile(state, file);
    setEditFilename(state, file.name || data.file_label || "decomposition");
    setEditPulseTrains(
      state,
      data.pulse_trains_full || data.pulse_trains || [],
    );
    setEditOriginalPulseTrains(
      state,
      (state.edit.pulseTrains || []).map((row) => (row ? [...row] : row)),
    );
    const dist = data.distime_all || data.distime || [];
    setEditDistimes(
      state,
      dist.map((d) =>
        (d || []).map((v) => Number(v)).filter((v) => Number.isFinite(v)),
      ),
    );
    setEditOriginalDistimes(
      state,
      (state.edit.distimes || []).map((d) => [...d]),
    );
    setEditMuGridIndex(state, data.mu_grid_index || []);
    if (!state.edit.muGridIndex.length && state.edit.distimes.length) {
      setEditMuGridIndex(
        state,
        state.edit.distimes.map(() => 0),
      );
    }
    setEditMuUids(
      state,
      Array.isArray(data.mu_uids) && data.mu_uids.length === state.edit.distimes.length
        ? data.mu_uids
        : generateMuUids(state.edit.muGridIndex),
    );
    setEditHistory(state, Array.isArray(data.edit_history) ? data.edit_history : []);
    setEditFsamp(state, data.fsamp);
    setEditParameters(state, data.parameters || {});
    setEditTotalSamples(
      state,
      data.total_samples || (state.edit.pulseTrains?.[0]?.length ?? 0),
    );
    ensureEditFlagged();
    setEditFlaggedArray(
      state,
      state.edit.distimes.map(() => false),
    );
    setEditBackup(state, null);
    setEditCurrentMuGrid(state, 0, { resetView: false });
    setEditCurrentMu(state, 0, { resetView: false });
    setEditView(state, { start: 0, end: state.edit.totalSamples || 0 });
    clearAllEditSelections(state);
    recomputeEditDirty();
    if (els.editSaveBtn) els.editSaveBtn.disabled = false;
    setEditStatus("Loaded. Click the spike train to edit.", "success");
    showWorkspace({ keepLandingVisible: true });
    switchStage("edit");
    if (typeof renderBidsMuscleFields === "function") {
      renderBidsMuscleFields();
    }
    renderEditExplorer();
    if (els.landing) els.landing.classList.add("hidden");
  } catch (err) {
    console.error(err);
    setEditStatus(`Failed to load: ${err.message}`, "error");
    resetEditState();
  } finally {
    setUploadLoading(false);
  }
}

export async function handleDecompositionFile(deps, file) {
  if (!file) return;
  if (typeof deps.loadDecompositionForEdit === "function") {
    await deps.loadDecompositionForEdit(file);
    return;
  }
  await loadDecompositionForEdit(deps, file);
}
