import {
  autoSaveRunDecomposition as autoSaveRunDecompositionFeature,
  cancelDecomposition as cancelDecompositionFeature,
  runDecomposition as runDecompositionFeature,
  handleStreamMessage as handleStreamMessageFeature,
} from "../../decomp/run.js";
import { buildRunPlan } from "../../decomp/live.js";
import {
  renderRunStage as renderRunStageView,
  renderRunTime,
  updateRunDots as updateRunDotsView,
} from "../../view/run-live.js";
import {
  DEFAULT_POSTPROCESS_MODE,
  POSTPROCESS_MODES,
  buildDecomposeParams,
} from "../../decomp/params.js";
import { oncePerFrame } from "../../view/plots.js";

/** @typedef {import("../context.js").App} App */
/** @typedef {import("../context.js").RunStage} RunStage */

/**
 * @param {App} app
 * @returns {import("../context.js").RunStage}
 */
export function createRunStageService(app) {
  const { state, els } = app;

  /** @type {RunStage["updateStartAvailability"]} */
  function updateStartAvailability() {
    const blocked = !state.file || state.isRunning;
    for (const btn of [els.start, els.runStartBtn, els.runAgainBtn]) {
      if (btn) btn.disabled = blocked;
    }
    if (els.cancelRun) {
      els.cancelRun.hidden = !state.isRunning;
      els.cancelRun.disabled = false;
    }
  }

  /** @type {RunStage["buildParams"]} */
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

  /** @type {RunStage["renderRunStage"]} */
  function renderRunStage() {
    const plan = state.runLive
      ? []
      : buildRunPlan(
          state,
          buildParams(),
          els.postprocessMode?.value || DEFAULT_POSTPROCESS_MODE,
        );
    renderRunStageView(els, state.runLive, {
      gridNames: state.gridNames || [],
      plan,
      now: Date.now(),
    });
  }

  return {
    renderRunStage,
    scheduleRunRender: oncePerFrame(() => app.renderRunStage()),
    renderRunClock: () => renderRunTime(els, state.runLive, Date.now()),
    updateRunDots: (change) =>
      state.runLive && updateRunDotsView(els, state.runLive, change),
    autoSaveRunDecomposition: () => autoSaveRunDecompositionFeature(app),
    handleStreamMessage: (msg) => handleStreamMessageFeature(app, msg),
    runDecomposition: () => runDecompositionFeature(app),
    cancelDecomposition: () => cancelDecompositionFeature(app),
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
    cancelDecomposition,
    enableRoiSelection,
    syncRois,
    refreshVisuals,
    setupToggle,
    setupLockedOnToggle,
    toggleConditional,
    updateStartAvailability,
    renderAuxiliaryChannels,
    runAutoQc,
    toggleArtifactMode,
    removeLastArtifact,
  } = app;

  els.start?.addEventListener("click", runDecomposition);
  els.runStartBtn?.addEventListener("click", runDecomposition);
  els.runAgainBtn?.addEventListener("click", runDecomposition);
  els.cancelRun?.addEventListener("click", cancelDecomposition);
  els.runRetrySaveBtn?.addEventListener("click", () => {
    void app.autoSaveRunDecomposition();
  });
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

  // Keep the plan shown before a run in step with the settings panel.
  const refreshPlan = () => {
    if (state.currentStage === "run" && !state.runLive) app.renderRunStage();
  };
  els.settingsPanel?.addEventListener("change", refreshPlan);
  els.settingsPanel?.addEventListener("click", refreshPlan);

  els.auxSelector?.addEventListener("change", () => {
    renderAuxiliaryChannels();
    els.auxSelector.blur();
  });
}
