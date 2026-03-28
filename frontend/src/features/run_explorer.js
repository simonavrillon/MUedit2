import { UNIFORM_PULSE_COLOR } from "../config.js";

export function buildRunMuDropdownModel({ state, getMuIndicesForGridFn }) {
  const gridOptions = (state.gridNames || []).map((name, idx) => ({
    value: idx,
    label: `Grid ${idx + 1}${name ? ` • ${name}` : ""}`,
  }));

  let targetGrid = state.currentMuGrid || 0;
  let mus = getMuIndicesForGridFn(targetGrid);
  if (!mus.length && state.gridNames?.length) {
    for (let g = 0; g < state.gridNames.length; g++) {
      const list = getMuIndicesForGridFn(g);
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

export function buildRunMuExplorerModel({ state, fsamp = null }) {
  const allPulses = Array.isArray(state.muPulseTrains) ? state.muPulseTrains : [];
  const currentMu = Number(state.currentMu);
  let muIdx =
    Number.isFinite(currentMu) && currentMu >= 0 ? Math.floor(currentMu) : 0;
  if (!allPulses[muIdx]?.length && allPulses.length) {
    const firstWithPulse = allPulses.findIndex(
      (pulseRow) => Array.isArray(pulseRow) && pulseRow.length > 0,
    );
    if (firstWithPulse >= 0) muIdx = firstWithPulse;
  }
  const pulse = allPulses?.[muIdx] || [];
  const spikes = state.muDistimes?.[muIdx] || [];
  const view = state.runView || { start: 0, end: pulse.length || 0 };
  const nextView =
    !state.runView || (pulse && state.runView.end > pulse.length)
      ? { start: 0, end: pulse.length || 0 }
      : null;
  const selectionOverlay = state.muSelectionRange
    ? [state.muSelectionRange]
    : [];
  const metaText =
    pulse && pulse.length ? `${spikes?.length || 0} discharge times` : "";
  const color = UNIFORM_PULSE_COLOR;
  const markerVals = spikes.map((s) => pulse?.[s] ?? 0);

  return {
    muIdx,
    pulse,
    spikes,
    view: nextView || view,
    nextView,
    selectionOverlay,
    metaText,
    color,
    markerVals,
    fsamp: Number.isFinite(fsamp) && fsamp > 0 ? fsamp : null,
  };
}
