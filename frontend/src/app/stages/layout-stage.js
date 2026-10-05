import {
  ensureSettingsToggleIcon,
  initLayoutResizePolicy,
  toggleSettingsOpen,
} from "../services/layout.js";

/** @typedef {import("../context.js").App} App */

/** @param {App} app */
export function setupLayoutEvents(app) {
  const { els, setSettingsOpen } = app;

  ensureSettingsToggleIcon(els);

  /** @type {NodeListOf<HTMLElement> | HTMLElement[]} */
  const sectionHeaders =
    els.settingsPanel?.querySelectorAll(".section-header") || [];
  sectionHeaders.forEach((head) => {
    head.setAttribute("tabindex", "0");
    const toggle = () => {
      const section = head.parentElement;
      if (!section) return;
      // A click on a rail icon expands the panel and opens that section.
      if (!els.workspace?.classList.contains("settings-open")) {
        setSettingsOpen(true);
        section.classList.remove("collapsed");
        head.setAttribute("aria-expanded", "true");
        return;
      }
      const isCollapsed = section.classList.toggle("collapsed");
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

  els.settingsToggleBtn?.addEventListener("click", () =>
    toggleSettingsOpen(app),
  );
  els.settingsOverlay?.addEventListener("click", () => setSettingsOpen(false));

  window.addEventListener("keydown", (e) => {
    if (
      e.key === "Escape" &&
      els.workspace?.classList.contains("settings-open")
    ) {
      setSettingsOpen(false);
    }
  });

  initLayoutResizePolicy(app);
}
