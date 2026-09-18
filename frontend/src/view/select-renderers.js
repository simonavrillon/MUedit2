/** @typedef {{ value: number, label: string }} SelectOption */

/**
 * Render a paired grid/MU dropdown. Both option arrays use {value, label}
 * objects so callers normalize their model shape before calling.
 *
 * @param {HTMLSelectElement | null | undefined} gridSel
 * @param {HTMLSelectElement | null | undefined} muSel
 * @param {SelectOption[]} gridOptions
 * @param {SelectOption[]} muOptions
 * @param {number | undefined} selectedGrid
 * @param {number | undefined} selectedMu
 */
export function renderSelectPair(
  gridSel,
  muSel,
  gridOptions,
  muOptions,
  selectedGrid,
  selectedMu,
) {
  if (!gridSel || !muSel) return;

  gridSel.innerHTML = "";
  gridOptions.forEach((opt) => {
    const el = document.createElement("option");
    el.value = String(opt.value);
    el.textContent = opt.label;
    gridSel.appendChild(el);
  });
  gridSel.value = String(selectedGrid ?? 0);

  muSel.innerHTML = "";
  if (!muOptions.length) {
    const el = document.createElement("option");
    el.value = "";
    el.textContent = "No motor units";
    muSel.appendChild(el);
    muSel.disabled = true;
    return;
  }
  muSel.disabled = false;
  muOptions.forEach((opt) => {
    const el = document.createElement("option");
    el.value = String(opt.value);
    el.textContent = opt.label;
    muSel.appendChild(el);
  });
  muSel.value = String(selectedMu ?? muOptions[0].value);
}
