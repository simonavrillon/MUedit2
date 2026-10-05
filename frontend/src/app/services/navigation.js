import {
  setEditCurrentMu,
  setEditView,
  setShowBookmark,
} from "../../state/actions.js";
import { getEditMuIndicesForGrid } from "../../state/selectors.js";
import { clampView } from "../../editing/operations.js";

/** @typedef {import("../context.js").App} App */
/** @typedef {import("../context.js").Els} Els */
/** @typedef {import("../context.js").Span} Span */
/** @typedef {import("../context.js").StageKey} StageKey */
/** @typedef {import("../context.js").Tone} Tone */
/** @typedef {import("../context.js").WorkflowStep} WorkflowStep */
/** @typedef {import("../../state/state.js").State} State */
/** @typedef {"zoom_in" | "zoom_out" | "scroll_left" | "scroll_right"} ViewAction */

/**
 * @param {Els} els
 * @param {string} text
 * @param {Tone} [tone]
 */
export function setStatus(els, text, tone = "muted") {
  if (!els.status) return;
  els.status.textContent = text;
  els.status.title = text;
  els.status.dataset.tone = tone;
}

/**
 * @param {App} app
 * @param {WorkflowStep} targetStage
 */
export function updateWorkflowStepper(app, targetStage) {
  const { els, state } = app;
  const steps = [
    { key: "import", el: els.stepImport, complete: !!state.file },
    { key: "qc", el: els.stepQc, complete: !!state.gridSeries?.length },
    { key: "run", el: els.stepRun, complete: !!state.runResultToken },
    { key: "edit", el: els.stepEdit, complete: !!state.edit.distimes?.length },
  ];
  steps.forEach((step) => {
    if (!step.el) return;
    step.el.classList.remove("active", "complete", "pending");
    if (step.key === targetStage) {
      step.el.classList.add("active");
    } else if (step.complete) {
      step.el.classList.add("complete");
    } else {
      step.el.classList.add("pending");
    }
  });
  positionStepIndicator(els);
}

/**
 * Place the sliding pill behind the active workflow chip. Called on stage
 * changes and on resizes so the pill follows the chips' current geometry.
 *
 * @param {Els} els
 */
export function positionStepIndicator(els) {
  const stepper = els.stepImport?.closest(".workflow-stepper");
  const indicator = stepper?.querySelector(".step-indicator");
  if (!stepper || !(indicator instanceof HTMLElement)) return;
  const active = stepper.querySelector(".step-chip.active");
  if (!(active instanceof HTMLElement)) {
    indicator.classList.remove("ready");
    return;
  }
  indicator.classList.add("ready");
  indicator.style.left = `${active.offsetLeft}px`;
  indicator.style.top = `${active.offsetTop}px`;
  indicator.style.width = `${active.offsetWidth}px`;
  indicator.style.height = `${active.offsetHeight}px`;
}

/**
 * Leave the landing page for `target`'s page. The page draws at the next
 * frame; the landing goes in that frame, after it, so nothing blank shows.
 *
 * @param {App} app
 * @param {StageKey} target
 */
export function showWorkspace(app, target) {
  const { els } = app;
  els.workspace?.classList.remove("hidden");
  app.switchStage(target);
  window.requestAnimationFrame(() => els.landing?.classList.add("hidden"));
}

/** @param {App} app */
export function updateStepAvailability(app) {
  const { els, state } = app;
  const hasFile = !!state.file;
  const hasPreview = !!state.gridSeries?.length;
  const hasRunResults = !!state.runResultToken;
  const hasEditData = !!state.edit.distimes?.length;

  if (els.stepRun) {
    // Run becomes available once a preview exists, independent of active stage.
    els.stepRun.disabled = !hasFile || !hasPreview;
  }
  if (els.stepEdit) {
    // Edit is available when edit data is loaded, or after a full run.
    els.stepEdit.disabled = !hasEditData && (!hasFile || !hasRunResults);
  }
}

/** @param {App} app */
export function populateGridTabs(app) {
  const { els, state } = app;
  const tabs = els.qcGridTabs;
  if (!tabs) return;
  tabs.innerHTML = "";
  (state.gridNames || []).forEach((name, idx) => {
    const btn = document.createElement("button");
    btn.className = `tab-btn ${idx === state.currentGrid ? "active" : ""}`;
    btn.textContent = `Grid ${idx + 1}${name ? ` • ${name}` : ""}`;
    btn.onclick = () => app.setSelectedGrid(idx);
    tabs.appendChild(btn);
  });
}

/**
 * @param {Span | null} view
 * @param {number} total
 * @param {ViewAction} action
 * @returns {Span | null}
 */
function adjustView(view, total, action) {
  if (!view || total <= 0) return view;
  const span = Math.max(1, view.end - view.start);
  const center = view.start + span / 2;
  let nextSpan = span;
  let nextStart = view.start;
  let nextEnd = view.end;

  if (action === "zoom_in") {
    nextSpan = Math.max(10, Math.round(span * 0.8));
  } else if (action === "zoom_out") {
    nextSpan = Math.min(total, Math.round(span * 1.5));
  } else if (action === "scroll_left") {
    const step = Math.max(1, Math.round(span * 0.05));
    nextStart = view.start - step;
    nextEnd = view.end - step;
  } else if (action === "scroll_right") {
    const step = Math.max(1, Math.round(span * 0.05));
    nextStart = view.start + step;
    nextEnd = view.end + step;
  }

  if (action === "zoom_in" || action === "zoom_out") {
    nextStart = Math.round(center - nextSpan / 2);
    nextEnd = Math.round(center + nextSpan / 2);
  }

  const next = clampView(nextStart, nextEnd - nextStart, total);
  if (next.end <= next.start) next.end = Math.min(total, next.start + 1);
  return next;
}

/**
 * @param {App} app
 * @param {"prev" | "next"} direction
 */
function goToMu(app, direction) {
  const { state } = app;
  const mus = getEditMuIndicesForGrid(state, state.edit.currentMuGrid || 0);
  if (!mus.length) return;
  const idx = mus.indexOf(state.edit.currentMu ?? mus[0]);
  const offset = direction === "prev" ? -1 : 1;
  setEditCurrentMu(state, mus[(idx + offset + mus.length) % mus.length], {
    resetView: true,
  });
  app.renderEditExplorer();
}

/**
 * The edit page's view keys: arrows zoom and scroll, `<` and `>` step
 * through the grid's MUs.
 *
 * @param {App} app
 * @param {KeyboardEvent} e
 */
export function handleKeyboardNavigation(app, e) {
  const { state } = app;
  if (e.key === "<" || e.key === ">") {
    goToMu(app, e.key === "<" ? "prev" : "next");
    e.preventDefault();
    return;
  }

  /** @type {Record<string, ViewAction>} */
  const actions = {
    ArrowUp: "zoom_in",
    ArrowDown: "zoom_out",
    ArrowLeft: "scroll_left",
    ArrowRight: "scroll_right",
  };
  const action = actions[e.key];
  if (!action) return;
  if (action === "zoom_out") setShowBookmark(state, true);

  const total = state.edit.distimes?.length ? state.edit.totalSamples || 0 : 0;
  if (!total) return;
  if (!state.edit.view) setEditView(state, { start: 0, end: total });
  const view = state.edit.view;
  if (!view) return;
  setEditView(state, adjustView(view, total, action));
  app.renderEditExplorer();
  e.preventDefault();
}
