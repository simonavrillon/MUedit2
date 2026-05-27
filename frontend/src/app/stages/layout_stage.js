export function createLayoutStageService(deps) {
  const {
    ensureSettingsToggleIcon,
    toggleSettingsOpen,
    setSettingsOpen,
    initLayoutResizePolicy,
  } = deps;

  return {
    ensureSettingsToggleIcon,
    toggleSettingsOpen,
    setSettingsOpen,
    initLayoutResizePolicy,
  };
}

/**
 * @typedef {import('../deps.js').LayoutSetupDeps} LayoutSetupDeps
 */

/**
 * @param {LayoutSetupDeps} deps
 */
export function setupLayoutEvents(deps) {
  const { els, toggleSettingsOpen, setSettingsOpen, initLayoutResizePolicy } =
    deps;

  const sectionHeaders =
    els.settingsPanel?.querySelectorAll(".section-header") || [];
  sectionHeaders.forEach((head) => {
    head.setAttribute("tabindex", "0");
    const toggle = () => {
      const isCollapsed = head.parentElement.classList.toggle("collapsed");
      head.setAttribute("aria-expanded", isCollapsed ? "false" : "true");
    };
    head.addEventListener("click", toggle);
    head.addEventListener("keydown", (e) => {
      if (e.key === " " || e.key === "Enter") {
        e.preventDefault();
        toggle();
      }
    });
  });

  els.settingsToggleBtn?.addEventListener("click", () => toggleSettingsOpen());
  els.settingsOverlay?.addEventListener("click", () => setSettingsOpen(false));

  window.addEventListener("keydown", (e) => {
    if (
      e.key === "Escape" &&
      els.workspace?.classList.contains("settings-open")
    ) {
      setSettingsOpen(false);
    }
  });

  initLayoutResizePolicy();
}
