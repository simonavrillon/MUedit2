/**
 * Edit-stage logic without DOM: the pulse view's range, the grid/MU
 * dropdown model, discharge rates, and selections turned into edit requests.
 */
import { getEditMuIndicesForGrid, getEditView } from "../state/selectors.js";
import { traceRange } from "../signal/series.js";

/** @typedef {import("../state/state.js").State} State */
/** @typedef {import("../state/state.js").Selection} Selection */
/** @typedef {import("../app/context.js").App} App */
/** @typedef {import("../app/context.js").Span} Span */
/** @typedef {import("../app/context.js").RoiOp} RoiOp */
/** @typedef {ReturnType<typeof buildEditDropdownModel>} EditDropdownModel */

// --- Pulse view and dropdowns ---

/**
 * The window on screen and the value range of the current MU's pulse train
 * there, which map a drawn box to samples and pulse values. Null until that
 * window has been fetched for the MU as it is now.
 *
 * @param {State} state
 */
export function getPulseViewMeta(state) {
  const mu = state.edit.currentMu;
  const shown = state.edit.pulseView;
  if (!shown || shown.mu !== mu || shown.version !== state.edit.versions[mu]) {
    return null;
  }
  const totalSamples = state.edit.totalSamples;
  const view = getEditView(state);
  const start = Math.max(0, Math.min(totalSamples, view.start));
  const end = Math.max(start + 1, Math.min(totalSamples, view.end));
  const { min, max } = traceRange(shown);
  return { start, end, minVal: min, span: max - min || 1 };
}

/**
 * The dropdowns' model: which grid to select, which MU list to show, and
 * whether the current grid/MU needs switching. The caller applies the
 * switches and renders.
 *
 * @param {State} state
 */
export function buildEditDropdownModel(state) {
  const gridNames = state.gridNames;
  let currentGrid = state.edit.currentGrid;
  let mus = getEditMuIndicesForGrid(state, currentGrid);
  if (!mus.length) {
    for (let grid = 0; grid < gridNames.length; grid++) {
      const list = getEditMuIndicesForGrid(state, grid);
      if (list.length) {
        currentGrid = grid;
        mus = list;
        break;
      }
    }
  }
  const currentMu = state.edit.currentMu;
  const needsMuSwitch = mus.length > 0 && !mus.includes(currentMu);
  return {
    gridNames,
    currentGrid,
    muOptions: mus,
    currentMu: needsMuSwitch ? mus[0] : currentMu,
    needsGridSwitch: currentGrid !== state.edit.currentGrid,
    needsMuSwitch,
  };
}

// --- Discharge rates ---

/**
 * Rate (Hz, 0 when fsamp is unknown) of each positive inter-spike interval,
 * at its midpoint rounded to a sample inside `[0, totalSamples)`.
 *
 * @param {ArrayLike<number>} spikes
 * @param {number | null} fsamp
 * @param {number} totalSamples
 */
export function dischargeRates(spikes, fsamp, totalSamples) {
  /** @type {number[]} */
  const positions = [];
  /** @type {number[]} */
  const rates = [];
  for (let i = 0; i < spikes.length - 1; i++) {
    const isi = spikes[i + 1] - spikes[i];
    if (isi <= 0) continue;
    const at = Math.round(spikes[i] + isi / 2);
    positions.push(Math.min(totalSamples - 1, Math.max(0, at)));
    rates.push(fsamp ? fsamp / isi : 0);
  }
  return { positions, rates };
}

/**
 * Discharge rates by MU's discharge times. An edit replaces an MU's array, so
 * an entry outlives no change to it; redraws while panning reuse it.
 *
 * @type {WeakMap<ArrayLike<number>, { fsamp: number | null, totalSamples: number, rates: ReturnType<typeof dischargeRates> }>}
 */
const ratesCache = new WeakMap();

/**
 * `dischargeRates`, computed once per MU's discharge times.
 *
 * @param {ArrayLike<number>} spikes
 * @param {number | null} fsamp
 * @param {number} totalSamples
 */
export function cachedDischargeRates(spikes, fsamp, totalSamples) {
  const hit = ratesCache.get(spikes);
  if (hit && hit.fsamp === fsamp && hit.totalSamples === totalSamples) {
    return hit.rates;
  }
  const rates = dischargeRates(spikes, fsamp, totalSamples);
  ratesCache.set(spikes, { fsamp, totalSamples, rates });
  return rates;
}

/**
 * The fastest rate whose midpoint is in `view`: the top of the rate plot.
 *
 * @param {ReturnType<typeof dischargeRates>} rates
 * @param {Span} view
 */
export function fastestRateInView({ positions, rates }, view) {
  let fastest = 0;
  for (let i = 0; i < positions.length; i++) {
    if (positions[i] >= view.start && positions[i] < view.end) {
      fastest = Math.max(fastest, rates[i]);
    }
  }
  return fastest;
}

// --- Selections to edit requests (one per op, as the backend's session) ---

/**
 * The drawn box as an edit payload: samples clamped to the view, and its pixel
 * rows clamped to the plot as pulse values; null until the window on screen
 * has been fetched.
 *
 * @param {App} app
 * @param {Selection} sel
 */
function pulseBox(app, sel) {
  const meta = getPulseViewMeta(app.state);
  if (!meta) return null;
  const xStart = Math.max(meta.start, Math.min(meta.end, sel.start));
  const xEnd = Math.max(xStart + 1, Math.min(meta.end, sel.end));
  const height = app.getPulsePlotHeight();
  const y1 = Math.max(0, Math.min(height, sel.yMin));
  const y2 = Math.max(0, Math.min(height, sel.yMax));
  /** @param {number} y */
  const toValue = (y) => meta.minVal + (1 - y / height) * meta.span;
  return {
    mu: app.state.edit.currentMu,
    xStart,
    xEnd,
    yMin: toValue(Math.max(y1, y2)),
    yMax: toValue(Math.min(y1, y2)),
  };
}

/**
 * Send `op` on the drawn box; only `delete-spikes` takes the box's top.
 *
 * @param {App} app
 * @param {RoiOp} op
 * @param {Selection} sel
 */
function editInSelection(app, op, sel) {
  const box = pulseBox(app, sel);
  if (!box) return;
  const { mu, xStart, xEnd, yMin } = box;
  app.requestRoiEdit(
    op,
    op === "delete-spikes" ? box : { mu, xStart, xEnd, yMin },
  );
}

/**
 * @param {App} app
 * @param {Selection} sel
 */
export function addSpikesInSelection(app, sel) {
  editInSelection(app, "add-spikes", sel);
}

/**
 * @param {App} app
 * @param {Selection} sel
 */
export function addArtifactInSelection(app, sel) {
  editInSelection(app, "add-artifact", sel);
}

/**
 * @param {App} app
 * @param {Selection} sel
 */
export function deleteSpikesInSelection(app, sel) {
  editInSelection(app, "delete-spikes", sel);
}

/**
 * Place a `span`-wide view at `start`, shifted to stay inside [0, totalSamples].
 * @param {number} start
 * @param {number} span
 * @param {number} totalSamples
 * @returns {Span}
 */
export function clampView(start, span, totalSamples) {
  let s = start;
  let e = s + span;
  if (s < 0) {
    e -= s;
    s = 0;
  }
  if (e > totalSamples) {
    s = Math.max(0, s - (e - totalSamples));
    e = totalSamples;
  }
  return { start: s, end: e };
}
