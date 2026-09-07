import {
  syncRois as syncRoisController,
  requestQcGridWindow as requestQcGridWindowFeature,
  requestPreview as requestPreviewFeature,
  handleRawFile as handleRawFileFeature,
  handleLandingFile as handleLandingFileFeature,
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

export function createQcStageService(deps) {
  const {
    state,
    els,
    api,
    drawMiniSeries,
    drawGridOverlay,
    setStatus,
    updateProgress,
    setUploadLoading,
    showUnsupportedUploadFormatError,
    clearUploadFormatError,
    isSupportedSignalFile,
    detectLandingFileType,
    rawAndDecompositionExtensions,
    ensureDiscardMasks,
    populateGridTabs,
    getCurrentGrid,
    renderBidsAutoInfo,
    renderBidsMuscleFields,
    showWorkspace,
    nextFrame,
    updateStartAvailability,
    resetBidsEntityDefaults: resetBidsEntityDefaultsFn,
    applyPreviewMetadata,
    getNwindows,
    hideLanding,
  } = deps;

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
    return requestQcGridWindowFeature(
      { state, api, renderChannelQC, setStatus },
      gridIdx,
      start,
      end,
    );
  }

  async function requestPreview(options = {}) {
    return requestPreviewFeature(
      {
        state,
        api,
        setUploadLoading,
        updateProgress,
        populateAuxSelector,
        ensureDiscardMasks,
        populateGridTabs,
        requestQcGridWindow,
        getCurrentGrid,
        enableRoiSelection,
        renderBidsAutoInfo,
        renderBidsMuscleFields,
        setStatus,
        showWorkspace,
        nextFrame,
        refreshVisuals,
        renderChannelQC,
        applyPreviewMetadata,
        getNwindows,
        hideLanding,
      },
      options,
    );
  }

  async function handleRawFile(file, options = {}) {
    return handleRawFileFeature(
      {
        state,
        resetBidsEntityDefaults: resetBidsEntityDefaultsFn,
        requestPreview,
        setStatus,
        updateStartAvailability,
      },
      file,
      options,
    );
  }

  async function handleLandingFile(file, handleDecompositionFile) {
    return handleLandingFileFeature(
      {
        setUploadLoading,
        showUnsupportedUploadFormatError,
        clearUploadFormatError,
        isSupportedSignalFile: (input) =>
          isSupportedSignalFile(input, rawAndDecompositionExtensions),
        detectLandingFileType,
        handleRawFile,
        handleDecompositionFile,
      },
      file,
    );
  }

  async function handleRawFilePath(path, name, options = {}) {
    const syntheticFile = { name };
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
    return renderChannelQCController(
      {
        state,
        els,
        nextFrame,
        drawMiniSeries,
        requestQcGridWindow,
        getCurrentGrid,
        ensureDiscardMasks,
      },
      waitForMiniPlots,
    );
  }

  function enableRoiSelection(canvasId) {
    return enableRoiSelectionController(
      {
        state,
        els,
        syncRois,
        refreshVisualsFn: refreshVisuals,
        requestQcGridWindow,
        updateProgress,
      },
      canvasId,
    );
  }

  function refreshVisuals() {
    refreshVisualsController({
      state,
      els,
      drawGridOverlay,
      renderAuxiliaryChannels,
      renderMuExplorer: deps.renderMuExplorer,
    });
  }

  function syncRois(nwin) {
    syncRoisController(state, nwin);
  }

  return {
    populateAuxSelector,
    renderAuxiliaryChannels,
    requestQcGridWindow,
    requestPreview,
    handleRawFile,
    handleLandingFile,
    handleRawFilePath,
    renderChannelQC,
    enableRoiSelection,
    refreshVisuals,
    syncRois,
  };
}
