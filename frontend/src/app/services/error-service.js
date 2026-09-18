/**
 * Logs an error and surfaces it through a status callback.
 * Centralises the console.error + setStatus("...", "error") pattern used
 * across service and domain modules, and provides a single integration point
 * for future error-tracking tools (e.g. Sentry).
 *
 * @param {unknown} err
 * @param {(text: string, tone?: import("../context.js").Tone) => void} setStatus
 * @param {string} label  Human-readable action name, e.g. "ROI failed"
 */
export function handleError(err, setStatus, label) {
  console.error(err);
  setStatus(`${label}: ${errorMessage(err)}`, "error");
}

/**
 * The message of a caught value, which need not be an Error.
 *
 * @param {unknown} err
 */
export function errorMessage(err) {
  return err instanceof Error ? err.message : String(err);
}
