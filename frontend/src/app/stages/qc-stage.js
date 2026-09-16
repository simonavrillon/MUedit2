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
} from "../../state/actions.js";

export function createQcStageService(deps) {
  const { state, els, setStatus, updateProgress, updateStartAvailability } =
    deps;

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
    return requestQcGridWindowFeature(ctx, gridIdx, start, end);
  }

  async function requestPreview(options = {}) {
    return requestPreviewFeature(ctx, options);
  }

  async function handleRawFilePath(path, name, options = {}) {
    const syntheticFile = { name, path };
    beginRawPreviewTransition(state, syntheticFile);
    resetBidsEntityDefaults(els, name);
    setStatus("File ready");
    updateStartAvailability();
    const ok = await requestPreview({
      silentFailure: options.silentPreviewFailure ?? false,
      filepath: path,
    });
    if (!ok) {
      rollbackRawPreviewTransition(state);
      updateStartAvailability();
    }
    return ok;
  }

  function renderChannelQC(waitForMiniPlots = false) {
    return renderChannelQCController(ctx, waitForMiniPlots);
  }

  function enableRoiSelection(canvasId) {
    return enableRoiSelectionController(ctx, canvasId);
  }

  function refreshVisuals() {
    refreshVisualsController(ctx);
  }

  function syncRois(nwin) {
    syncRoisController(state, nwin);
  }

  function runAutoQc() {
    return requestAutoQcFeature(ctx);
  }

  /** Arm (or cancel) artifact selection for the next drag on the EMG plot. */
  function toggleArtifactMode() {
    setArtifactMode(state, !state.artifactMode);
    refreshVisuals();
    updateProgress(
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
    updateProgress(
      undefined,
      removed
        ? `Artifact window removed (${n} left)`
        : "No artifact windows to remove",
    );
  }

  const ctx = {
    ...deps,
    populateAuxSelector,
    renderAuxiliaryChannels,
    requestQcGridWindow,
    renderChannelQC,
    enableRoiSelection,
    refreshVisuals,
    refreshVisualsFn: refreshVisuals,
    syncRois,
  };

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
  };
}
