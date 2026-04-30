// In desktop mode the frontend and API share the same origin (same port).
// In web dev mode the frontend runs on :8080 and the API on :8000, so
// MUEDIT_API_BASE can be set to override (e.g. "http://localhost:8000/api/v1").
export const API_BASE =
  window.MUEDIT_API_BASE ||
  (window.location.port === "8080"
    ? `${window.location.protocol}//${window.location.hostname}:8000/api/v1`
    : `${window.location.origin}/api/v1`);

export const DEFAULT_BIDS_ROOT = window.MUEDIT_BIDS_ROOT || "../data/bids_out";

export const COLORS = {
  primary: "#ffffff",
  secondary: "#ffd43b",
  warning: "#ffd43b",
  muted: "#b7b7b7",
  muPurple: "#e7c1ff",
  gridEmpty: "#555",
  roiFill: "rgba(242, 167, 185, 0.08)",
  roiStroke: "rgba(242, 167, 185, 0.6)",
  selectionFill: "rgba(195, 155, 242, 0.08)",
  selectionStroke: "rgba(195, 155, 242, 0.6)",
  gridLineDim: "rgba(255, 255, 255, 0.08)",
  gridAxis: "rgba(255, 255, 255, 0.15)",
};

export const GRID_COLORS = [
  "#ffffff",
  "#f5f5f5",
  "#e8e8e8",
  "#dcdcdc",
  "#3776ab",
  "#ffd43b",
  "#2ca02c",
];

export const RAW_SIGNAL_EXTENSIONS = [".mat", ".otb+", ".otb4", ".bdf", ".edf"];
export const DECOMPOSITION_EXTENSIONS = [".npz"];

export const UNIFORM_PULSE_COLOR = "#f5f5f5";
