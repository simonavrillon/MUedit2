/**
 * Backend paths, relative to API_BASE; only `client.js` reads them, and
 * `tests/test_api_app.py` checks the backend serves each one. A dynamic
 * route is a function returning its path.
 */
export const routes = {
  seriesEmg: "/series/emg",
  seriesOverview: "/series/overview",
  seriesAux: "/series/aux",
  seriesPulse: "/series/pulse",
  qcAuto: "/qc/auto",
  qcSegment: "/qc/segment",
  previewByPath: "/preview-by-path",
  decomposeStream: "/decompose_stream",
  decomposeCancel: "/decompose/cancel",
  editSave: "/edit/save",
  editSessionOpen: "/edit/session/open",
  editSession: "/edit/session",
  editSessionRecover: "/edit/session/recover",
  editSessionPrepareGrid: "/edit/session/prepare-grid",
  editSessionSave: "/edit/session/save",
  editOp: (/** @type {string} */ op) => `/edit/ops/${op}`,
  dialogOpenFile: "/dialog/open-file",
  dialogDataFolder: "/dialog/data-folder",
  sessionClose: "/session/close",
  health: "/health",
  projects: "/projects",
};
