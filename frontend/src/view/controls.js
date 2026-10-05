/**
 * Small DOM controls the stages share: on/off toggle buttons, and buttons
 * that show they are busy while their action runs.
 */

/**
 * @param {HTMLElement | null | undefined} btn
 * @param {boolean} on
 */
function applyToggle(btn, on) {
  if (!btn) return;
  const label =
    btn.dataset.label || (btn.textContent || "").split(":")[0] || "Toggle";
  btn.dataset.state = on ? "on" : "off";
  btn.setAttribute("aria-pressed", on ? "true" : "false");
  btn.classList.toggle("on", on);
  btn.textContent = `${label}: ${on ? "On" : "Off"}`;
}

/** @param {HTMLElement | null | undefined} btn */
export function isToggleOn(btn) {
  return btn?.dataset.state === "on";
}

/**
 * Set a toggle whose short and full labels are separate elements.
 *
 * @param {HTMLElement | null | undefined} btn
 * @param {boolean} on
 * @param {{ shortSel: string, fullSel: string, prefix: string }} labels
 */
export function applyLabeledToggle(btn, on, { shortSel, fullSel, prefix }) {
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

/**
 * @param {string} id
 * @param {boolean} show
 */
export function toggleConditional(id, show) {
  document.getElementById(id)?.classList.toggle("hidden", !show);
}

/**
 * Make `btn` an on/off toggle, reporting its state now and on each change.
 *
 * @param {HTMLElement | null | undefined} btn
 * @param {(on: boolean) => void} [onChange]
 */
export function setupToggle(btn, onChange) {
  if (!btn) return;
  btn.setAttribute("tabindex", "0");
  applyToggle(btn, isToggleOn(btn));
  onChange?.(isToggleOn(btn));
  btn.addEventListener("click", () => {
    const next = !isToggleOn(btn);
    applyToggle(btn, next);
    onChange?.(next);
  });
  btn.addEventListener("keydown", (e) => {
    if (e.key === " " || e.key === "Enter") {
      e.preventDefault();
      btn.click();
    }
  });
}

/**
 * A toggle shown on that cannot be turned off.
 *
 * @param {HTMLElement | null | undefined} btn
 * @param {(on: boolean) => void} [onChange]
 */
export function setupLockedOnToggle(btn, onChange) {
  if (!btn) return;
  btn.setAttribute("tabindex", "0");
  btn.setAttribute("aria-disabled", "true");
  btn.title = "This filter is always enabled";
  applyToggle(btn, true);
  onChange?.(true);
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

/**
 * @param {HTMLElement | null | undefined} button
 * @param {boolean} busy
 */
export function setEditActionBusy(button, busy) {
  if (!button) return;
  button.classList.toggle("is-running", !!busy);
  button.setAttribute("aria-busy", busy ? "true" : "false");
}

/**
 * Run `fn` with `button` shown busy; a click while it runs is ignored.
 *
 * @template T
 * @param {HTMLElement | null | undefined} button
 * @param {() => T | Promise<T>} fn
 * @returns {Promise<T | undefined>}
 */
export async function runEditAction(button, fn) {
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
