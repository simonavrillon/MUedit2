import { renderSelectPair } from "./select-renderers.js";

/** @typedef {import("../app/context.js").Els} Els */
/** @typedef {import("../decomp/explorer.js").RunMuDropdownModel} RunMuDropdownModel */
/** @typedef {import("../decomp/explorer.js").RunMuExplorerModel} RunMuExplorerModel */

/**
 * @param {Els} els
 * @param {RunMuDropdownModel | null | undefined} model
 */
export function renderMuDropdowns(els, model) {
  if (!model) return;
  renderSelectPair(
    els.muGridSelect,
    els.muSelect,
    model.gridOptions || [],
    model.muOptions || [],
    model.selectedGrid,
    model.selectedMu,
  );
}

/**
 * @param {{ els: Els, drawSeries: typeof import("./plots.js").drawSeries }} deps
 * @param {RunMuExplorerModel | null | undefined} model
 */
export function renderMuExplorer(deps, model) {
  const { els, drawSeries } = deps;
  if (!model) return;
  if (els.muMeta) {
    els.muMeta.textContent = model.metaText || "";
  }
  const pulseCanvas = els.muPulseCanvas || "muPulseCanvas";
  drawSeries(
    pulseCanvas,
    model.pulse || [],
    model.color,
    model.spikes || [],
    [],
    (model.pulse || []).length,
    model.view,
    model.markerVals || [],
    true,
    { showAxes: true, fsamp: model.fsamp, noDataText: "" },
  );
}
