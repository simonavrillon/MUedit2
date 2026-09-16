import {
  ensureEditFlagged as ensureEditFlaggedFeature,
  getRawPulse as getRawPulseFeature,
  getDisplayPulse as getDisplayPulseFeature,
  backupEditMu as backupEditMuFeature,
  restoreEditBackup as restoreEditBackupFeature,
  recomputeEditDirty as recomputeEditDirtyFeature,
  getEditTotalSamples as getEditTotalSamplesFeature,
  getPulseViewMeta as getPulseViewMetaFeature,
  refreshEditTotals as refreshEditTotalsFeature,
  resetEditState as resetEditStateFeature,
  addSpikesInSelection as addSpikesInSelectionFeature,
  addArtifactInSelection as addArtifactInSelectionFeature,
  deleteSpikesInSelection as deleteSpikesInSelectionFeature,
  deleteDrInSelection as deleteDrInSelectionFeature,
  resetCurrentMuEdits as resetCurrentMuEditsFeature,
  duplicateMu as duplicateMuFeature,
  buildEditDropdownModel,
} from "../../editing/operations.js";
import {
  renderEditExplorer as renderEditExplorerFeature,
  renderInstantaneousDr as renderInstantaneousDrFeature,
  renderEditTimeline as renderEditTimelineFeature,
  renderEditDropdownsView,
  bindEditCanvas as bindEditCanvasFeature,
  bindEditDrCanvas as bindEditDrCanvasFeature,
  bindEditTimeline as bindEditTimelineFeature,
} from "../../view/edit-canvas.js";
import {
  saveEditedFile as saveEditedFileFeature,
  loadDecompositionForEdit as loadDecompositionForEditFeature,
  requestRoiEdit as requestRoiEditFeature,
  requestFilterUpdate as requestFilterUpdateFeature,
  removeOutliers as removeOutliersFeature,
  flagMuForDeletion as flagMuForDeletionFeature,
  removeDuplicateMus as removeDuplicateMusFeature,
} from "../services/editing-service.js";
import {
  appendEditHistoryEntry,
  setEditProject,
  setEditCurrentMu,
  setEditCurrentMuGrid,
  setEditBookmark,
  setShowBookmark,
} from "../../state/actions.js";
import { getEditMuIndicesForGrid } from "../../state/selectors.js";

export function createEditStageService(deps) {
  const { state, els, getCanvasPlotMetrics, refreshEditModeButtons } = deps;

  function plotHeight(canvas) {
    return canvas ? getCanvasPlotMetrics(canvas, true).plotHeight || 1 : 1;
  }

  function ensureEditFlagged() {
    ensureEditFlaggedFeature(state);
  }

  function getRawPulse(muIdx) {
    return getRawPulseFeature(state, muIdx);
  }

  function getDisplayPulse(muIdx) {
    return getDisplayPulseFeature(state, muIdx);
  }

  function backupEditMu() {
    backupEditMuFeature(state);
    refreshEditModeButtons();
  }

  function recomputeEditDirty() {
    recomputeEditDirtyFeature(state);
  }

  function appendEditHistory(entry) {
    appendEditHistoryEntry(state, {
      ...entry,
      timestamp: new Date().toISOString(),
    });
  }

  function getEditTotalSamples() {
    return getEditTotalSamplesFeature(state);
  }

  function getPulseViewMeta() {
    return getPulseViewMetaFeature(state);
  }

  function refreshEditTotals() {
    refreshEditTotalsFeature(state);
  }

  function resetEditState() {
    resetEditStateFeature(ctx);
    if (els.editSaveBtn) els.editSaveBtn.disabled = true;
    if (els.bidsProject) els.bidsProject.value = "";
  }

  function getEditMuIndices(gridIdx) {
    return getEditMuIndicesForGrid(state, gridIdx);
  }

  function renderEditDropdowns() {
    const model = buildEditDropdownModel(state, getEditMuIndices);
    if (model.needsGridSwitch) {
      setEditCurrentMuGrid(state, model.targetGrid, { resetView: false });
    }
    if (model.needsMuSwitch) {
      setEditCurrentMu(state, model.currentMu, { resetView: false });
    }
    renderEditDropdownsView(els, model);
  }

  function renderInstantaneousDr() {
    renderInstantaneousDrFeature(ctx);
  }

  function renderEditExplorer() {
    renderEditExplorerFeature(ctx);
    renderEditTimelineFeature(ctx);
  }

  const restoreEditBackup = () => restoreEditBackupFeature(ctx);
  const requestRoiEdit = (action, payload) =>
    requestRoiEditFeature(ctx, action, payload);
  const requestFilterUpdate = (mode) => requestFilterUpdateFeature(ctx, mode);
  const updateMuFilter = () => requestFilterUpdate("update-filter");
  const addSpikesInSelection = (sel) => addSpikesInSelectionFeature(ctx, sel);
  const addArtifactInSelection = (sel) =>
    addArtifactInSelectionFeature(ctx, sel);
  const deleteSpikesInSelection = (sel) =>
    deleteSpikesInSelectionFeature(ctx, sel);
  const deleteDrInSelection = (sel) => deleteDrInSelectionFeature(ctx, sel);
  const removeOutliers = () => removeOutliersFeature(ctx);
  const flagMuForDeletion = () => flagMuForDeletionFeature(ctx);
  const resetCurrentMuEdits = () => resetCurrentMuEditsFeature(ctx);
  const removeDuplicateMus = () => removeDuplicateMusFeature(ctx);
  const duplicateMu = () => duplicateMuFeature(ctx);
  const bindEditCanvas = () => bindEditCanvasFeature(ctx);
  const bindEditDrCanvas = () => bindEditDrCanvasFeature(ctx);
  const bindEditTimeline = () => bindEditTimelineFeature(ctx);
  const saveEditedFile = () => saveEditedFileFeature(ctx);
  const loadDecompositionForEdit = (file, absolutePath) =>
    loadDecompositionForEditFeature(ctx, file, absolutePath);

  function loadDecompositionForEditByPath(path) {
    const name = path.split("/").pop().split("\\").pop() || path;
    return loadDecompositionForEdit({ name }, path);
  }

  const ctx = {
    ...deps,
    setEditBookmark,
    setShowBookmark,
    appendEditHistory,
    ensureEditFlagged,
    getRawPulse,
    getDisplayPulse,
    backupEditMu,
    recomputeEditDirty,
    refreshEditTotals,
    resetEditState,
    getEditTotalSamples,
    getPulseViewMeta,
    getPulsePlotHeight: () => plotHeight(els.editPulseCanvas),
    getDrPlotHeight: () => plotHeight(els.editDrCanvas),
    renderEditDropdowns,
    renderEditExplorer,
    renderInstantaneousDr,
    requestRoiEdit,
    addSpikesInSelection,
    addArtifactInSelection,
    deleteSpikesInSelection,
    deleteDrInSelection,
  };

  return {
    getEditMuIndices,
    ensureEditFlagged,
    getRawPulse,
    getDisplayPulse,
    backupEditMu,
    recomputeEditDirty,
    refreshEditTotals,
    restoreEditBackup,
    renderEditExplorer,
    renderInstantaneousDr,
    bindEditCanvas,
    bindEditDrCanvas,
    bindEditTimeline,
    requestRoiEdit,
    requestFilterUpdate,
    updateMuFilter,
    addSpikesInSelection,
    addArtifactInSelection,
    deleteSpikesInSelection,
    deleteDrInSelection,
    removeOutliers,
    flagMuForDeletion,
    resetCurrentMuEdits,
    saveEditedFile,
    loadDecompositionForEdit,
    loadDecompositionForEditByPath,
    duplicateMu,
    removeDuplicateMus,
  };
}

/**
 * @typedef {import('../deps.js').EditSetupDeps} EditSetupDeps
 */

/**
 * @param {EditSetupDeps} deps
 */
export function setupEditEvents(deps) {
  const {
    els,
    state,
    bindEditCanvas,
    bindEditDrCanvas,
    bindEditTimeline,
    renderEditExplorer,
    runEditAction,
    saveEditedFile,
    resetCurrentMuEdits,
    updateMuFilter,
    removeOutliers,
    flagMuForDeletion,
    duplicateMu,
    removeDuplicateMus,
    restoreEditBackup,
    setEditMode,
    refreshEditModeButtons,
    handleKeyboardNavigation,
    applyLabeledToggle,
  } = deps;

  bindEditCanvas();
  bindEditDrCanvas();
  bindEditTimeline();

  els.editMuGridSelect?.addEventListener("change", (e) => {
    const idx = Number(e.target.value) || 0;
    setEditCurrentMuGrid(state, idx, { resetView: true });
    renderEditExplorer();
    e.target.blur();
  });

  els.editMuSelect?.addEventListener("change", (e) => {
    const idx = Number(e.target.value);
    setEditCurrentMu(state, idx, { resetView: true });
    renderEditExplorer();
    e.target.blur();
  });

  els.editSaveBtn?.addEventListener("click", () => {
    void runEditAction(els.editSaveBtn, saveEditedFile);
  });
  els.editResetBtn?.addEventListener("click", () => {
    void runEditAction(els.editResetBtn, () => resetCurrentMuEdits());
  });
  els.editUpdateBtn?.addEventListener("click", () => {
    void runEditAction(els.editUpdateBtn, updateMuFilter);
  });
  if (els.editPeelOffToggle) {
    const peelOffConfig = {
      shortSel: ".peeloff-short",
      fullSel: ".peeloff-full",
      prefix: "Peel-off",
    };
    applyLabeledToggle(els.editPeelOffToggle, false, peelOffConfig);
    els.editPeelOffToggle.addEventListener("click", () => {
      applyLabeledToggle(
        els.editPeelOffToggle,
        els.editPeelOffToggle.dataset.state !== "on",
        peelOffConfig,
      );
    });
  }
  if (els.editLockSpikesToggle) {
    const lockSpikesConfig = {
      shortSel: ".lockspikes-short",
      fullSel: ".lockspikes-full",
      prefix: "Lock",
    };
    applyLabeledToggle(els.editLockSpikesToggle, false, lockSpikesConfig);
    els.editLockSpikesToggle.addEventListener("click", () => {
      applyLabeledToggle(
        els.editLockSpikesToggle,
        els.editLockSpikesToggle.dataset.state !== "on",
        lockSpikesConfig,
      );
    });
  }
  els.editOutliersBtn?.addEventListener("click", () => {
    void runEditAction(els.editOutliersBtn, () => removeOutliers());
  });
  els.editFlagBtn?.addEventListener("click", () => {
    void runEditAction(els.editFlagBtn, () => flagMuForDeletion());
  });
  els.editDuplicateBtn?.addEventListener("click", () => {
    void runEditAction(els.editDuplicateBtn, () => duplicateMu());
  });
  els.editDeduplicateBtn?.addEventListener("click", () => {
    void runEditAction(els.editDeduplicateBtn, () => removeDuplicateMus());
  });
  els.editUndoBtn?.addEventListener("click", () => {
    void runEditAction(els.editUndoBtn, () => restoreEditBackup());
  });
  els.editAddBtn?.addEventListener("click", () => {
    setEditMode("add", "Drag a box on pulse train to add spikes");
  });
  els.editAddArtifactBtn?.addEventListener("click", () => {
    setEditMode(
      "add_artifact",
      "Drag a box on pulse train to mark an artifact",
    );
  });
  els.editDeleteSpikeBtn?.addEventListener("click", () => {
    setEditMode("delete_spikes", "Drag a box on pulse train to delete spikes");
  });

  els.bidsProject?.addEventListener("input", (e) => {
    setEditProject(state, e.target.value);
  });

  els.bidsPlacementScheme?.addEventListener("change", (e) => {
    const row = els.bidsPlacementDescRow;
    if (row) row.classList.toggle("hidden", e.target.value !== "Other");
  });

  refreshEditModeButtons();
  window.addEventListener("keydown", handleKeyboardNavigation);
}
