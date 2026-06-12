import {
  setStatus as setStatusController,
  showWorkspace as showWorkspaceController,
  switchStage as switchStageController,
  populateGridTabs as populateGridTabsController,
  updateStepAvailability as updateStepAvailabilityController,
  updateWorkflowStepper as updateWorkflowStepperController,
} from "./navigation.js";
import {
  ensureSettingsToggleIcon as ensureSettingsToggleIconController,
  initLayoutResizePolicy as initLayoutResizePolicyController,
  rerenderPlotsForLayout as rerenderPlotsForLayoutController,
  scheduleLayoutRerender as scheduleLayoutRerenderController,
  setSettingsOpen as setSettingsOpenController,
  toggleSettingsOpen as toggleSettingsOpenController,
} from "./layout.js";

export function createUiService(deps) {
  const {
    els,
    state,
    renderChannelQC,
    refreshVisuals,
    renderEditExplorer,
    setSelectedGrid,
  } = deps;

  function setStatus(text, tone = "muted") {
    setStatusController(els, text, tone);
  }

  function setEditStatus(text, tone = "muted") {
    if (!els.editStatus) return;
    els.editStatus.textContent = text;
    els.editStatus.dataset.tone = tone;
  }

  function updateWorkflowStepper(targetStage) {
    updateWorkflowStepperController({ els, state }, targetStage);
  }

  function updateStepAvailability() {
    updateStepAvailabilityController({ els, state });
  }

  function setRunPhase(pct, message = "", stage = "") {
    if (!els.runPhase) return;
    const stageText =
      typeof stage === "string" ? stage.trim().toLowerCase() : "";
    const messageText = typeof message === "string" ? message.trim() : "";
    const msgLower = messageText.toLowerCase();
    let phase = "Idle";
    if (stageText === "error" || messageText.toLowerCase().includes("error")) {
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

  function rerenderPlotsForLayout() {
    rerenderPlotsForLayoutController({
      state,
      renderChannelQC,
      refreshVisuals,
      renderEditExplorer,
    });
  }

  function scheduleLayoutRerender(delay = 90) {
    scheduleLayoutRerenderController({ rerenderPlotsForLayout }, delay);
  }

  function initLayoutResizePolicy() {
    initLayoutResizePolicyController({ els, scheduleLayoutRerender });
  }

  function setSettingsOpen(open) {
    setSettingsOpenController({ els, scheduleLayoutRerender }, open);
  }

  function toggleSettingsOpen() {
    toggleSettingsOpenController({ els, setSettingsOpen });
  }

  function ensureSettingsToggleIcon() {
    ensureSettingsToggleIconController(els);
  }

  function switchStage(target) {
    switchStageController(
      {
        state,
        els,
        setSettingsOpen,
        setStatus,
        updateStepAvailability,
        updateWorkflowStepper,
        scheduleLayoutRerender,
      },
      target,
    );
  }

  function populateGridTabs() {
    populateGridTabsController({ els, state, setSelectedGrid });
  }

  function showWorkspace(options = {}) {
    showWorkspaceController(
      { els, state, setSettingsOpen, switchStage, populateGridTabs },
      options,
    );
  }

  function applyToggle(btn, on) {
    if (!btn) return;
    const label =
      btn.dataset.label || btn.textContent.split(":")[0] || "Toggle";
    btn.dataset.state = on ? "on" : "off";
    btn.setAttribute("aria-pressed", on ? "true" : "false");
    btn.classList.toggle("on", on);
    btn.textContent = `${label}: ${on ? "On" : "Off"}`;
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
    setRunPhase,
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
    runEditAction,
    setEditActionBusy,
  };
}
