import { renderSelectPair } from "./select-renderers.js";

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
