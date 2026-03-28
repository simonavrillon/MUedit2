import {
  autoDownloadRunDecomposition as autoDownloadRunDecompositionFeature,
  runDecomposition as runDecompositionFeature,
} from "../../features/run.js";
import {
  buildRunMuDropdownModel as buildRunMuDropdownModelFeature,
  buildRunMuExplorerModel as buildRunMuExplorerModelFeature,
} from "../../features/run_explorer.js";
import { handleStreamMessage as handleStreamMessageFeature } from "../../features/events.js";
import {
  renderMuDropdowns as renderMuDropdownsController,
  renderMuExplorer as renderMuExplorerController,
} from "../../controllers/mu_explorer.js";
import {
  setRunCurrentMu,
  setRunCurrentMuGrid,
  setRunView,
} from "../../state/actions.js";
import { getRunMuIndicesForGrid } from "../../state/selectors.js";

export function createRunStageService(deps) {
  const {
    state,
    els,
    API_BASE,
    apiFetch,
    COLORS,
    drawSeries,
    drawGridOverlay,
    getSuggestedNpzName,
    persistNpzBySaveTarget,
    getBidsRoot,
    getBidsMuscleNames,
    buildParams,
    updateStartAvailability,
    switchStage,
    setStatus,
    updateProgress,
    ensureDiscardMasks,
    renderChannelQC,
    getCurrentGrid,
    requestQcGridWindow,
    showWorkspace,
    renderBidsAutoInfo,
    renderBidsMuscleFields,
    populateAuxSelector,
    renderAuxiliaryChannels,
    enableRoiSelection,
  } = deps;

  function getMuIndicesForGrid(gridIdx) {
    return getRunMuIndicesForGrid(state, gridIdx);
  }

  function renderMuDropdowns() {
    const model = buildRunMuDropdownModelFeature({
      state,
      getMuIndicesForGridFn: getMuIndicesForGrid,
    });
    const selectedGrid = model.selectedGrid ?? state.currentMuGrid ?? 0;
    setRunCurrentMuGrid(state, selectedGrid, {
      resetView: false,
    });
    const selectedMu = Number.isFinite(model.selectedMu)
      ? model.selectedMu
      : model.muOptions?.length
        ? Number(model.muOptions[0].value)
        : Number.isFinite(state.currentMu)
          ? state.currentMu
          : 0;
    setRunCurrentMu(state, selectedMu, { resetView: false });
    renderMuDropdownsController(els, model);
  }

  function renderMuExplorer() {
    renderMuDropdowns();
    const fs = Number(els.fsamp?.value);
    const model = buildRunMuExplorerModelFeature({
      state,
      fsamp: Number.isFinite(fs) && fs > 0 ? fs : null,
    });
    if (model.nextView) {
      setRunView(state, model.nextView);
      model.view = model.nextView;
    }
    renderMuExplorerController({ els, drawSeries }, model);
  }

  function autoDownloadRunDecomposition() {
    return autoDownloadRunDecompositionFeature({
      state,
      els,
      getSuggestedNpzName,
      persistNpzBySaveTarget,
      getBidsMuscleNames,
      setStatus,
    });
  }

  function handleStreamMessage(msg) {
    return handleStreamMessageFeature(
      {
        state,
        els,
        apiFetch,
        API_BASE,
        setStatus,
        updateProgressFn: updateProgress,
        ensureDiscardMasks,
        renderChannelQC,
        getCurrentGrid,
        requestQcGridWindow,
        drawGridOverlay,
        showWorkspace,
        renderMuExplorer,
        renderBidsAutoInfo,
        renderBidsMuscleFields,
        populateAuxSelector,
        renderAuxiliaryChannels,
        enableRoiSelection,
        autoDownloadRunDecomposition,
      },
      msg,
    );
  }

  function runDecomposition() {
    return runDecompositionFeature({
      state,
      els,
      API_BASE,
      apiFetch,
      getBidsRoot,
      getBidsMuscleNames,
      buildParams,
      updateStartAvailability,
      switchStage,
      setStatus,
      updateProgressFn: updateProgress,
      handleStreamMessageFn: handleStreamMessage,
    });
  }

  return {
    getMuIndicesForGrid,
    renderMuDropdowns,
    renderMuExplorer,
    autoDownloadRunDecomposition,
    handleStreamMessage,
    runDecomposition,
  };
}
