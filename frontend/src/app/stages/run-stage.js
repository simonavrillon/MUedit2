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
  buildDecomposeParams,
} from "../../decomp/params.js";
import { drawSeries } from "../../view/plots.js";

/** @typedef {import("../context.js").App} App */

/**
 * @param {App} app
 * @returns {import("../context.js").RunStage}
 */
export function createRunStageService(app) {
  const { state, els } = app;

  function updateStartAvailability() {
    if (els.start) {
      els.start.disabled = !state.file || state.isRunning;
    }
  }

  function buildParams() {
    return buildDecomposeParams({
      niter: Number(els.niter?.value) || 150,
      nwindows: Number(els.nwindows?.value) || 1,
      peelOn: app.isToggleOn(els.peelOffToggle),
      postprocessMode: els.postprocessMode?.value || "windowed",
      covOn: app.isToggleOn(els.covToggle),
      peelWindow: Number(els.peelOffWindow?.value) || 25,
      covVal: Number(els.covValue?.value) || 0.5,
      silVal: Number(els.silValue?.value) || 0.9,
      duplicatesthresh: Number(els.duplicatesthresh?.value) || 0.3,
    });
  }

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

  return {
    getMuIndicesForGrid,
    renderMuDropdowns,
    renderMuExplorer,
    autoSaveRunDecomposition: () => autoSaveRunDecompositionFeature(app),
    handleStreamMessage: (msg) => handleStreamMessageFeature(app, msg),
    runDecomposition: () => runDecompositionFeature(app),
    updateStartAvailability,
    buildParams,
  };
}

/** @param {App} app */
export function setupRunEvents(app) {
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
    runAutoQc,
    toggleArtifactMode,
    removeLastArtifact,
  } = app;

  els.start?.addEventListener("click", runDecomposition);
  els.qcAutoBtn?.addEventListener("click", runAutoQc);
  els.artifactAddBtn?.addEventListener("click", toggleArtifactMode);
  els.artifactRemoveBtn?.addEventListener("click", removeLastArtifact);
  refreshVisuals();

  enableRoiSelection("emgCanvas");

  if (els.nwindows) {
    els.nwindows.addEventListener("change", () => {
      const nwin = Number(els.nwindows.value) || 1;
      syncRois(nwin);
      refreshVisuals();
      els.nwindows.blur();
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
  els.postprocessMode?.addEventListener("change", () => {
    renderPostprocessHint();
    els.postprocessMode.blur();
  });
  setupToggle(els.covToggle, (on) => toggleConditional("covSettings", on));
  setupLockedOnToggle(els.silToggle, (on) =>
    toggleConditional("silSettings", on),
  );
  updateStartAvailability();

  els.auxSelector?.addEventListener("change", () => {
    renderAuxiliaryChannels();
    els.auxSelector.blur();
  });

  els.muGridSelect?.addEventListener("change", () => {
    const idx = Number(els.muGridSelect.value) || 0;
    setRunCurrentMuGrid(state, idx, { resetView: true });
    renderMuExplorer();
    els.muGridSelect.blur();
  });

  els.muSelect?.addEventListener("change", () => {
    const idx = Number(els.muSelect.value);
    setRunCurrentMu(state, idx, { resetView: true });
    renderMuExplorer();
    els.muSelect.blur();
  });
}
