import {
  setStatus as setStatusController,
  showWorkspace as showWorkspaceController,
  populateGridTabs as populateGridTabsController,
  updateStepAvailability as updateStepAvailabilityController,
  updateWorkflowStepper as updateWorkflowStepperController,
} from "./navigation.js";
import { setSettingsOpen as setSettingsOpenController } from "./layout.js";
import {
  renderActiveStage,
  switchStage as switchStageController,
} from "../stages/lifecycle.js";
import { oncePerFrame } from "../../view/plots.js";

/** @typedef {import("../context.js").App} App */
/** @typedef {import("../context.js").UiService} UiService */

/**
 * @param {App} app
 * @returns {import("../context.js").UiService}
 */
export function createUiService(app) {
  const { els } = app;

  /** @type {UiService["setStatus"]} */
  function setStatus(text, tone = "muted") {
    setStatusController(els, text, tone);
  }

  /** @type {UiService["setEditStatus"]} */
  function setEditStatus(text, tone = "muted") {
    if (!els.editStatus) return;
    els.editStatus.textContent = text;
    els.editStatus.dataset.tone = tone;
  }

  /** @type {UiService["updateWorkflowStepper"]} */
  const updateWorkflowStepper = (targetStage) =>
    updateWorkflowStepperController(app, targetStage);
  /** @type {UiService["updateStepAvailability"]} */
  const updateStepAvailability = () => updateStepAvailabilityController(app);

  /** @type {UiService["scheduleLayoutRerender"]} */
  const scheduleLayoutRerender = oncePerFrame(() => renderActiveStage(app));
  /** @type {UiService["setSettingsOpen"]} */
  const setSettingsOpen = (open) => setSettingsOpenController(app, open);
  /** @type {UiService["switchStage"]} */
  const switchStage = (target) => switchStageController(app, target);
  /** @type {UiService["populateGridTabs"]} */
  const populateGridTabs = () => populateGridTabsController(app);
  /** @type {UiService["showWorkspace"]} */
  const showWorkspace = (target) => showWorkspaceController(app, target);

  return {
    setStatus,
    setEditStatus,
    updateWorkflowStepper,
    updateStepAvailability,
    setSettingsOpen,
    scheduleLayoutRerender,
    switchStage,
    showWorkspace,
    populateGridTabs,
  };
}
