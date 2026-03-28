export function renderBidsAutoInfo(els, model) {
  const box = els.bidsAutoInfo;
  if (!box) return;
  if (!model || model.hidden) {
    box.classList.add("hidden");
    return;
  }
  box.classList.remove("hidden");

  let html = "<b>Auto-detected Metadata</b>";
  if (model.deviceName) {
    html += `<div class="auto-info-item"><span>Device:</span> <span>${model.deviceName}</span></div>`;
  }
  if (model.musclesText) {
    html += `<div class="auto-info-item"><span>Muscles:</span> <span>${model.musclesText}</span></div>`;
  }
  if (model.filtersText) {
    html += `<div class="auto-info-item"><span>Filters:</span> <span>${model.filtersText}</span></div>`;
  }
  if (model.gainText) {
    html += `<div class="auto-info-item"><span>Amplifier Gain:</span> <span>${model.gainText}</span></div>`;
  }
  box.innerHTML = html;
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
