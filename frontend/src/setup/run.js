import { setRunCurrentMu, setRunCurrentMuGrid } from "../state/actions.js";

/**
 * @typedef {import('../app/deps.js').RunSetupDeps} RunSetupDeps
 */

/**
 * @param {RunSetupDeps} deps
 */
export function setupRunEvents(deps) {
  const {
    els,
    state,
    runDecomposition,
    enableRoiSelection,
    syncRois,
    refreshVisuals,
    setupToggle,
    setupLockedOnToggle,
    toggleConditional,
    updateStartAvailability,
    renderAuxiliaryChannels,
    renderMuExplorer,
  } = deps;

  els.start?.addEventListener("click", runDecomposition);

  enableRoiSelection("emgCanvas");

  if (els.nwindows) {
    els.nwindows.addEventListener("change", () => {
      const nwin = Number(els.nwindows.value) || 1;
      syncRois(nwin);
      refreshVisuals();
    });
  }

  setupToggle(els.peelOffToggle, (on) =>
    toggleConditional("peelOffSettings", on),
  );
  setupToggle(els.useAdaptiveToggle);
  setupToggle(els.covToggle, (on) => toggleConditional("covSettings", on));
  setupLockedOnToggle(els.silToggle, (on) =>
    toggleConditional("silSettings", on),
  );
  updateStartAvailability();

  els.auxSelector?.addEventListener("change", () => renderAuxiliaryChannels());

  els.muGridSelect?.addEventListener("change", (e) => {
    const idx = Number(e.target.value) || 0;
    setRunCurrentMuGrid(state, idx, { resetView: true });
    renderMuExplorer();
  });

  els.muSelect?.addEventListener("change", (e) => {
    const idx = Number(e.target.value);
    setRunCurrentMu(state, idx, { resetView: true });
    renderMuExplorer();
  });
}
