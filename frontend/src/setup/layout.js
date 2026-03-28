/**
 * @typedef {import('../app/deps.js').LayoutSetupDeps} LayoutSetupDeps
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
    head.addEventListener("click", () => {
      head.parentElement.classList.toggle("collapsed");
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
