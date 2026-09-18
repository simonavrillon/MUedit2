import {
  syncRois as syncRoisController,
  requestAutoQc as requestAutoQcFeature,
  requestQcGridWindow as requestQcGridWindowFeature,
  requestPreview as requestPreviewFeature,
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
import { roiEnd, roiStart } from "../../state/selectors.js";

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

  /** @type {QcStage["requestQcGridWindow"]} */
  async function requestQcGridWindow(
    gridIdx,
    start = 0,
    end = state.seriesLength,
  ) {
    return requestQcGridWindowFeature(app, gridIdx, start, end);
  }

  /** @type {QcStage["requestPreview"]} */
  async function requestPreview(options = {}) {
    return requestPreviewFeature(app, options);
  }

  /** @type {QcStage["handleRawFilePath"]} */
  async function handleRawFilePath(path, name, options = {}) {
    const syntheticFile = { name, path };
    beginRawPreviewTransition(state, syntheticFile);
    resetBidsEntityDefaults(els, name);
    app.setStatus("File ready");
    app.updateStartAvailability();
    const ok = await requestPreview({
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
  function renderChannelQC(waitForMiniPlots = false) {
    return renderChannelQCController(app, waitForMiniPlots);
  }

  /** @type {QcStage["enableRoiSelection"]} */
  function enableRoiSelection(canvasId) {
    return enableRoiSelectionController(app, canvasId);
  }

  /** @type {QcStage["refreshVisuals"]} */
  function refreshVisuals() {
    refreshVisualsController(app);
  }

  /** @type {QcStage["syncRois"]} */
  function syncRois(nwin) {
    syncRoisController(state, nwin);
  }

  /** @type {QcStage["runAutoQc"]} */
  function runAutoQc() {
    return requestAutoQcFeature(app);
  }

  /** Arm (or cancel) artifact selection for the next drag on the EMG plot. */
  /** @type {QcStage["toggleArtifactMode"]} */
  function toggleArtifactMode() {
    setArtifactMode(state, !state.artifactMode);
    refreshVisuals();
    app.updateProgress(
      undefined,
      state.artifactMode
        ? "Drag on the EMG plot to mark an artifact window"
        : "Artifact selection cancelled",
    );
  }

  /** @type {QcStage["removeLastArtifact"]} */
  function removeLastArtifact() {
    const removed = removeLastArtifactRegion(state);
    setArtifactMode(state, false);
    refreshVisuals();
    const n = state.artifactRegions.length;
    app.updateProgress(
      undefined,
      removed
        ? `Artifact window removed (${n} left)`
        : "No artifact windows to remove",
    );
  }

  /** @type {QcStage["setSelectedGrid"]} */
  function setSelectedGrid(idx) {
    setCurrentGrid(state, idx);
    const tabs = els.qcGridTabs?.querySelectorAll(".tab-btn") || [];
    tabs.forEach((tab, i) => {
      tab.classList.toggle("active", i === state.currentGrid);
    });
    renderChannelQC();
    renderAuxiliaryChannels();
    const roi = state.rois?.[0];
    requestQcGridWindow(
      state.currentGrid,
      roiStart(roi),
      roiEnd(roi, state.seriesLength),
    );
  }

  return {
    populateAuxSelector,
    renderAuxiliaryChannels,
    requestQcGridWindow,
    requestPreview,
    handleRawFilePath,
    renderChannelQC,
    enableRoiSelection,
    refreshVisuals,
    syncRois,
    runAutoQc,
    toggleArtifactMode,
    removeLastArtifact,
    setSelectedGrid,
  };
}
