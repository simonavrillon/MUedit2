/**
 * Central API endpoint path table. Callers compose the full URL by
 * prepending API_BASE (injected via deps): `${API_BASE}${routes.seriesEmg}`.
 * Dynamic routes are functions that return the path segment.
 */
export const routes = {
  seriesEmg: "/series/emg",
  seriesOverview: "/series/overview",
  seriesAux: "/series/aux",
  seriesPulse: "/series/pulse",
  qcAuto: "/qc/auto",
  previewByPath: "/preview-by-path",
  decomposeStream: "/decompose_stream",
  decomposeCancel: "/decompose/cancel",
  decomposePreview: (/** @type {string} */ token) =>
    `/decompose_preview/${encodeURIComponent(token)}`,
  editSave: "/edit/save",
  editSessionOpen: "/edit/session/open",
  editSession: "/edit/session",
  editSessionRecover: "/edit/session/recover",
  editSessionPrepareGrid: "/edit/session/prepare-grid",
  editSessionSave: "/edit/session/save",
  editOp: (/** @type {string} */ op) => `/edit/ops/${op}`,
  dialogOpenFile: "/dialog/open-file",
  sessionClose: "/session/close",
  health: "/health",
};
