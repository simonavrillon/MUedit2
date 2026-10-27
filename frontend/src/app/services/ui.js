/**
 * The UI service: binds the navigation, layout and lifecycle helpers to the
 * `app` context so features call them as `app.x()`.
 */
import {
  setStatus as setStatusFeature,
  showWorkspace as showWorkspaceFeature,
  populateGridTabs as populateGridTabsFeature,
  updateStepAvailability as updateStepAvailabilityFeature,
  updateWorkflowStepper as updateWorkflowStepperFeature,
} from "./navigation.js";
import { setSettingsOpen as setSettingsOpenFeature } from "./layout.js";
import {
  renderActiveStage,
  switchStage as switchStageFeature,
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
    setStatusFeature(els, text, tone);
  }

  /** @type {UiService["updateWorkflowStepper"]} */
  const updateWorkflowStepper = (targetStage) =>
    updateWorkflowStepperFeature(app, targetStage);
  /** @type {UiService["updateStepAvailability"]} */
  const updateStepAvailability = () => updateStepAvailabilityFeature(app);

  /** @type {UiService["scheduleLayoutRerender"]} */
  const scheduleLayoutRerender = oncePerFrame(() => renderActiveStage(app));
  /** @type {UiService["setSettingsOpen"]} */
  const setSettingsOpen = (open) => setSettingsOpenFeature(app, open);
  /** @type {UiService["switchStage"]} */
  const switchStage = (target) => switchStageFeature(app, target);
  /** @type {UiService["populateGridTabs"]} */
  const populateGridTabs = () => populateGridTabsFeature(app);
  /** @type {UiService["showWorkspace"]} */
  const showWorkspace = (target) => showWorkspaceFeature(app, target);

  return {
    setStatus,
    updateWorkflowStepper,
    updateStepAvailability,
    setSettingsOpen,
    scheduleLayoutRerender,
    switchStage,
    showWorkspace,
    populateGridTabs,
  };
}
