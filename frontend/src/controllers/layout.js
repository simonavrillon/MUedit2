let layoutRerenderTimer = null;
let layoutSettleTimer = null;
let layoutResizePolicyInitialized = false;

export function setSettingsOpen({ els, scheduleLayoutRerender }, open) {
  if (!els.workspace) return;
  const next = !!open;
  els.workspace.classList.toggle("settings-open", next);
  if (els.settingsToggleBtn) {
    els.settingsToggleBtn.setAttribute(
      "aria-expanded",
      next ? "true" : "false",
    );
  }
  scheduleLayoutRerender(0);
}

export function toggleSettingsOpen({ els, setSettingsOpen }) {
  if (!els.workspace) return;
  setSettingsOpen(!els.workspace.classList.contains("settings-open"));
}

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

  const path = document.createElementNS(ns, "path");
  path.setAttribute("stroke-linecap", "round");
  path.setAttribute("stroke-linejoin", "round");
  path.setAttribute("d", "M3.75 6.75h16.5M3.75 12h16.5m-16.5 5.25h16.5");
  svg.appendChild(path);
  els.settingsToggleBtn.appendChild(svg);

  if (!els.settingsToggleBtn.querySelector("svg path")) {
    els.settingsToggleBtn.textContent = "☰";
  }
}

export function rerenderPlotsForLayout({
  state,
  renderChannelQC,
  refreshVisuals,
  renderEditExplorer,
}) {
  renderChannelQC();
  refreshVisuals();
  if (state.edit.distimes?.length) {
    renderEditExplorer();
  }
}

export function scheduleLayoutRerender({ rerenderPlotsForLayout }, delay = 90) {
  if (layoutRerenderTimer) {
    clearTimeout(layoutRerenderTimer);
  }
  layoutRerenderTimer = window.setTimeout(() => {
    layoutRerenderTimer = null;
    // Two-pass draw: one pass during active resize, one on next frame after layout settles.
    window.requestAnimationFrame(() => {
      rerenderPlotsForLayout();
      window.requestAnimationFrame(() => rerenderPlotsForLayout());
    });
  }, delay);

  if (layoutSettleTimer) {
    clearTimeout(layoutSettleTimer);
  }
  // Final pass after CSS transitions / scrollbar changes are done.
  layoutSettleTimer = window.setTimeout(
    () => {
      layoutSettleTimer = null;
      window.requestAnimationFrame(() => rerenderPlotsForLayout());
    },
    Math.max(220, delay + 140),
  );
}

export function initLayoutResizePolicy({ els, scheduleLayoutRerender }) {
  if (layoutResizePolicyInitialized) return;
  layoutResizePolicyInitialized = true;

  const targets = [
    els.workspace,
    document.querySelector(".visual-panel"),
    els.stageQc,
    els.stageRun,
    els.stageEdit,
    ...document.querySelectorAll(
      ".stage-run .run-kpis, .stage-run .progress-row, .stage-run .chart-card, .stage-run .mu-controls",
    ),
    document.querySelector(".edit-top-row"),
    ...document.querySelectorAll(".stage-edit .edit-full"),
    els.settingsPanel,
  ].filter(Boolean);

  if ("ResizeObserver" in window && targets.length) {
    const resizeObserver = new ResizeObserver(() => {
      scheduleLayoutRerender(0);
    });
    targets.forEach((node) => resizeObserver.observe(node));
  }

  window.addEventListener("resize", () => {
    scheduleLayoutRerender(0);
  });
  window.addEventListener("orientationchange", () => {
    scheduleLayoutRerender(0);
  });
}
