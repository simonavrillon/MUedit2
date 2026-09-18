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
  setEditMode as setEditModeAction,
  setEditProject,
  setEditCurrentMu,
  setEditCurrentMuGrid,
} from "../../state/actions.js";
import { getEditMuIndicesForGrid } from "../../state/selectors.js";
import { getCanvasPlotMetrics } from "../../view/plots.js";
import { handleKeyboardNavigation } from "../services/navigation.js";

/** @typedef {import("../context.js").App} App */
/** @typedef {import("../context.js").EditStage} EditStage */

/**
 * @param {App} app
 * @returns {import("../context.js").EditStage}
 */
export function createEditStageService(app) {
  const { state, els } = app;

  /** @type {EditStage["refreshEditModeButtons"]} */
  function refreshEditModeButtons() {
    app.setEditActionBusy(els.editAddBtn, state.edit.mode === "add");
    app.setEditActionBusy(
      els.editAddArtifactBtn,
      state.edit.mode === "add_artifact",
    );
    app.setEditActionBusy(
      els.editDeleteSpikeBtn,
      state.edit.mode === "delete_spikes",
    );
    if (els.editUndoBtn) els.editUndoBtn.disabled = !state.edit.backup;
  }

  /** @type {EditStage["setEditMode"]} */
  function setEditMode(mode, message) {
    setEditModeAction(state, mode);
    refreshEditModeButtons();
    if (mode) {
      app.setEditStatus(message || `Mode: ${mode}`, "muted");
    }
  }

  /** @param {HTMLCanvasElement | null | undefined} canvas */
  function plotHeight(canvas) {
    return canvas ? getCanvasPlotMetrics(canvas, true).plotHeight || 1 : 1;
  }

  /** @type {EditStage["ensureEditFlagged"]} */
  function ensureEditFlagged() {
    ensureEditFlaggedFeature(state);
  }

  /** @type {EditStage["getRawPulse"]} */
  function getRawPulse(muIdx) {
    return getRawPulseFeature(state, muIdx);
  }

  /** @type {EditStage["getDisplayPulse"]} */
  function getDisplayPulse(muIdx) {
    return getDisplayPulseFeature(state, muIdx);
  }

  /** @type {EditStage["backupEditMu"]} */
  function backupEditMu() {
    backupEditMuFeature(state);
    refreshEditModeButtons();
  }

  /** @type {EditStage["recomputeEditDirty"]} */
  function recomputeEditDirty() {
    recomputeEditDirtyFeature(state);
  }

  /** @type {EditStage["appendEditHistory"]} */
  function appendEditHistory(entry) {
    appendEditHistoryEntry(state, {
      ...entry,
      timestamp: new Date().toISOString(),
    });
  }

  /** @type {EditStage["getEditTotalSamples"]} */
  function getEditTotalSamples() {
    return getEditTotalSamplesFeature(state);
  }

  /** @type {EditStage["getPulseViewMeta"]} */
  function getPulseViewMeta() {
    return getPulseViewMetaFeature(state);
  }

  /** @type {EditStage["refreshEditTotals"]} */
  function refreshEditTotals() {
    refreshEditTotalsFeature(state);
  }

  /** @type {EditStage["resetEditState"]} */
  function resetEditState() {
    resetEditStateFeature(app);
    if (els.editSaveBtn) els.editSaveBtn.disabled = true;
    if (els.bidsProject) els.bidsProject.value = "";
  }

  /** @type {EditStage["getEditMuIndices"]} */
  function getEditMuIndices(gridIdx) {
    return getEditMuIndicesForGrid(state, gridIdx);
  }

  /** @type {EditStage["renderEditDropdowns"]} */
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

  /** @type {EditStage["renderInstantaneousDr"]} */
  function renderInstantaneousDr() {
    renderInstantaneousDrFeature(app);
  }

  /** @type {EditStage["renderEditExplorer"]} */
  function renderEditExplorer() {
    renderEditExplorerFeature(app);
    renderEditTimelineFeature(app);
  }

  /** @type {EditStage["restoreEditBackup"]} */
  const restoreEditBackup = () => restoreEditBackupFeature(app);
  /** @type {EditStage["requestRoiEdit"]} */
  const requestRoiEdit = (action, payload) =>
    requestRoiEditFeature(app, action, payload);
  /** @type {EditStage["requestFilterUpdate"]} */
  const requestFilterUpdate = (mode) => requestFilterUpdateFeature(app, mode);
  /** @type {EditStage["updateMuFilter"]} */
  const updateMuFilter = () => requestFilterUpdate("update-filter");
  /** @type {EditStage["addSpikesInSelection"]} */
  const addSpikesInSelection = (sel) => addSpikesInSelectionFeature(app, sel);
  /** @type {EditStage["addArtifactInSelection"]} */
  const addArtifactInSelection = (sel) =>
    addArtifactInSelectionFeature(app, sel);
  /** @type {EditStage["deleteSpikesInSelection"]} */
  const deleteSpikesInSelection = (sel) =>
    deleteSpikesInSelectionFeature(app, sel);
  /** @type {EditStage["deleteDrInSelection"]} */
  const deleteDrInSelection = (sel) => deleteDrInSelectionFeature(app, sel);
  /** @type {EditStage["removeOutliers"]} */
  const removeOutliers = () => removeOutliersFeature(app);
  /** @type {EditStage["flagMuForDeletion"]} */
  const flagMuForDeletion = () => flagMuForDeletionFeature(app);
  /** @type {EditStage["resetCurrentMuEdits"]} */
  const resetCurrentMuEdits = () => resetCurrentMuEditsFeature(app);
  /** @type {EditStage["removeDuplicateMus"]} */
  const removeDuplicateMus = () => removeDuplicateMusFeature(app);
  /** @type {EditStage["duplicateMu"]} */
  const duplicateMu = () => duplicateMuFeature(app);
  /** @type {EditStage["bindEditCanvas"]} */
  const bindEditCanvas = () => bindEditCanvasFeature(app);
  /** @type {EditStage["bindEditDrCanvas"]} */
  const bindEditDrCanvas = () => bindEditDrCanvasFeature(app);
  /** @type {EditStage["bindEditTimeline"]} */
  const bindEditTimeline = () => bindEditTimelineFeature(app);
  /** @type {EditStage["saveEditedFile"]} */
  const saveEditedFile = () => saveEditedFileFeature(app);
  /** @type {EditStage["loadDecompositionForEdit"]} */
  const loadDecompositionForEdit = (file, absolutePath) =>
    loadDecompositionForEditFeature(app, file, absolutePath);

  /** @type {EditStage["loadDecompositionForEditByPath"]} */
  function loadDecompositionForEditByPath(path) {
    const name = path.split("/").pop()?.split("\\").pop() || path;
    return loadDecompositionForEdit({ name }, path);
  }

  return {
    getEditMuIndices,
    ensureEditFlagged,
    getRawPulse,
    getDisplayPulse,
    backupEditMu,
    recomputeEditDirty,
    refreshEditTotals,
    getEditTotalSamples,
    getPulseViewMeta,
    getPulsePlotHeight: () => plotHeight(els.editPulseCanvas),
    getDrPlotHeight: () => plotHeight(els.editDrCanvas),
    appendEditHistory,
    resetEditState,
    refreshEditModeButtons,
    setEditMode,
    renderEditDropdowns,
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

/** @param {App} app */
export function setupEditEvents(app) {
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
    applyLabeledToggle,
  } = app;

  bindEditCanvas();
  bindEditDrCanvas();
  bindEditTimeline();

  els.editMuGridSelect?.addEventListener("change", () => {
    const idx = Number(els.editMuGridSelect.value) || 0;
    setEditCurrentMuGrid(state, idx, { resetView: true });
    renderEditExplorer();
    els.editMuGridSelect.blur();
  });

  els.editMuSelect?.addEventListener("change", () => {
    const idx = Number(els.editMuSelect.value);
    setEditCurrentMu(state, idx, { resetView: true });
    renderEditExplorer();
    els.editMuSelect.blur();
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

  els.bidsProject?.addEventListener("input", () => {
    setEditProject(state, els.bidsProject.value);
  });

  els.bidsPlacementScheme?.addEventListener("change", () => {
    const row = els.bidsPlacementDescRow;
    if (row)
      row.classList.toggle("hidden", els.bidsPlacementScheme.value !== "Other");
  });

  refreshEditModeButtons();
  window.addEventListener("keydown", (e) => handleKeyboardNavigation(app, e));
}
