export function getCurrentGrid(state) {
  return state.currentGrid || 0;
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
