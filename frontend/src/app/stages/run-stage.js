/**
 * The run stage: its service (parameters, start availability, the stream,
 * rendering and auto-save) and its event bindings.
 */
import {
  autoSaveRunDecomposition as autoSaveRunDecompositionFeature,
  cancelDecomposition,
  runDecomposition,
  handleStreamMessage as handleStreamMessageFeature,
} from "../../decomp/run.js";
import { renderRunStage as renderRunStageFeature } from "../../view/run-live.js";
import { buildDecomposeParams, postprocessMode } from "../../decomp/params.js";
import { oncePerFrame } from "../../view/plots.js";
import { isToggleOn, setupToggle } from "../../view/controls.js";

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
    for (const btn of [els.start, els.runAgainBtn]) {
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
      peelOffEnabled: isToggleOn(els.peelOffToggle),
      postprocessMode: els.postprocessMode?.value,
      covFilter: isToggleOn(els.covToggle),
      peelOffWinMs: Number(els.peelOffWindow?.value) || 25,
      covThr: Number(els.covValue?.value) || 0.5,
      silThr: Number(els.silValue?.value) || 0.9,
      duplicatesthresh: Number(els.duplicatesthresh?.value) || 0.3,
    });
  }

  /** @type {RunStage["renderRunStage"]} */
  function renderRunStage() {
    renderRunStageFeature(els, state.runLive, {
      gridNames: state.gridNames || [],
      now: Date.now(),
    });
  }

  return {
    renderRunStage,
    scheduleRunRender: oncePerFrame(() => app.renderRunStage()),
    autoSaveRunDecomposition: () => autoSaveRunDecompositionFeature(app),
    handleStreamMessage: (msg) => handleStreamMessageFeature(app, msg),
    updateStartAvailability,
    buildParams,
  };
}

/** @param {App} app */
export function setupRunEvents(app) {
  const { els } = app;

  const run = () => void runDecomposition(app);
  els.start?.addEventListener("click", run);
  els.runAgainBtn?.addEventListener("click", run);
  els.cancelRun?.addEventListener("click", () => void cancelDecomposition(app));
  els.runRetrySaveBtn?.addEventListener("click", () => {
    void app.autoSaveRunDecomposition();
  });

  setupToggle(els.peelOffToggle, (on) => {
    if (els.peelOffSettings) els.peelOffSettings.hidden = !on;
  });
  const renderPostprocessHint = () => {
    if (!els.postprocessModeHint) return;
    els.postprocessModeHint.textContent = postprocessMode(
      els.postprocessMode?.value,
    ).hint;
  };
  renderPostprocessHint();
  els.postprocessMode?.addEventListener("change", () => {
    renderPostprocessHint();
    els.postprocessMode.blur();
  });
  setupToggle(els.covToggle, (on) => {
    if (els.covSettings) els.covSettings.hidden = !on;
  });
  app.updateStartAvailability();
}
