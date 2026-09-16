/**
 * Central API endpoint path table. Callers compose the full URL by
 * prepending API_BASE (injected via deps): `${API_BASE}${routes.qcWindow}`.
 * Dynamic routes are functions that return the path segment.
 */
export const routes = {
  qcWindow: "/qc/window",
  qcAuto: "/qc/auto",
  previewByPath: "/preview-by-path",
  preview: "/preview",
  decomposeStream: "/decompose_stream",
  decomposePreview: (token) =>
    `/decompose_preview/${encodeURIComponent(token)}`,
  editSave: "/edit/save",
  editAction: (action) => `/edit/${action}`,
  editMode: (mode) => `/edit/${mode}`,
  editRemoveOutliers: "/edit/remove-outliers",
  editRemoveDuplicates: "/edit/remove-duplicates",
  editFlagMu: "/edit/flag-mu",
  editLoadByPath: "/edit/load-by-path",
  editLoad: "/edit/load",
  dialogOpenFile: "/dialog/open-file",
  health: "/health",
};
