export function renderMuDropdowns(els, model) {
  const gridSel = els.muGridSelect;
  const muSel = els.muSelect;
  if (!gridSel || !muSel || !model) return;

  gridSel.innerHTML = "";
  (model.gridOptions || []).forEach((optDef) => {
    const opt = document.createElement("option");
    opt.value = String(optDef.value);
    opt.textContent = optDef.label;
    gridSel.appendChild(opt);
  });
  gridSel.value = String(model.selectedGrid ?? 0);

  muSel.innerHTML = "";
  if (!model.muOptions?.length) {
    const opt = document.createElement("option");
    opt.value = "";
    opt.textContent = "No motor units";
    muSel.appendChild(opt);
    muSel.disabled = true;
    return;
  }
  muSel.disabled = false;
  model.muOptions.forEach((optDef) => {
    const opt = document.createElement("option");
    opt.value = String(optDef.value);
    opt.textContent = optDef.label;
    muSel.appendChild(opt);
  });
  muSel.value = String(model.selectedMu ?? model.muOptions[0].value);
}

export function renderMuExplorer({ els, drawSeries }, model) {
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
    model.selectionOverlay || [],
    (model.pulse || []).length,
    model.view,
    model.markerVals || [],
    true,
    { showAxes: true, fsamp: model.fsamp, noDataText: "" },
  );
}
