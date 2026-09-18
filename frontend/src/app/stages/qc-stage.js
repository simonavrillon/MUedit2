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

/**
 * @param {App} app
 * @returns {import("../context.js").QcStage}
 */
export function createQcStageService(app) {
  const { state, els } = app;

  function populateAuxSelector() {
    populateAuxSelectorFeature(els, state);
  }

  function renderAuxiliaryChannels() {
    renderAuxiliaryChannelsFeature(els, state);
  }

  async function requestQcGridWindow(
    gridIdx,
    start = 0,
    end = state.seriesLength,
  ) {
    return requestQcGridWindowFeature(app, gridIdx, start, end);
  }

  async function requestPreview(options = {}) {
    return requestPreviewFeature(app, options);
  }

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

  function renderChannelQC(waitForMiniPlots = false) {
    return renderChannelQCController(app, waitForMiniPlots);
  }

  function enableRoiSelection(canvasId) {
    return enableRoiSelectionController(app, canvasId);
  }

  function refreshVisuals() {
    refreshVisualsController(app);
  }

  function syncRois(nwin) {
    syncRoisController(state, nwin);
  }

  function runAutoQc() {
    return requestAutoQcFeature(app);
  }

  /** Arm (or cancel) artifact selection for the next drag on the EMG plot. */
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
