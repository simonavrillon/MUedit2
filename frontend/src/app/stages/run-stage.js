import {
  autoSaveRunDecomposition as autoSaveRunDecompositionFeature,
  cancelDecomposition,
  runDecomposition,
  handleStreamMessage as handleStreamMessageFeature,
} from "../../decomp/run.js";
import { renderRunStage as renderRunStageView } from "../../view/run-live.js";
import {
  DEFAULT_POSTPROCESS_MODE,
  POSTPROCESS_MODES,
  buildDecomposeParams,
} from "../../decomp/params.js";
import { oncePerFrame } from "../../view/plots.js";
import {
  isToggleOn,
  setupLockedOnToggle,
  setupToggle,
  toggleConditional,
} from "../../view/controls.js";

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
      peelOn: isToggleOn(els.peelOffToggle),
      postprocessMode: els.postprocessMode?.value || "windowed",
      covOn: isToggleOn(els.covToggle),
      peelWindow: Number(els.peelOffWindow?.value) || 25,
      covVal: Number(els.covValue?.value) || 0.5,
      silVal: Number(els.silValue?.value) || 0.9,
      duplicatesthresh: Number(els.duplicatesthresh?.value) || 0.3,
    });
  }

  /** @type {RunStage["renderRunStage"]} */
  function renderRunStage() {
    renderRunStageView(els, state.runLive, {
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
  app.updateStartAvailability();

  // A session field marked missing clears once it is filled.
  const clearFilled = (/** @type {Event} */ e) => {
    const field = /** @type {HTMLInputElement} */ (e.target);
    if (field.getAttribute?.("aria-invalid") && field.value.trim()) {
      field.removeAttribute("aria-invalid");
    }
  };
  els.settingsPanel?.addEventListener("input", clearFilled);
  els.settingsPanel?.addEventListener("change", clearFilled);
}
