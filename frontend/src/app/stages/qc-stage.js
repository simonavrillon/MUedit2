import {
  syncRois,
  requestAutoQc,
  createQcTraces,
  requestPreview,
} from "../../signal/qc.js";
import {
  populateAuxSelector as populateAuxSelectorFeature,
  renderAuxiliaryChannels as renderAuxiliaryChannelsFeature,
  refreshVisuals as refreshVisualsController,
  enableRoiSelection as enableRoiSelectionController,
  renderChannelQC as renderChannelQCController,
} from "../../view/qc-renderer.js";
import {
  beginRawPreviewTransition,
  rollbackRawPreviewTransition,
} from "../../state/transitions.js";
import { resetBidsEntityDefaults } from "../../view/bids-renderer.js";
import {
  removeLastArtifactRegion,
  setArtifactMode,
  setCurrentGrid,
} from "../../state/actions.js";
import { oncePerFrame } from "../../view/plots.js";

/** @typedef {import("../context.js").App} App */
/** @typedef {import("../context.js").QcStage} QcStage */

/**
 * @param {App} app
 * @returns {import("../context.js").QcStage}
 */
export function createQcStageService(app) {
  const { state, els } = app;

  /** @type {QcStage["populateAuxSelector"]} */
  function populateAuxSelector() {
    populateAuxSelectorFeature(els, state);
  }

  /** @type {QcStage["renderAuxiliaryChannels"]} */
  function renderAuxiliaryChannels() {
    renderAuxiliaryChannelsFeature(els, state);
  }

  /** @type {QcStage["ensureQcTraces"]} */
  const ensureQcTraces = createQcTraces(app);

  /** @type {QcStage["handleRawFilePath"]} */
  async function handleRawFilePath(path, name, options = {}) {
    const syntheticFile = { name, path };
    beginRawPreviewTransition(state, syntheticFile);
    resetBidsEntityDefaults(els, name);
    app.setStatus("File ready");
    app.updateStartAvailability();
    const ok = await requestPreview(app, {
      silentFailure: options.silentPreviewFailure ?? false,
      filepath: path,
    });
    if (!ok) {
      rollbackRawPreviewTransition(state);
      app.updateStartAvailability();
    }
    return ok;
  }

  /** @type {QcStage["renderChannelQC"]} */
  function renderChannelQC() {
    renderChannelQCController(app);
  }

  /** @type {QcStage["enableRoiSelection"]} */
  function enableRoiSelection(canvasId) {
    return enableRoiSelectionController(app, canvasId);
  }

  /** @type {QcStage["refreshVisuals"]} */
  function refreshVisuals() {
    refreshVisualsController(app);
  }

  /** @type {QcStage["scheduleRefreshVisuals"]} */
  const scheduleRefreshVisuals = oncePerFrame(() => app.refreshVisuals());

  /** @type {QcStage["setSelectedGrid"]} */
  function setSelectedGrid(idx) {
    setCurrentGrid(state, idx);
    const tabs = els.qcGridTabs?.querySelectorAll(".tab-btn") || [];
    tabs.forEach((tab, i) => {
      tab.classList.toggle("active", i === state.currentGrid);
    });
    renderChannelQC();
    renderAuxiliaryChannels();
  }

  return {
    populateAuxSelector,
    renderAuxiliaryChannels,
    ensureQcTraces,
    handleRawFilePath,
    renderChannelQC,
    enableRoiSelection,
    refreshVisuals,
    scheduleRefreshVisuals,
    setSelectedGrid,
  };
}

/**
 * Arm (or cancel) artifact selection for the next drag on the EMG plot.
 *
 * @param {App} app
 */
function toggleArtifactMode(app) {
  const { state } = app;
  setArtifactMode(state, !state.artifactMode);
  app.refreshVisuals();
  app.setStatus(
    state.artifactMode
      ? "Drag on the EMG plot to mark an artifact window"
      : "Artifact selection cancelled",
  );
}

/** @param {App} app */
function removeLastArtifact(app) {
  const { state } = app;
  const removed = removeLastArtifactRegion(state);
  setArtifactMode(state, false);
  app.refreshVisuals();
  const n = state.artifactRegions.length;
  app.setStatus(
    removed
      ? `Artifact window removed (${n} left)`
      : "No artifact windows to remove",
  );
}

/** @param {App} app */
export function setupQcEvents(app) {
  const { els, state } = app;

  els.qcAutoBtn?.addEventListener("click", () => void requestAutoQc(app));
  els.artifactAddBtn?.addEventListener("click", () => toggleArtifactMode(app));
  els.artifactRemoveBtn?.addEventListener("click", () =>
    removeLastArtifact(app),
  );
  app.refreshVisuals();
  app.enableRoiSelection("emgCanvas");

  els.nwindows?.addEventListener("change", () => {
    syncRois(state, Number(els.nwindows.value) || 1);
    app.refreshVisuals();
    els.nwindows.blur();
  });

  els.auxSelector?.addEventListener("change", () => {
    app.renderAuxiliaryChannels();
    els.auxSelector.blur();
  });
}
