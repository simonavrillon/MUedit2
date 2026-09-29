import { UNIFORM_PULSE_COLOR } from "../config.js";

/** @typedef {import("../app/state.js").State} State */
/** @typedef {ReturnType<typeof buildRunMuDropdownModel>} RunMuDropdownModel */
/** @typedef {ReturnType<typeof buildRunMuExplorerModel>} RunMuExplorerModel */

/**
 * @param {{ state: State, getMuIndicesForGrid: (gridIdx: number) => number[] }} deps
 */
export function buildRunMuDropdownModel(deps) {
  const { state, getMuIndicesForGrid } = deps;
  const gridOptions = (state.gridNames || []).map((name, idx) => ({
    value: idx,
    label: `Grid ${idx + 1}${name ? ` • ${name}` : ""}`,
  }));

  let targetGrid = state.currentMuGrid || 0;
  let mus = getMuIndicesForGrid(targetGrid);
  if (!mus.length && state.gridNames?.length) {
    for (let g = 0; g < state.gridNames.length; g++) {
      const list = getMuIndicesForGrid(g);
      if (list.length) {
        targetGrid = g;
        mus = list;
        break;
      }
    }
  }

  const muOptions = mus.map((muIdx) => ({
    value: muIdx,
    label: `MU ${muIdx + 1}`,
  }));
  const selectedMu = mus.includes(state.currentMu) ? state.currentMu : mus[0];

  return {
    gridOptions,
    selectedGrid: targetGrid,
    muOptions,
    selectedMu,
  };
}

/**
 * What the run explorer draws: the current MU, its window and, once fetched,
 * that window of its pulse train (`trace`, null until then).
 *
 * @param {{ state: State, fsamp?: number | null }} deps
 */
export function buildRunMuExplorerModel(deps) {
  const { state, fsamp = null } = deps;
  const distimes = Array.isArray(state.muDistimes) ? state.muDistimes : [];
  const total = Number(state.seriesLength) || 0;
  const currentMu = Number(state.currentMu);
  let muIdx =
    Number.isFinite(currentMu) && currentMu >= 0 ? Math.floor(currentMu) : 0;
  if (muIdx >= distimes.length) muIdx = 0;
  const spikes = distimes[muIdx] || new Int32Array(0);
  const nextView =
    !state.runView || state.runView.end > total
      ? { start: 0, end: total }
      : null;
  const pulse = state.runPulseView;
  const hasMu = distimes.length > 0 && total > 0;
  return {
    muIdx,
    spikes,
    total,
    view:
      nextView ||
      /** @type {import("../app/context.js").Span} */ (state.runView),
    nextView,
    metaText: hasMu ? `${spikes.length} discharge times` : "",
    color: UNIFORM_PULSE_COLOR,
    trace: hasMu && pulse && pulse.mu === muIdx ? pulse : null,
    fsamp: fsamp != null && Number.isFinite(fsamp) && fsamp > 0 ? fsamp : null,
  };
}
