export function getCurrentGrid(state) {
  return state.currentGrid || 0;
}

// Return the MU indices belonging to a given grid. When no grid mapping is
// available, every MU is treated as belonging to the requested grid.
function muIndicesForGrid(pulseTrains, mapping, gridIdx) {
  const pulses = pulseTrains || [];
  if (!pulses.length) return [];
  const map = mapping || [];
  if (!map.length) {
    return pulses.map((_, idx) => idx);
  }
  return map
    .map((g, idx) => ({ g, idx }))
    .filter((item) => Number(item.g) === Number(gridIdx))
    .map((item) => item.idx);
}

export function getRunMuIndicesForGrid(state, gridIdx) {
  return muIndicesForGrid(state.muPulseTrains, state.muGridIndex, gridIdx);
}

export function getEditMuIndicesForGrid(state, gridIdx) {
  return muIndicesForGrid(
    state.edit.pulseTrains,
    state.edit.muGridIndex,
    gridIdx,
  );
}
