/**
 * Small DOM controls the stages share: on/off toggle buttons, and buttons
 * that show they are busy while their action runs.
 */

/**
 * @param {HTMLElement} btn
 * @param {boolean} on
 */
function setToggleState(btn, on) {
  btn.dataset.state = on ? "on" : "off";
  btn.setAttribute("aria-pressed", on ? "true" : "false");
  btn.classList.toggle("on", on);
}

/**
 * @param {HTMLElement} btn
 * @param {boolean} on
 */
function applyToggle(btn, on) {
  const label = (btn.textContent || "").split(":")[0];
  setToggleState(btn, on);
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
 * @param {string} prefix
 */
export function applyLabeledToggle(btn, on, prefix) {
  if (!btn) return;
  setToggleState(btn, on);
  const label = on ? "On" : "Off";
  const shortEl = btn.querySelector(".toggle-short");
  const fullEl = btn.querySelector(".toggle-full");
  if (shortEl) shortEl.textContent = label;
  if (fullEl) fullEl.textContent = `${prefix}: ${label}`;
}

/**
 * Make `btn` an on/off toggle, reporting its state now and on each change.
 *
 * @param {HTMLElement | null | undefined} btn
 * @param {(on: boolean) => void} [onChange]
 */
export function setupToggle(btn, onChange) {
  if (!btn) return;
  applyToggle(btn, isToggleOn(btn));
  onChange?.(isToggleOn(btn));
  btn.addEventListener("click", () => {
    const next = !isToggleOn(btn);
    applyToggle(btn, next);
    onChange?.(next);
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
