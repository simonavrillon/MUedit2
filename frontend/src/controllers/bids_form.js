function makeInfoItem(label, value) {
  const row = document.createElement("div");
  row.className = "auto-info-item";
  const l = document.createElement("span");
  l.textContent = label;
  const v = document.createElement("span");
  v.textContent = value;
  row.appendChild(l);
  row.appendChild(v);
  return row;
}

export function renderBidsAutoInfo(els, model) {
  const box = els.bidsAutoInfo;
  if (!box) return;
  if (!model || model.hidden) {
    box.classList.add("hidden");
    return;
  }
  box.classList.remove("hidden");
  box.innerHTML = "";

  const title = document.createElement("b");
  title.textContent = "Auto-detected Metadata";
  box.appendChild(title);

  if (model.deviceName) box.appendChild(makeInfoItem("Device:", model.deviceName));
  if (model.musclesText) box.appendChild(makeInfoItem("Muscles:", model.musclesText));
  if (model.filtersText) box.appendChild(makeInfoItem("Filters:", model.filtersText));
  if (model.gainText) box.appendChild(makeInfoItem("Amplifier Gain:", model.gainText));
}

export function renderBidsMuscleFields(els, rows) {
  const container = els.bidsMuscleContainer;
  if (!container) return;
  container.innerHTML = "";
  const safeRows =
    Array.isArray(rows) && rows.length
      ? rows
      : [{ id: "bidsMuscle_0", label: "Muscle", value: "" }];
  safeRows.forEach((rowDef) => {
    const row = document.createElement("div");
    row.className = "form-row";
    const label = document.createElement("label");
    label.textContent = rowDef.label;
    const input = document.createElement("input");
    input.type = "text";
    input.id = rowDef.id;
    input.className = "bids-muscle-input";
    input.placeholder = "e.g. tibialis anterior";
    input.value = rowDef.value || "";
    row.appendChild(label);
    row.appendChild(input);
    container.appendChild(row);
  });
}

export function applySessionInfoToDom(els, payload) {
  if (!payload) return;
  if (els.fileName) {
    els.fileName.textContent = payload.fileLabel || "decomposition";
    els.fileName.classList.remove("loading");
  }
  if (els.fsamp && payload.fsampText) {
    els.fsamp.value = payload.fsampText;
  }
  if (els.bidsSubject && payload.entities?.subject)
    els.bidsSubject.value = payload.entities.subject;
  if (els.bidsTask && payload.entities?.task)
    els.bidsTask.value = payload.entities.task;
  if (els.bidsSession) els.bidsSession.value = payload.entities?.session || "";
  if (els.bidsRun && payload.entities?.run)
    els.bidsRun.value = payload.entities.run;
}
