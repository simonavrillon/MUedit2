import { handleError } from "./error_service.js";
import { decodeEditLoadPayload } from "../../api/binary_payloads.js";
import { normalizeEditLoadPayload } from "../../api/payloads.js";
import { inferGridCount, normalizeGridNames } from "../../io/grid.js";
import {
  clearAllEditSelections,
  clearEditDrSelections,
  clearEditPulseSelections,
  setEditArtifactTimes,
  setEditArtifactTimesForMu,
  setEditBackup,
  setEditBidsRoot,
  setEditCurrentMu,
  setEditCurrentMuGrid,
  setEditDistimes,
  setEditDistimesForMu,
  setEditFile,
  setEditFilename,
  setEditFlagForMu,
  setEditFlaggedArray,
  setEditFsamp,
  setEditGridNames,
  setEditHistory,
  setEditMuGridIndex,
  setEditMuUids,
  setEditOriginalDistimes,
  setEditOriginalPulseTrains,
  setEditParameters,
  setEditPulseTrainForMu,
  setEditPulseTrains,
  setEditSignalToken,
  setEditTotalSamples,
  setEditView,
  setGridNames,
  setMuscle,
} from "../../state/actions.js";

function spikesDiff(before, after) {
  const afterSet = new Set(after);
  const beforeSet = new Set(before);
  return {
    added: after.filter((s) => !beforeSet.has(s)),
    removed: before.filter((s) => !afterSet.has(s)),
  };
}

function generateMuUids(muGridIndex) {
  const counts = {};
  return (muGridIndex || []).map((gridIdx) => {
    const count = counts[gridIdx] || 0;
    counts[gridIdx] = count + 1;
    return `g${gridIdx}_mu${count}`;
  });
}

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
  const artifactsBefore = [...(state.edit.artifactTimes?.[payload.muIdx] || [])];
  const isArtifact = action === "add-artifact";
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
        artifact_times: payload.artifact_times !== undefined
          ? payload.artifact_times
          : isArtifact
            ? (state.edit.artifactTimes?.[payload.muIdx] || [])
            : undefined,
      }),
    });
    if (data.distimes !== undefined) {
      setEditDistimesForMu(state, payload.muIdx, data.distimes || []);
    }
    if (data.artifact_times !== undefined) {
      setEditArtifactTimesForMu(state, payload.muIdx, data.artifact_times || []);
    }
    ensureEditFlagged();
    setEditFlagForMu(state, payload.muIdx, false);
    if (deps.appendEditHistory) {
      const muUid = state.edit.muUids?.[payload.muIdx] ?? `mu${payload.muIdx}`;

      if (action === "delete-spikes") {
        // Create separate log entries for spikes and artifacts
        const distimesAfter = state.edit.distimes?.[payload.muIdx] || [];
        const { removed: spikesRemoved } = spikesDiff(distimesBefore, distimesAfter);
        if (spikesRemoved.length) {
          deps.appendEditHistory({
            type: "delete_spikes",
            mu_uid: muUid,
            spikes_removed: spikesRemoved,
          });
        }

        const artifactsAfter = state.edit.artifactTimes?.[payload.muIdx] || [];
        const { removed: artifactsRemoved } = spikesDiff(artifactsBefore, artifactsAfter);
        if (artifactsRemoved.length) {
          deps.appendEditHistory({
            type: "delete_artifact",
            mu_uid: muUid,
            artifacts_removed: artifactsRemoved,
          });
        }
      } else {
        // Keep existing behavior for other actions
        const typeMap = { "add-spikes": "add_spikes", "delete-dr": "delete_dr", "add-artifact": "add_artifact" };
        const entry = { type: typeMap[action] || action, mu_uid: muUid };
        if (isArtifact) {
          const artifactsAfter = state.edit.artifactTimes?.[payload.muIdx] || [];
          const { added: artifactsAdded } = spikesDiff(artifactsBefore, artifactsAfter);
          if (artifactsAdded.length) entry.artifacts_added = artifactsAdded;
        } else {
          const distimesAfter = state.edit.distimes?.[payload.muIdx] || [];
          const { added, removed } = spikesDiff(distimesBefore, distimesAfter);
          if (added.length) entry.spikes_added = added;
          if (removed.length) entry.spikes_removed = removed;
        }
        deps.appendEditHistory(entry);
      }
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
    handleError(err, setEditStatus, "ROI failed");
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
          lock_spikes: els.editLockSpikesToggle?.dataset.state === "on",
          flagged: state.edit.flagged || [],
          artifact_times: state.edit.artifactTimes?.[muIdx] || [],
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
      const lockSpikes = els.editLockSpikesToggle?.dataset.state === "on";
      const entry = { type: "update_filter", mu_uid: muUid, view_start: start, view_end: end, use_peeloff: peeloff, lock_spikes: lockSpikes };
      if (added.length) entry.spikes_added = added;
      if (removed.length) entry.spikes_removed = removed;
      deps.appendEditHistory(entry);
    }
    recomputeEditDirty();
    refreshEditTotals();
    renderEditExplorer();
    setEditStatus("MU filter updated", "success");
  } catch (err) {
    handleError(err, setEditStatus, "Filter update failed");
  }
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
    handleError(err, setEditStatus, "Outlier removal failed");
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
    setEditPulseTrains(state, keptIdx.map((i) => state.edit.pulseTrains?.[i] || []));
    setEditOriginalDistimes(state, (state.edit.originalDistimes || []).filter((_, i) => keptSet.has(i)));
    setEditOriginalPulseTrains(state, (state.edit.originalPulseTrains || []).filter((_, i) => keptSet.has(i)));
    setEditMuGridIndex(state, (state.edit.muGridIndex || []).filter((_, i) => keptSet.has(i)));
    setEditFlaggedArray(state, (state.edit.flagged || []).filter((_, i) => keptSet.has(i)));
    setEditMuUids(state, (state.edit.muUids || []).filter((_, i) => keptSet.has(i)));
    setEditArtifactTimes(state, (state.edit.artifactTimes || []).filter((_, i) => keptSet.has(i)));

    const removedCount = data.removed_count || (distimes.length - keptIdx.length);
    if (deps.appendEditHistory) {
      const removedUids = (state.edit.muUids || []).filter((_, i) => !keptSet.has(i));
      deps.appendEditHistory({ type: "remove_duplicates", removed_count: removedCount, removed_mu_uids: removedUids });
    }

    const currentMu = state.edit.currentMu ?? 0;
    const newCurrentMu = keptIdx.includes(currentMu)
      ? keptIdx.indexOf(currentMu)
      : 0;
    setEditCurrentMu(state, newCurrentMu, { resetView: false });

    recomputeEditDirty();
    renderEditExplorer();
    setEditStatus(`${removedCount} duplicate${removedCount !== 1 ? "s" : ""} removed`, "success");
  } catch (err) {
    handleError(err, setEditStatus, "Deduplication failed");
  }
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
    handleError(err, setEditStatus, "Flagging failed");
  }
}

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
    artifact_times: distimes.map((_, i) => {
      const times = state.edit.artifactTimes?.[i];
      return Array.isArray(times) ? times : [];
    }),
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
    handleError(err, setEditStatus, "Save failed");
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
          headers: {},
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
    setEditArtifactTimes(state, Array.isArray(data.artifact_times) ? data.artifact_times : []);
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
    handleError(err, setEditStatus, "Failed to load");
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
