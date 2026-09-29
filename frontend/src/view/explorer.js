import { COLORS } from "../config.js";
import { drawTrace } from "./plots.js";
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
 * @param {Els} els
 * @param {RunMuExplorerModel | null | undefined} model
 */
export function renderMuExplorer(els, model) {
  if (!model) return;
  if (els.muMeta) {
    els.muMeta.textContent = model.metaText || "";
  }
  const trace = model.trace;
  drawTrace(els.muPulseCanvas || "muPulseCanvas", trace, model.view, {
    color: model.color,
    markers: trace
      ? [
          {
            positions: trace.spikes,
            values: trace.spikeValues,
            color: COLORS.secondary,
          },
        ]
      : [],
    showAxes: true,
    fsamp: model.fsamp,
    noDataText: "",
  });
}
