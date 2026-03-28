export function getCurrentGrid(state) {
  return state.currentGrid || 0;
}

export function getRunCurrentMuGrid(state) {
  return state.currentMuGrid || 0;
}

export function getRunCurrentMu(state) {
  return Number.isFinite(state.currentMu) ? state.currentMu : 0;
}

export function getEditCurrentMuGrid(state) {
  return state.edit.currentMuGrid || 0;
}

export function getEditCurrentMu(state) {
  return Number.isFinite(state.edit.currentMu) ? state.edit.currentMu : 0;
}

export function getRunMuIndicesForGrid(state, gridIdx) {
  if (!state.muPulseTrains || !state.muPulseTrains.length) return [];
  const mapping = state.muGridIndex || [];
  if (!mapping.length) {
    return state.muPulseTrains.map((_, idx) => idx);
  }
  return mapping
    .map((g, idx) => ({ g, idx }))
    .filter((item) => Number(item.g) === Number(gridIdx))
    .map((item) => item.idx);
}

export function getEditMuIndicesForGrid(state, gridIdx) {
  const pulses = state.edit.pulseTrains || [];
  if (!pulses.length) return [];
  const mapping = state.edit.muGridIndex || [];
  if (!mapping.length) {
    return pulses.map((_, idx) => idx);
  }
  return mapping
    .map((g, idx) => ({ g, idx }))
    .filter((item) => Number(item.g) === Number(gridIdx))
    .map((item) => item.idx);
}
