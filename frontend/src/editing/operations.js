import { resetEditSlice } from "../state/actions.js";
import { traceRange } from "../signal/series.js";

/** @typedef {import("../state/state.js").State} State */
/** @typedef {import("../state/state.js").Selection} Selection */
/** @typedef {import("../app/context.js").Span} Span */
/** @typedef {ReturnType<typeof buildEditDropdownModel>} EditDropdownModel */
/** @typedef {NonNullable<ReturnType<typeof getPulseViewMeta>>} PulseViewMeta */

/** @typedef {import("../app/context.js").App} App */

// --- State helpers ---

/**
 * The window on screen and the value range of the current MU's pulse train
 * there, which map a drawn box to samples and pulse values. Null until that
 * window has been fetched for the MU as it is now.
 *
 * @param {State} state
 */
export function getPulseViewMeta(state) {
  const muIdx = state.edit.currentMu ?? 0;
  const shown = state.edit.pulseView;
  if (
    !shown ||
    shown.mu !== muIdx ||
    shown.version !== state.edit.versions?.[muIdx]
  ) {
    return null;
  }
  const total = state.edit.totalSamples || 0;
  const view = state.edit.view || { start: 0, end: total };
  const s = Math.max(0, Math.min(total, view.start));
  const e = Math.max(s + 1, Math.min(total, view.end));
  const { min, max } = traceRange(shown);
  return { s, e, minVal: min, maxVal: max, span: max - min || 1 };
}

// Compute the dropdown model: which grid to select, which MU list to show,
// and whether the current grid/MU needs switching. Pure logic — no DOM,
// no state mutation. The caller applies mutations and renders.
/**
 * @param {State} state
 * @param {(gridIdx: number) => number[]} getEditMuIndices
 */
export function buildEditDropdownModel(state, getEditMuIndices) {
  const gridNames = state.edit.gridNames || [];
  let targetGrid = state.edit.currentMuGrid || 0;
  let mus = getEditMuIndices(targetGrid);
  if (!mus.length && gridNames.length) {
    for (let g = 0; g < gridNames.length; g++) {
      const list = getEditMuIndices(g);
      if (list.length) {
        targetGrid = g;
        mus = list;
        break;
      }
    }
  }
  const currentMu = state.edit.currentMu;
  const needsMuSwitch = mus.length && !mus.includes(currentMu);
  return {
    gridNames,
    targetGrid,
    muOptions: mus,
    currentMu: needsMuSwitch ? mus[0] : currentMu,
    needsGridSwitch: targetGrid !== (state.edit.currentMuGrid || 0),
    needsMuSwitch,
  };
}

/** @param {App} app */
export function resetEditState(app) {
  const { state, refreshEditModeButtons } = app;
  resetEditSlice(state);
  refreshEditModeButtons();
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
 * The fastest rate whose midpoint is in `view`: the top of the rate plot.
 *
 * @param {{ positions: number[], rates: number[] }} dr
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

// --- Selection coordinators (bridge canvas coordinates → API actions) ---

/**
 * The drawn box in samples, clamped to the view, and its pixel rows clamped
 * to the plot; null until the window on screen has been fetched.
 *
 * @param {App} app
 * @param {Selection} sel
 */
function pulseBox(app, sel) {
  const meta = getPulseViewMeta(app.state);
  if (!meta) return null;
  const { s, e } = meta;
  const start = Math.max(s, Math.min(e, sel.start));
  const end = Math.max(start + 1, Math.min(e, sel.end));
  const height = app.getPulsePlotHeight();
  const y1 = Math.max(0, Math.min(height, sel.yMin ?? 0));
  const y2 = Math.max(0, Math.min(height, sel.yMax ?? height));
  /** @param {number} y */
  const toValue = (y) => meta.minVal + (1 - y / height) * meta.span;
  return {
    start,
    end,
    top: toValue(Math.min(y1, y2)),
    low: toValue(Math.max(y1, y2)),
  };
}

/**
 * @param {App} app
 * @param {Selection} sel
 */
export function addSpikesInSelection(app, sel) {
  const box = pulseBox(app, sel);
  if (!box) return;
  app.requestRoiEdit("add-spikes", {
    muIdx: app.state.edit.currentMu ?? 0,
    xStart: box.start,
    xEnd: box.end,
    yMin: box.low,
  });
}

/**
 * @param {App} app
 * @param {Selection} sel
 */
export function addArtifactInSelection(app, sel) {
  const box = pulseBox(app, sel);
  if (!box) return;
  app.requestRoiEdit("add-artifact", {
    muIdx: app.state.edit.currentMu ?? 0,
    xStart: box.start,
    xEnd: box.end,
    yMin: box.low,
  });
}

/**
 * @param {App} app
 * @param {Selection} sel
 */
export function deleteSpikesInSelection(app, sel) {
  const box = pulseBox(app, sel);
  if (!box) return;
  app.requestRoiEdit("delete-spikes", {
    muIdx: app.state.edit.currentMu ?? 0,
    xStart: box.start,
    xEnd: box.end,
    yMin: box.low,
    yMax: box.top,
  });
}

/**
 * @param {App} app
 * @param {Selection} sel
 */
export function deleteDrInSelection(app, sel) {
  const { state, getDrPlotHeight, requestRoiEdit } = app;

  const muIdx = state.edit.currentMu ?? 0;
  const spikes = state.edit.distimes?.[muIdx] || [];
  if (spikes.length < 2) return;
  const height = getDrPlotHeight();
  const yLowPx = Math.max(sel.yMin ?? 0, sel.yMax ?? height);
  const total = state.edit.totalSamples || 0;
  // Same scale the rate plot draws: 0 Hz at the bottom, the fastest rate in view on top.
  const fastest = fastestRateInView(
    dischargeRates(spikes, state.edit.fsamp, total),
    state.edit.view || { start: 0, end: total },
  );
  requestRoiEdit("delete-dr", {
    muIdx,
    xStart: Math.min(sel.start, sel.end),
    xEnd: Math.max(sel.start, sel.end),
    yMin: (1 - yLowPx / height) * (fastest || 1),
  });
}
