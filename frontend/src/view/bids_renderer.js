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
  // Manufacturer and device model now have editable fields — only show
  // filters and gain in the auto-info box.
  const hasInfo =
    model &&
    !model.hidden &&
    (model.musclesText || model.filtersText || model.gainText);
  if (!hasInfo) {
    box.classList.add("hidden");
    return;
  }
  box.classList.remove("hidden");
  box.innerHTML = "";

  const title = document.createElement("b");
  title.textContent = "Auto-detected Metadata";
  box.appendChild(title);

  if (model.musclesText)
    box.appendChild(makeInfoItem("Muscles:", model.musclesText));
  if (model.filtersText)
    box.appendChild(makeInfoItem("Filters:", model.filtersText));
  if (model.gainText)
    box.appendChild(makeInfoItem("Amplifier Gain:", model.gainText));
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

  // Reset to HTML defaults before applying parsed values so stale values
  // from a previously loaded file don't bleed into the new one.
  if (els.bidsSubject) els.bidsSubject.value = "1";
  if (els.bidsTask) els.bidsTask.value = "trapezoid";
  if (els.bidsSession) els.bidsSession.value = "1";
  if (els.bidsRun) els.bidsRun.value = "1";
  if (els.bidsProject) els.bidsProject.value = "";
  if (els.bidsPlacementScheme)
    els.bidsPlacementScheme.value = "ChannelSpecific";
  if (els.bidsPlacementDescription) els.bidsPlacementDescription.value = "";
  if (els.bidsPlacementDescRow)
    els.bidsPlacementDescRow.classList.add("hidden");
  if (els.bidsPowerlineFreq) els.bidsPowerlineFreq.value = "50";
  if (els.bidsManufacturer) els.bidsManufacturer.value = "";
  if (els.bidsDeviceModel) els.bidsDeviceModel.value = "";
  if (els.bidsTaskDescription) els.bidsTaskDescription.value = "";
  if (els.bidsParticipantAge) els.bidsParticipantAge.value = "";
  if (els.bidsParticipantSex) els.bidsParticipantSex.value = "";
  if (els.bidsParticipantHandedness) els.bidsParticipantHandedness.value = "";

  if (els.bidsSubject && payload.entities?.subject)
    els.bidsSubject.value = payload.entities.subject;
  if (els.bidsTask && payload.entities?.task)
    els.bidsTask.value = payload.entities.task;
  if (els.bidsSession && payload.entities?.session)
    els.bidsSession.value = payload.entities.session;
  if (els.bidsRun && payload.entities?.run)
    els.bidsRun.value = payload.entities.run;

  // Pre-fill participant fields from BIDS sidecar.
  if (els.bidsParticipantAge && payload.participant?.age)
    els.bidsParticipantAge.value = payload.participant.age;
  if (els.bidsParticipantSex && payload.participant?.sex)
    els.bidsParticipantSex.value = payload.participant.sex;
  if (els.bidsParticipantHandedness && payload.participant?.handedness)
    els.bidsParticipantHandedness.value = payload.participant.handedness;

  // Pre-fill hardware fields from BIDS sidecar or auto-detected loader metadata.
  if (
    els.bidsManufacturer &&
    (payload.hardware?.manufacturer || payload.autoInfo?.manufacturer)
  )
    els.bidsManufacturer.value =
      payload.hardware?.manufacturer || payload.autoInfo.manufacturer;
  if (
    els.bidsDeviceModel &&
    (payload.hardware?.deviceModel || payload.autoInfo?.deviceName)
  )
    els.bidsDeviceModel.value =
      payload.hardware?.deviceModel || payload.autoInfo.deviceName;

  // Pre-fill BIDS recording metadata round-tripped from the EMG JSON sidecar.
  if (
    els.bidsPowerlineFreq &&
    payload.bids?.powerlineFreq != null &&
    payload.bids.powerlineFreq !== ""
  )
    els.bidsPowerlineFreq.value = String(payload.bids.powerlineFreq);
  if (els.bidsPlacementScheme && payload.bids?.placementScheme)
    els.bidsPlacementScheme.value = payload.bids.placementScheme;
  if (els.bidsPlacementDescription && payload.bids?.placementDescription) {
    els.bidsPlacementDescription.value = payload.bids.placementDescription;
    els.bidsPlacementDescRow?.classList.remove("hidden");
  }
}
