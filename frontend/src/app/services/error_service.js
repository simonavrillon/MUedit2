/**
 * Logs an error and surfaces it through a status callback.
 * Centralises the console.error + setStatus("...", "error") pattern used
 * across service and domain modules, and provides a single integration point
 * for future error-tracking tools (e.g. Sentry).
 *
 * @param {unknown} err
 * @param {(msg: string, level: string) => void} setStatus
 * @param {string} label  Human-readable action name, e.g. "ROI failed"
 */
export function handleError(err, setStatus, label) {
  console.error(err);
  const message = err instanceof Error ? err.message : String(err);
  setStatus(`${label}: ${message}`, "error");
}
