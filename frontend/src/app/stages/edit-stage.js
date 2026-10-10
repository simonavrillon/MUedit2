import {
  resetEditState as resetEditStateFeature,
  addSpikesInSelection as addSpikesInSelectionFeature,
  addArtifactInSelection as addArtifactInSelectionFeature,
  deleteSpikesInSelection as deleteSpikesInSelectionFeature,
  buildEditDropdownModel,
} from "../../editing/operations.js";
import {
  drawEditPulse,
  drawEditRates,
  drawEditTimeline,
  renderEditDropdownsView,
  bindEditCanvas,
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
  setEditView,
} from "../../state/actions.js";
import { getEditMuIndicesForGrid } from "../../state/selectors.js";
import { getCanvasPlotMetrics, oncePerFrame } from "../../view/plots.js";
import { handleKeyboardNavigation } from "../services/navigation.js";
import { createViewFetcher } from "../services/view-fetcher.js";
import { errorMessage } from "../services/error-service.js";
import {
  applyLabeledToggle,
  isToggleOn,
  runEditAction,
  setEditActionBusy,
} from "../../view/controls.js";

/** @typedef {import("../context.js").App} App */
/** @typedef {import("../context.js").EditStage} EditStage */
/** @typedef {import("../context.js").Els} Els */
/** @typedef {{ [K in keyof Els]: Els[K] extends HTMLButtonElement | null ? K : never }[keyof Els]} ButtonId */

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
    if (els.editSaveBtn) els.editSaveBtn.disabled = !state.edit.token;
  }

  /** @type {EditStage["setEditMode"]} */
  function setEditMode(mode, message) {
    setEditModeAction(state, mode);
    refreshEditModeButtons();
    if (mode) {
      app.setStatus(message || `Mode: ${mode}`, "muted");
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
    app.setBidsProject("");
  }

  /**
   * Bring what the page shows in line with the data before it is drawn: a
   * grid that has MUs, one of its MUs, and a view inside the recording.
   */
  function settleEditSelection() {
    const model = buildEditDropdownModel(state, (grid) =>
      getEditMuIndicesForGrid(state, grid),
    );
    if (model.needsGridSwitch) {
      setEditCurrentMuGrid(state, model.targetGrid, { resetView: false });
    }
    if (model.needsMuSwitch) {
      setEditCurrentMu(state, model.currentMu, { resetView: false });
    }
    const total = state.edit.totalSamples || 0;
    if (!state.edit.view || state.edit.view.end > total) {
      setEditView(state, { start: 0, end: total });
    }
    return model;
  }

  /** What the dropdowns last showed; a redraw that changes none of it leaves them be. */
  let shownDropdowns = "";

  /** @param {import("../../editing/operations.js").EditDropdownModel} model */
  function renderEditDropdowns(model) {
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
    renderEditDropdowns(settleEditSelection());
    drawEditPulse(els, state);
    drawEditRates(els, state);
    drawEditTimeline(els, state);
    // The window drawn follows the view, the MU's edits and the plot's width.
    ensureEditPulseView();
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
    (err) => app.setStatus(`Pulse train failed: ${errorMessage(err)}`, "error"),
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

  /** @type {EditStage["loadDecompositionForEditByPath"]} */
  function loadDecompositionForEditByPath(path, options) {
    const name = path.split("/").pop()?.split("\\").pop() || path;
    return loadDecompositionForEdit(app, { name }, path, options);
  }

  return {
    getPulsePlotHeight: () => plotHeight(els.editPulseCanvas),
    resetEditState,
    refreshEditModeButtons,
    setEditMode,
    renderEditExplorer,
    scheduleEditRender,
    ensureEditPulseView,
    requestRoiEdit,
    addSpikesInSelection,
    addArtifactInSelection,
    deleteSpikesInSelection,
    loadDecompositionForEditByPath,
  };
}

/** @typedef {{ shortSel: string, fullSel: string, prefix: string }} ToggleLabels */

/** @type {ToggleLabels} */
const PEEL_OFF = {
  shortSel: ".peeloff-short",
  fullSel: ".peeloff-full",
  prefix: "Peel-off",
};
/** @type {ToggleLabels} */
const LOCK_SPIKES = {
  shortSel: ".lockspikes-short",
  fullSel: ".lockspikes-full",
  prefix: "Lock",
};

/**
 * @param {HTMLButtonElement | null} btn
 * @param {ToggleLabels} labels
 */
function flipToggle(btn, labels) {
  if (btn) applyLabeledToggle(btn, !isToggleOn(btn), labels);
}

/**
 * The edit toolbar: each command's button and, for some, its key. An `edit`
 * talks to the server and its button shows busy until it is done; a `run`
 * only changes the page.
 *
 * @typedef {object} EditCommand
 * @property {ButtonId} button
 * @property {string} [key] `KeyboardEvent.key`, lower case.
 * @property {(app: App) => Promise<unknown>} [edit]
 * @property {(app: App) => void} [run]
 */

/** @type {EditCommand[]} */
export const EDIT_COMMANDS = [
  {
    button: "editAddBtn",
    key: "a",
    run: (app) =>
      app.setEditMode("add", "Drag a box on pulse train to add spikes"),
  },
  {
    button: "editDeleteSpikeBtn",
    key: "d",
    run: (app) =>
      app.setEditMode(
        "delete_spikes",
        "Drag a box on pulse train to delete spikes",
      ),
  },
  {
    button: "editAddArtifactBtn",
    key: "x",
    run: (app) =>
      app.setEditMode(
        "add_artifact",
        "Drag a box on pulse train to mark an artifact",
      ),
  },
  { button: "editOutliersBtn", key: "r", edit: removeOutliers },
  { button: "editUpdateBtn", key: " ", edit: requestFilterUpdate },
  {
    button: "editPeelOffToggle",
    key: "p",
    run: (app) => flipToggle(app.els.editPeelOffToggle, PEEL_OFF),
  },
  {
    button: "editLockSpikesToggle",
    key: "l",
    run: (app) => flipToggle(app.els.editLockSpikesToggle, LOCK_SPIKES),
  },
  { button: "editSaveBtn", edit: saveEditedFile },
  { button: "editResetBtn", edit: resetCurrentMuEdits },
  { button: "editFlagBtn", edit: flagMuForDeletion },
  { button: "editDuplicateBtn", edit: duplicateMu },
  { button: "editDeduplicateBtn", edit: removeDuplicateMus },
  { button: "editUndoBtn", edit: undoEdit },
];

/**
 * @param {App} app
 * @param {EditCommand} command
 */
function runCommand(app, command) {
  const { edit, run } = command;
  if (edit) void runEditAction(app.els[command.button], () => edit(app));
  else run?.(app);
}

/** @param {KeyboardEvent} e */
function typingInAField(e) {
  const target = /** @type {HTMLElement | null} */ (e.target);
  return ["INPUT", "TEXTAREA", "SELECT"].includes(target?.tagName ?? "");
}

/** @param {App} app */
export function setupEditEvents(app) {
  const { els, state, renderEditExplorer, refreshEditModeButtons } = app;

  bindEditCanvas(app);
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

  applyLabeledToggle(els.editPeelOffToggle, false, PEEL_OFF);
  applyLabeledToggle(els.editLockSpikesToggle, false, LOCK_SPIKES);
  for (const command of EDIT_COMMANDS) {
    els[command.button]?.addEventListener("click", () =>
      runCommand(app, command),
    );
  }

  els.bidsProject?.addEventListener("input", () => {
    setEditProject(state, app.getBidsProject());
  });

  els.bidsPlacementScheme?.addEventListener("change", () => {
    const row = els.bidsPlacementDescRow;
    if (row)
      row.classList.toggle("hidden", els.bidsPlacementScheme.value !== "Other");
  });

  refreshEditModeButtons();
  window.addEventListener("keydown", (e) => {
    // The landing page hides the workspace without leaving the edit stage.
    const shown = !els.workspace?.classList.contains("hidden");
    if (state.currentStage !== "edit" || !shown || typingInAField(e)) return;
    const key = e.key.toLowerCase();
    const command = EDIT_COMMANDS.find((c) => c.key === key);
    if (command) {
      runCommand(app, command);
      e.preventDefault();
      return;
    }
    handleKeyboardNavigation(app, e);
  });
}
