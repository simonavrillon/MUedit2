import {
  resetEditState as resetEditStateFeature,
  addSpikesInSelection as addSpikesInSelectionFeature,
  addArtifactInSelection as addArtifactInSelectionFeature,
  deleteSpikesInSelection as deleteSpikesInSelectionFeature,
  deleteDrInSelection as deleteDrInSelectionFeature,
  buildEditDropdownModel,
} from "../../editing/operations.js";
import {
  renderEditExplorer as renderEditExplorerFeature,
  renderEditTimeline as renderEditTimelineFeature,
  renderEditDropdownsView,
  bindEditCanvas,
  bindEditDrCanvas,
  bindEditTimeline,
} from "../../view/edit-canvas.js";
import {
  saveEditedFile,
  loadDecompositionForEdit,
  requestRoiEdit as requestRoiEditFeature,
  requestFilterUpdate,
  removeOutliers,
  flagMuForDeletion,
  removeDuplicateMus,
  resetCurrentMuEdits,
  duplicateMu,
  undoEdit,
  prepareEditGrid,
} from "../services/editing-service.js";
import {
  setEditMode as setEditModeAction,
  setEditProject,
  setEditCurrentMu,
  setEditCurrentMuGrid,
  setEditPulseView,
} from "../../state/actions.js";
import { getEditMuIndicesForGrid } from "../../state/selectors.js";
import { getCanvasPlotMetrics, oncePerFrame } from "../../view/plots.js";
import { handleKeyboardNavigation } from "../services/navigation.js";
import { createViewFetcher } from "../services/view-fetcher.js";
import { errorMessage } from "../services/error-service.js";
import {
  applyLabeledToggle,
  runEditAction,
  setEditActionBusy,
} from "../../view/controls.js";

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
    setEditActionBusy(els.editAddBtn, state.edit.mode === "add");
    setEditActionBusy(
      els.editAddArtifactBtn,
      state.edit.mode === "add_artifact",
    );
    setEditActionBusy(
      els.editDeleteSpikeBtn,
      state.edit.mode === "delete_spikes",
    );
    if (els.editUndoBtn) els.editUndoBtn.disabled = !state.edit.canUndo;
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

  /** @param {HTMLCanvasElement} canvas */
  function plotWidth(canvas) {
    return getCanvasPlotMetrics(canvas, true).plotWidth || 1;
  }

  /** @type {EditStage["resetEditState"]} */
  function resetEditState() {
    resetEditStateFeature(app);
    if (els.editSaveBtn) els.editSaveBtn.disabled = true;
    if (els.bidsProject) els.bidsProject.value = "";
  }

  /** What the dropdowns last showed; a redraw that changes none of it leaves them be. */
  let shownDropdowns = "";

  /** @type {EditStage["renderEditDropdowns"]} */
  function renderEditDropdowns() {
    const model = buildEditDropdownModel(state, (grid) =>
      getEditMuIndicesForGrid(state, grid),
    );
    if (model.needsGridSwitch) {
      setEditCurrentMuGrid(state, model.targetGrid, { resetView: false });
    }
    if (model.needsMuSwitch) {
      setEditCurrentMu(state, model.currentMu, { resetView: false });
    }
    const shown = JSON.stringify([
      model.gridNames,
      model.muOptions,
      model.targetGrid,
      model.currentMu,
    ]);
    if (shown === shownDropdowns) return;
    shownDropdowns = shown;
    renderEditDropdownsView(els, model);
  }

  /** @type {EditStage["renderEditExplorer"]} */
  function renderEditExplorer() {
    renderEditExplorerFeature(app);
    renderEditTimelineFeature(app);
  }

  /** @type {EditStage["scheduleEditRender"]} */
  const scheduleEditRender = oncePerFrame(() => app.renderEditExplorer());

  const pulseFetcher = createViewFetcher(
    (
      /** @type {{ token: string, mu: number, start: number, end: number, bins: number }} */ params,
    ) => app.api.fetchPulse(params),
    (view) => {
      setEditPulseView(state, view);
      if (state.currentStage === "edit") renderEditExplorer();
    },
    (err) =>
      app.setEditStatus(`Pulse train failed: ${errorMessage(err)}`, "error"),
  );

  /** @type {EditStage["ensureEditPulseView"]} */
  function ensureEditPulseView() {
    const e = state.edit;
    const canvas = els.editPulseCanvas;
    const total = e.totalSamples || 0;
    const mu = e.currentMu ?? 0;
    if (!e.token || !canvas || !total || e.distimes?.[mu] === undefined) return;
    const { start, end } = e.view || { start: 0, end: total };
    const bins = Math.max(1, Math.round(plotWidth(canvas)));
    const version = e.versions?.[mu] ?? 0;
    const shown = e.pulseView;
    if (
      shown &&
      shown.mu === mu &&
      shown.version === version &&
      shown.start === start &&
      shown.end === end &&
      shown.bins === bins
    ) {
      return;
    }
    pulseFetcher.want(`${e.token}:${mu}:${version}:${start}:${end}:${bins}`, {
      token: e.token,
      mu,
      start,
      end,
      bins,
    });
  }

  /** @type {EditStage["requestRoiEdit"]} */
  const requestRoiEdit = (action, payload) =>
    requestRoiEditFeature(app, action, payload);
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

  /** @type {EditStage["loadDecompositionForEditByPath"]} */
  function loadDecompositionForEditByPath(path, options) {
    const name = path.split("/").pop()?.split("\\").pop() || path;
    return loadDecompositionForEdit(app, { name }, path, options);
  }

  return {
    getPulsePlotHeight: () => plotHeight(els.editPulseCanvas),
    getDrPlotHeight: () => plotHeight(els.editDrCanvas),
    resetEditState,
    refreshEditModeButtons,
    setEditMode,
    renderEditDropdowns,
    renderEditExplorer,
    scheduleEditRender,
    ensureEditPulseView,
    requestRoiEdit,
    addSpikesInSelection,
    addArtifactInSelection,
    deleteSpikesInSelection,
    deleteDrInSelection,
    loadDecompositionForEditByPath,
  };
}

/** @param {App} app */
export function setupEditEvents(app) {
  const {
    els,
    state,
    renderEditExplorer,
    setEditMode,
    refreshEditModeButtons,
  } = app;

  bindEditCanvas(app);
  bindEditDrCanvas(app);
  bindEditTimeline(app);

  els.editMuGridSelect?.addEventListener("change", () => {
    const idx = Number(els.editMuGridSelect.value) || 0;
    setEditCurrentMuGrid(state, idx, { resetView: true });
    renderEditExplorer();
    prepareEditGrid(app, idx);
    els.editMuGridSelect.blur();
  });

  els.editMuSelect?.addEventListener("change", () => {
    const idx = Number(els.editMuSelect.value);
    setEditCurrentMu(state, idx, { resetView: true });
    renderEditExplorer();
    els.editMuSelect.blur();
  });

  els.editSaveBtn?.addEventListener("click", () => {
    void runEditAction(els.editSaveBtn, () => saveEditedFile(app));
  });
  els.editResetBtn?.addEventListener("click", () => {
    void runEditAction(els.editResetBtn, () => resetCurrentMuEdits(app));
  });
  els.editUpdateBtn?.addEventListener("click", () => {
    void runEditAction(els.editUpdateBtn, () => requestFilterUpdate(app));
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
    void runEditAction(els.editOutliersBtn, () => removeOutliers(app));
  });
  els.editFlagBtn?.addEventListener("click", () => {
    void runEditAction(els.editFlagBtn, () => flagMuForDeletion(app));
  });
  els.editDuplicateBtn?.addEventListener("click", () => {
    void runEditAction(els.editDuplicateBtn, () => duplicateMu(app));
  });
  els.editDeduplicateBtn?.addEventListener("click", () => {
    void runEditAction(els.editDeduplicateBtn, () => removeDuplicateMus(app));
  });
  els.editUndoBtn?.addEventListener("click", () => {
    void runEditAction(els.editUndoBtn, () => undoEdit(app));
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
