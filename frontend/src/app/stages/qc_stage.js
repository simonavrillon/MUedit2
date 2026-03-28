import {
  populateAuxSelector as populateAuxSelectorFeature,
  renderAuxiliaryChannels as renderAuxiliaryChannelsFeature,
  requestQcGridWindow as requestQcGridWindowFeature,
  requestPreview as requestPreviewFeature,
  handleRawFile as handleRawFileFeature,
  handleLandingFile as handleLandingFileFeature,
} from "../../features/qc.js";
import {
  syncRois as syncRoisController,
  refreshVisuals as refreshVisualsController,
  enableRoiSelection as enableRoiSelectionController,
  renderChannelQC as renderChannelQCController,
} from "../../controllers/qc_canvas.js";
import {
  beginRawPreviewTransition,
  rollbackRawPreviewTransition,
} from "../../state/transitions.js";

export function createQcStageService(deps) {
  const {
    state,
    els,
    API_BASE,
    apiFetch,
    apiJson,
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
    targetPoints = 96,
  ) {
    return requestQcGridWindowFeature(
      { state, apiJson, apiFetch, API_BASE, renderChannelQC, setStatus },
      gridIdx,
      start,
      end,
      targetPoints,
    );
  }

  async function requestPreview(options = {}) {
    return requestPreviewFeature(
      {
        state,
        apiJson,
        API_BASE,
        setUploadLoading,
        updateProgressFn: updateProgress,
        populateAuxSelectorFn: populateAuxSelector,
        ensureDiscardMasks,
        populateGridTabs,
        requestQcGridWindowFn: requestQcGridWindow,
        getCurrentGrid,
        enableRoiSelection,
        renderBidsAutoInfo,
        renderBidsMuscleFields,
        setStatus,
        showWorkspace,
        nextFrame,
        refreshVisuals,
        renderChannelQC,
        els,
      },
      options,
    );
  }

  async function handleRawFile(file, options = {}) {
    return handleRawFileFeature(
      {
        state,
        els,
        requestPreviewFn: requestPreview,
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
        handleRawFileFn: handleRawFile,
        handleDecompositionFileFn: handleDecompositionFile,
      },
      file,
    );
  }

  async function handleRawFilePath(path, name, options = {}) {
    const syntheticFile = { name };
    beginRawPreviewTransition(state, syntheticFile);
    if (els.fileName) {
      els.fileName.textContent = name;
      els.fileName.classList.remove("loading");
    }
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
        syncRoisFn: syncRois,
        refreshVisualsFn: refreshVisuals,
        requestQcGridWindowFn: requestQcGridWindow,
        updateProgressFn: updateProgress,
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
