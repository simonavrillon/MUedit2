import {
  autoSaveRunDecomposition as autoSaveRunDecompositionFeature,
  runDecomposition as runDecompositionFeature,
  handleStreamMessage as handleStreamMessageFeature,
} from "../../decomp/run.js";
import {
  buildRunMuDropdownModel as buildRunMuDropdownModelFeature,
  buildRunMuExplorerModel as buildRunMuExplorerModelFeature,
} from "../../decomp/explorer.js";
import {
  renderMuDropdowns as renderMuDropdownsController,
  renderMuExplorer as renderMuExplorerController,
} from "../../view/explorer.js";
import {
  setRunCurrentMu,
  setRunCurrentMuGrid,
  setRunView,
} from "../../state/actions.js";
import { getRunMuIndicesForGrid } from "../../state/selectors.js";
import {
  DEFAULT_POSTPROCESS_MODE,
  POSTPROCESS_MODES,
} from "../../decomp/params.js";

export function createRunStageService(deps) {
  const {
    state,
    els,
    api,
    drawSeries,
    drawGridOverlay,
    getSuggestedNpzName,
    persistNpzBySaveTarget,
    getBidsProject,
    getBidsMuscleNames,
    collectBidsEntities,
    buildParams,
    updateStartAvailability,
    switchStage,
    setStatus,
    updateProgress,
    setProgressText,
    setNwindows,
    emgCanvasId,
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
    loadDecompositionForEditByPath,
  } = deps;

  function getMuIndicesForGrid(gridIdx) {
    return getRunMuIndicesForGrid(state, gridIdx);
  }

  function renderMuDropdowns() {
    const model = buildRunMuDropdownModelFeature({
      state,
      getMuIndicesForGrid,
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
    const model = buildRunMuExplorerModelFeature({
      state,
      fsamp: state.fsamp,
    });
    if (model.nextView) {
      setRunView(state, model.nextView);
      model.view = model.nextView;
    }
    renderMuExplorerController({ els, drawSeries }, model);
  }

  function autoSaveRunDecomposition() {
    return autoSaveRunDecompositionFeature({
      state,
      getSuggestedNpzName,
      persistNpzBySaveTarget,
      getBidsMuscleNames,
      setStatus,
      onSaved: loadDecompositionForEditByPath || null,
    });
  }

  function handleStreamMessage(msg) {
    return handleStreamMessageFeature(
      {
        state,
        api,
        setStatus,
        updateProgress,
        setProgressText,
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
        autoSaveRunDecomposition,
        setNwindows,
        emgCanvasId,
      },
      msg,
    );
  }

  function runDecomposition() {
    return runDecompositionFeature({
      state,
      api,
      getBidsProject,
      collectBidsEntities,
      buildParams,
      updateStartAvailability,
      switchStage,
      setStatus,
      updateProgress,
      handleStreamMessageFn: handleStreamMessage,
    });
  }

  return {
    getMuIndicesForGrid,
    renderMuDropdowns,
    renderMuExplorer,
    autoSaveRunDecomposition,
    handleStreamMessage,
    runDecomposition,
  };
}

/**
 * @typedef {import('../deps.js').RunSetupDeps} RunSetupDeps
 */

/**
 * @param {RunSetupDeps} deps
 */
export function setupRunEvents(deps) {
  const {
    els,
    state,
    runDecomposition,
    enableRoiSelection,
    syncRois,
    refreshVisuals,
    setupToggle,
    setupLockedOnToggle,
    toggleConditional,
    updateStartAvailability,
    renderAuxiliaryChannels,
    renderMuExplorer,
  } = deps;

  els.start?.addEventListener("click", runDecomposition);

  enableRoiSelection("emgCanvas");

  if (els.nwindows) {
    els.nwindows.addEventListener("change", (e) => {
      const nwin = Number(els.nwindows.value) || 1;
      syncRois(nwin);
      refreshVisuals();
      e.target.blur();
    });
  }

  setupToggle(els.peelOffToggle, (on) =>
    toggleConditional("peelOffSettings", on),
  );
  const renderPostprocessHint = () => {
    if (!els.postprocessModeHint) return;
    const mode =
      POSTPROCESS_MODES[els.postprocessMode?.value] ||
      POSTPROCESS_MODES[DEFAULT_POSTPROCESS_MODE];
    els.postprocessModeHint.textContent = mode.hint;
  };
  renderPostprocessHint();
  els.postprocessMode?.addEventListener("change", (e) => {
    renderPostprocessHint();
    e.target.blur();
  });
  setupToggle(els.covToggle, (on) => toggleConditional("covSettings", on));
  setupLockedOnToggle(els.silToggle, (on) =>
    toggleConditional("silSettings", on),
  );
  updateStartAvailability();

  els.auxSelector?.addEventListener("change", (e) => {
    renderAuxiliaryChannels();
    e.target.blur();
  });

  els.muGridSelect?.addEventListener("change", (e) => {
    const idx = Number(e.target.value) || 0;
    setRunCurrentMuGrid(state, idx, { resetView: true });
    renderMuExplorer();
    e.target.blur();
  });

  els.muSelect?.addEventListener("change", (e) => {
    const idx = Number(e.target.value);
    setRunCurrentMu(state, idx, { resetView: true });
    renderMuExplorer();
    e.target.blur();
  });
}
