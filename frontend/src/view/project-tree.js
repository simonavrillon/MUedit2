/**
 * Project tree renderer: the output folder's projects, each a row with the
 * Projects header's folder icon, then "+ New project…". The picked row is
 * marked current; each row holds its project's name ("" for a new one).
 * While a decomposition is open for editing, every row is disabled.
 */

/** @typedef {import("../app/dom.js").Els} Els */

/**
 * @param {Els} els
 * @param {string[]} projects
 * @param {string} picked
 * @param {boolean} locked
 */
export function renderProjectTree(els, projects, picked, locked) {
  const tree = els.projectTree;
  if (!tree) return;
  const icon = els.projectsHeader?.querySelector("svg");
  const row = (/** @type {string} */ name, /** @type {string} */ label) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "tree-project";
    button.dataset.project = name;
    button.title = label;
    button.disabled = locked;
    if (name && icon) button.append(icon.cloneNode(true));
    const text = document.createElement("span");
    text.textContent = label;
    button.append(text);
    if (name === picked) button.setAttribute("aria-current", "true");
    return button;
  };
  tree.replaceChildren(
    ...projects.map((name) => row(name, name)),
    row("", "+ New project…"),
  );
}
