import { positionStepIndicator } from "./navigation.js";

/** @typedef {import("../context.js").App} App */

/**
 * @param {App} app
 * @param {boolean} open
 */
export function setSettingsOpen(app, open) {
  const { els } = app;
  if (!els.workspace) return;
  const next = !!open;
  els.workspace.classList.toggle("settings-open", next);
  if (els.settingsToggleBtn) {
    els.settingsToggleBtn.setAttribute(
      "aria-expanded",
      next ? "true" : "false",
    );
  }
  app.scheduleLayoutRerender();
}

/** @param {App} app */
export function toggleSettingsOpen(app) {
  const { els } = app;
  if (!els.workspace) return;
  app.setSettingsOpen(!els.workspace.classList.contains("settings-open"));
}

/** @param {App["els"]} els */
export function ensureSettingsToggleIcon(els) {
  if (!els.settingsToggleBtn) return;
  els.settingsToggleBtn.setAttribute("aria-label", "Toggle settings panel");
  els.settingsToggleBtn.replaceChildren();
  const ns = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(ns, "svg");
  svg.setAttribute("xmlns", ns);
  svg.setAttribute("fill", "none");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("stroke-width", "1.5");
  svg.setAttribute("stroke", "currentColor");
  svg.setAttribute("aria-hidden", "true");
  svg.setAttribute("width", "24");
  svg.setAttribute("height", "24");

  const rect = document.createElementNS(ns, "rect");
  rect.setAttribute("x", "3.75");
  rect.setAttribute("y", "3.75");
  rect.setAttribute("width", "16.5");
  rect.setAttribute("height", "16.5");
  rect.setAttribute("rx", "2");
  svg.appendChild(rect);

  const path = document.createElementNS(ns, "path");
  path.setAttribute("stroke-linecap", "round");
  path.setAttribute("stroke-linejoin", "round");
  path.setAttribute("d", "M9.75 3.75v16.5");
  svg.appendChild(path);
  els.settingsToggleBtn.appendChild(svg);
}

/**
 * Redraw the visible stage whenever a canvas or the channel grid changes size.
 * Hidden stages are display:none, so their canvases have no size, and
 * entering a stage schedules its own redraw.
 *
 * @param {App} app
 */
export function initLayoutResizePolicy(app) {
  const { els } = app;
  const targets = [
    els.emgCanvas,
    els.auxCanvas,
    els.qcSection,
    els.editPulseCanvas,
    els.editDrCanvas,
    els.editTimelineCanvas,
  ].filter((node) => node != null);
  const resizeObserver = new ResizeObserver(() => app.scheduleLayoutRerender());
  targets.forEach((node) => resizeObserver.observe(node));

  window.addEventListener("resize", () => positionStepIndicator(els));
  window.addEventListener("orientationchange", () =>
    positionStepIndicator(els),
  );
}
