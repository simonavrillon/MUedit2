import { POSTPROCESS_MODES, DEFAULT_POSTPROCESS_MODE } from "./params.js";

/** @typedef {import("../app/context.js").JsonObject} JsonObject */
/** @typedef {import("../state/state.js").State} State */

/** @typedef {"load" | "preprocess" | "decompose" | "postprocess" | "save"} RunPhase */
/** @typedef {"running" | "done" | "failed"} RunStatus */

/** The pipeline's phases, in order, as the phase track shows them. */
export const RUN_PHASES = /** @type {const} */ ([
  { key: "load", label: "Load" },
  { key: "preprocess", label: "Filter" },
  { key: "decompose", label: "Decompose" },
  { key: "postprocess", label: "Post-process" },
  { key: "save", label: "Save" },
]);

/** What became of one iteration of the search, as its dot shows it. */
export const DOT = /** @type {const} */ ({
  pending: 0,
  kept: 1,
  rejected: 2,
  fewSpikes: 3,
  skipped: 4,
});

/** @type {Record<string, number>} */
const OUTCOME_CODES = { k: DOT.kept, r: DOT.rejected, f: DOT.fewSpikes };

/** @type {Record<string, RunPhase>} */
const PHASE_OF_EVENT = {
  load: "load",
  preprocess: "preprocess",
  decompose: "decompose",
  postprocess: "postprocess",
  export: "save",
};

/**
 * One grid × window of the search: a dot per iteration.
 *
 * @typedef {object} RunRow
 * @property {number} grid
 * @property {number} window
 * @property {Uint8Array} dots DOT codes, one per iteration.
 * @property {number} done Iterations accounted for (run or skipped).
 */

/**
 * The finished run, from its `done` event.
 *
 * @typedef {object} RunSummary
 * @property {number} muCount Units kept after post-processing.
 * @property {number[]} perGrid
 * @property {number[]} muGridIndex
 * @property {number | null} meanSil
 */

/**
 * A decomposition as the run page follows it.
 *
 * @typedef {object} RunLive
 * @property {RunStatus} status
 * @property {RunPhase} phase
 * @property {number} startedAt Epoch ms.
 * @property {number | null} searchStartedAt When the first window began.
 * @property {number | null} finishedAt
 * @property {number} niter
 * @property {RunRow[]} rows
 * @property {number[]} keptByGrid Units the search kept so far, before post-processing.
 * @property {string} error
 * @property {RunSummary | null} summary
 * @property {boolean} saving
 * @property {string} savedPath
 * @property {string} saveError
 */

/**
 * @param {number} now
 * @returns {RunLive}
 */
export function createRunLive(now) {
  return {
    status: "running",
    phase: "load",
    startedAt: now,
    searchStartedAt: null,
    finishedAt: null,
    niter: 0,
    rows: [],
    keptByGrid: [],
    error: "",
    summary: null,
    saving: false,
    savedPath: "",
    saveError: "",
  };
}

/** @param {RunLive} live */
export function keptTotal(live) {
  return live.keptByGrid.reduce((a, b) => a + b, 0);
}

/**
 * Lay out one row per grid × window the first time the search reports its shape.
 *
 * @param {RunLive} live
 * @param {JsonObject} msg
 */
function ensureRows(live, msg) {
  const ngrid = Number(msg.ngrid) || 0;
  const nwindows = Number(msg.nwindows) || 0;
  const niter = Number(msg.niter) || 0;
  if (live.rows.length || !ngrid || !nwindows || !niter) return;
  live.niter = niter;
  live.keptByGrid = new Array(ngrid).fill(0);
  for (let grid = 0; grid < ngrid; grid++) {
    for (let window = 0; window < nwindows; window++) {
      live.rows.push({ grid, window, dots: new Uint8Array(niter), done: 0 });
    }
  }
}

/**
 * Fold one stream event into `live`.
 *
 * @param {RunLive} live
 * @param {JsonObject} msg
 * @param {number} now
 * @returns {{ row: number, from: number, to: number } | null} The dots it changed.
 */
export function applyRunEvent(live, msg, now) {
  const phase = PHASE_OF_EVENT[msg.phase];
  if (phase) live.phase = phase;
  if (phase !== "decompose") return null;

  ensureRows(live, msg);
  if (live.searchStartedAt === null) live.searchStartedAt = now;
  const nwindows = Number(msg.nwindows) || 1;
  const rowIdx = Number(msg.grid) * nwindows + Number(msg.window);
  const row = live.rows[rowIdx];
  if (!row) return null;

  const outcomes = typeof msg.outcomes === "string" ? msg.outcomes : "";
  const iter = Math.min(live.niter, Number(msg.iter) || 0);
  const from = Math.max(0, iter - outcomes.length);
  for (let k = 0; k < outcomes.length && from + k < live.niter; k++) {
    const code = OUTCOME_CODES[outcomes[k]] ?? DOT.rejected;
    row.dots[from + k] = code;
    if (code === DOT.kept) live.keptByGrid[row.grid] += 1;
  }
  let to = iter;
  // A window whose basis ran out stops early; its remaining dots never run.
  if (msg.window_done && iter < live.niter) {
    row.dots.fill(DOT.skipped, iter);
    to = live.niter;
  }
  row.done = Math.max(row.done, to);
  return { row: rowIdx, from, to };
}

/**
 * The run ended without a result.
 *
 * @param {RunLive} live
 * @param {string} message
 * @param {number} now
 */
export function failRun(live, message, now) {
  live.status = "failed";
  live.error = message;
  live.finishedAt = now;
}

/**
 * The run ended with its result; saving it is next.
 *
 * @param {RunLive} live
 * @param {RunSummary | null} summary The `done` event's, when it has one.
 * @param {number} now
 */
export function finishRun(live, summary, now) {
  if (summary) live.summary = summary;
  live.phase = "save";
  live.status = "done";
  live.finishedAt = now;
}

/** @param {RunLive} live */
export function startSave(live) {
  live.saving = true;
  live.saveError = "";
}

/**
 * @param {RunLive} live
 * @param {string} path Where the decomposition was saved.
 */
export function finishSave(live, path) {
  live.saving = false;
  live.savedPath = path;
}

/**
 * @param {RunLive} live
 * @param {string} message
 */
export function failSave(live, message) {
  live.saving = false;
  live.saveError = message;
}

/**
 * Seconds left in the search, from its pace so far; null until there is one.
 *
 * @param {RunLive} live
 * @param {number} now
 */
export function searchSecondsLeft(live, now) {
  if (live.phase !== "decompose" || live.searchStartedAt === null) return null;
  const total = live.rows.length * live.niter;
  const done = live.rows.reduce((sum, row) => sum + row.done, 0);
  const elapsed = (now - live.searchStartedAt) / 1000;
  if (!total || done < Math.min(10, total) || elapsed < 3) return null;
  return Math.max(0, ((total - done) * elapsed) / done);
}

/**
 * "1:05", "12:40", "1:02:03".
 *
 * @param {number} seconds
 */
export function formatClock(seconds) {
  const s = Math.max(0, Math.round(seconds));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const ss = String(s % 60).padStart(2, "0");
  return h ? `${h}:${String(m).padStart(2, "0")}:${ss}` : `${m}:${ss}`;
}

/**
 * "about 2 min", "under a minute".
 *
 * @param {number} seconds
 */
export function formatRemaining(seconds) {
  if (seconds < 60) return "under a minute";
  return `about ${Math.round(seconds / 60)} min`;
}

/**
 * @param {JsonObject} summary The `done` event's summary.
 * @param {JsonObject | null | undefined} preview Its preview metadata.
 * @returns {RunSummary}
 */
export function buildRunSummary(summary, preview) {
  const muCount = Math.max(0, Number(summary.mu_count) || 0);
  const muGridIndex = (
    Array.isArray(preview?.mu_grid_index) ? preview.mu_grid_index : []
  ).map((g) => Math.max(0, Number(g) || 0));
  const ngrid = Math.max(
    Array.isArray(summary.grid_names) ? summary.grid_names.length : 0,
    muGridIndex.length ? Math.max(...muGridIndex) + 1 : 0,
    muCount > 0 ? 1 : 0,
  );
  const perGrid = new Array(ngrid).fill(0);
  for (let mu = 0; mu < muCount; mu++) {
    const g = muGridIndex[mu] ?? 0;
    perGrid[g < ngrid ? g : 0] += 1;
  }
  const sil = (Array.isArray(summary.sil) ? summary.sil : [])
    .map(Number)
    .filter(Number.isFinite);
  const meanSil = sil.length
    ? sil.reduce((a, b) => a + b, 0) / sil.length
    : null;
  return { muCount, perGrid, muGridIndex, meanSil };
}

/**
 * The settings a run is about to use, as the page lists them before it starts.
 *
 * @param {State} state
 * @param {import("./params.js").DecomposeParams} params
 * @param {string} postprocessMode
 * @returns {{ label: string, value: string }[]}
 */
export function buildRunPlan(state, params, postprocessMode) {
  const fs = Number(state.fsamp) || 0;
  const rois = state.rois || [];
  const gridCount = (state.gridNames || []).length;
  const grids = (state.gridNames || []).map((name, idx) => {
    const mask = state.discardMasks?.[idx] || [];
    const kept = mask.filter((d) => !d).length;
    const channels = mask.length ? ` (${kept}/${mask.length} ch)` : "";
    return `${name || `Grid ${idx + 1}`}${channels}`;
  });
  const roiSeconds = fs
    ? rois.reduce((sum, r) => sum + Math.max(0, r.end - r.start), 0) / fs
    : 0;
  const windows = `${rois.length || 1}${roiSeconds ? ` · ${roiSeconds.toFixed(1)} s` : ""}`;
  const searches = gridCount * Math.max(1, rois.length);
  const mode =
    POSTPROCESS_MODES[postprocessMode] ||
    POSTPROCESS_MODES[DEFAULT_POSTPROCESS_MODE];
  const artifacts = (state.artifactRegions || []).length;
  return [
    { label: "Grids", value: grids.join(", ") || "None" },
    { label: "Windows", value: windows },
    {
      label: "Iterations",
      value: `${params.niter} per window${searches > 1 ? ` · ${params.niter * searches} total` : ""}`,
    },
    { label: "Silhouette threshold", value: String(params.sil_thr) },
    {
      label: "CoV filter",
      value: params.covfilter ? `On · ≤ ${params.cov_thr}` : "Off",
    },
    {
      label: "Peel-off",
      value: params.peel_off_enabled
        ? `On · ${Math.round(params.peel_off_win * 1000)} ms`
        : "Off",
    },
    { label: "Post-processing", value: mode.label },
    ...(artifacts
      ? [{ label: "Artifact windows", value: String(artifacts) }]
      : []),
  ];
}
