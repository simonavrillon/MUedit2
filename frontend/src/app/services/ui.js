import {
  setStatus as setStatusController,
  showWorkspace as showWorkspaceController,
  populateGridTabs as populateGridTabsController,
  updateStepAvailability as updateStepAvailabilityController,
  updateWorkflowStepper as updateWorkflowStepperController,
} from "./navigation.js";
import {
  ensureSettingsToggleIcon as ensureSettingsToggleIconController,
  initLayoutResizePolicy as initLayoutResizePolicyController,
  scheduleLayoutRerender as scheduleLayoutRerenderController,
  setSettingsOpen as setSettingsOpenController,
  toggleSettingsOpen as toggleSettingsOpenController,
} from "./layout.js";
import { switchStage as switchStageController } from "../stages/lifecycle.js";

/** @typedef {import("../context.js").App} App */

/**
 * @param {App} app
 * @returns {import("../context.js").UiService}
 */
export function createUiService(app) {
  const { els } = app;

  function setStatus(text, tone = "muted") {
    setStatusController(els, text, tone);
  }

  function setEditStatus(text, tone = "muted") {
    if (!els.editStatus) return;
    els.editStatus.textContent = text;
    els.editStatus.dataset.tone = tone;
  }

  const updateWorkflowStepper = (targetStage) =>
    updateWorkflowStepperController(app, targetStage);
  const updateStepAvailability = () => updateStepAvailabilityController(app);

  function setRunPhase(pct, message = "", stage = "") {
    if (!els.runPhase) return;
    const stageText =
      typeof stage === "string" ? stage.trim().toLowerCase() : "";
    const messageText = typeof message === "string" ? message.trim() : "";
    const msgLower = messageText.toLowerCase();
    let phase = "Idle";
    if (stageText === "error" || msgLower.includes("error")) {
      phase = "Failed";
    } else if (stageText === "done") {
      phase = "Complete";
    } else if (msgLower.includes("loading")) {
      phase = "Loading";
    } else if (msgLower.includes("preprocess")) {
      phase = "Preprocessing";
    } else if (msgLower.includes("finalizing")) {
      phase = "Finalizing";
    } else if (msgLower.includes("grid")) {
      phase = "Decomposing";
    } else if (stageText) {
      phase = stageText.charAt(0).toUpperCase() + stageText.slice(1);
    } else if (typeof pct === "number" && pct > 0) {
      phase = "Running";
    }
    els.runPhase.textContent = phase;
  }

  function updateProgress(pct, message = "", stage = "") {
    if (els.progressBar && pct !== undefined) {
      const clamped = Math.max(0, Math.min(100, pct));
      els.progressBar.style.width = `${clamped}%`;
      setRunPhase(clamped, message, stage);
    } else {
      setRunPhase(pct, message, stage);
    }
    if (els.progressText) {
      els.progressText.textContent = message || "";
    }
  }

  const scheduleLayoutRerender = (delay = 90) =>
    scheduleLayoutRerenderController(app, delay);
  const initLayoutResizePolicy = () => initLayoutResizePolicyController(app);
  const setSettingsOpen = (open) => setSettingsOpenController(app, open);
  const toggleSettingsOpen = () => toggleSettingsOpenController(app);
  const ensureSettingsToggleIcon = () =>
    ensureSettingsToggleIconController(els);
  const switchStage = (target) => switchStageController(app, target);
  const populateGridTabs = () => populateGridTabsController(app);
  const showWorkspace = (options = {}) => showWorkspaceController(app, options);

  function applyToggle(btn, on) {
    if (!btn) return;
    const label =
      btn.dataset.label || btn.textContent.split(":")[0] || "Toggle";
    btn.dataset.state = on ? "on" : "off";
    btn.setAttribute("aria-pressed", on ? "true" : "false");
    btn.classList.toggle("on", on);
    btn.textContent = `${label}: ${on ? "On" : "Off"}`;
  }

  function applyLabeledToggle(btn, on, { shortSel, fullSel, prefix }) {
    if (!btn) return;
    btn.dataset.state = on ? "on" : "off";
    btn.setAttribute("aria-pressed", on ? "true" : "false");
    btn.classList.toggle("on", on);
    const label = on ? "On" : "Off";
    const shortEl = btn.querySelector(shortSel);
    const fullEl = btn.querySelector(fullSel);
    if (shortEl) shortEl.textContent = label;
    if (fullEl) fullEl.textContent = `${prefix}: ${label}`;
  }

  function isToggleOn(btn) {
    return btn?.dataset.state === "on";
  }

  function toggleConditional(id, show) {
    const el = document.getElementById(id);
    if (el) {
      el.classList.toggle("hidden", !show);
    }
  }

  function setupToggle(btn, onChange) {
    if (!btn) return;
    btn.setAttribute("tabindex", "0");
    applyToggle(btn, isToggleOn(btn));
    if (onChange) {
      onChange(isToggleOn(btn));
    }
    btn.addEventListener("click", () => {
      const next = !isToggleOn(btn);
      applyToggle(btn, next);
      if (onChange) onChange(next);
    });
    btn.addEventListener("keydown", (e) => {
      if (e.key === " " || e.key === "Enter") {
        e.preventDefault();
        btn.click();
      }
    });
  }

  function setupLockedOnToggle(btn, onChange) {
    if (!btn) return;
    btn.setAttribute("tabindex", "0");
    btn.setAttribute("aria-disabled", "true");
    btn.title = "This filter is always enabled";
    applyToggle(btn, true);
    if (onChange) onChange(true);
    btn.addEventListener("click", (e) => {
      e.preventDefault();
      applyToggle(btn, true);
    });
    btn.addEventListener("keydown", (e) => {
      if (e.key === " " || e.key === "Enter") {
        e.preventDefault();
        applyToggle(btn, true);
      }
    });
  }

  function setEditActionBusy(button, busy) {
    if (!button) return;
    button.classList.toggle("is-running", !!busy);
    button.setAttribute("aria-busy", busy ? "true" : "false");
  }

  async function runEditAction(button, fn) {
    if (!button) return fn();
    if (button.dataset.busy === "1") return undefined;
    button.dataset.busy = "1";
    setEditActionBusy(button, true);
    try {
      return await fn();
    } finally {
      delete button.dataset.busy;
      setEditActionBusy(button, false);
    }
  }

  return {
    setStatus,
    setEditStatus,
    updateProgress,
    updateWorkflowStepper,
    updateStepAvailability,
    setSettingsOpen,
    toggleSettingsOpen,
    ensureSettingsToggleIcon,
    initLayoutResizePolicy,
    scheduleLayoutRerender,
    switchStage,
    showWorkspace,
    populateGridTabs,
    setupToggle,
    setupLockedOnToggle,
    toggleConditional,
    isToggleOn,
    applyLabeledToggle,
    runEditAction,
    setEditActionBusy,
  };
}
