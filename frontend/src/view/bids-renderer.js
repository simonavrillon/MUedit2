/**
 * Draws the BIDS session form: entity defaults, the fields a file's BIDS
 * sidecars fill, and muscle rows. Reads and writes the DOM only.
 */
import { naToEmpty } from "../io/bids.js";

/** @typedef {import("../app/context.js").Els} Els */
/** @typedef {import("../app/context.js").JsonObject} JsonObject */
/** @typedef {import("../io/bids.js").BidsMuscleRows} BidsMuscleRows */

/**
 * @param {Els} els
 * @param {string | null} [fileName]
 */
export function resetBidsEntityDefaults(els, fileName) {
  if (els.bidsSubject) els.bidsSubject.value = "1";
  if (els.bidsSession) els.bidsSession.value = "1";
  if (els.bidsAcquisition) els.bidsAcquisition.value = "";
  if (els.bidsRun) els.bidsRun.value = "";
  if (els.bidsTask) {
    els.bidsTask.value = "trapezoid";
    delete els.bidsTask.dataset.keep;
  }
  if (els.fileName && fileName) els.fileName.textContent = fileName;
}

/**
 * @param {Els} els
 * @param {BidsMuscleRows} rows
 */
export function renderBidsMuscleFields(els, rows) {
  const container = els.bidsMuscleContainer;
  if (!container) return;
  container.innerHTML = "";
  rows.forEach((rowDef) => {
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

/**
 * Clear the participant, hardware and placement fields to their HTML
 * defaults, so a file that records none doesn't show the previous file's.
 *
 * @param {Els} els
 */
export function resetSidecarFields(els) {
  if (els.bidsPlacementScheme) els.bidsPlacementScheme.value = "Other";
  if (els.bidsPlacementDescription) els.bidsPlacementDescription.value = "";
  if (els.bidsReferenceLocation) els.bidsReferenceLocation.value = "";
  if (els.bidsGroundLocation) els.bidsGroundLocation.value = "";
  if (els.bidsPowerlineFreq) els.bidsPowerlineFreq.value = "50";
  if (els.bidsManufacturer) els.bidsManufacturer.value = "";
  if (els.bidsDeviceModel) els.bidsDeviceModel.value = "";
  if (els.bidsParticipantAge) els.bidsParticipantAge.value = "";
  if (els.bidsParticipantSex) els.bidsParticipantSex.value = "";
  if (els.bidsParticipantHandedness) els.bidsParticipantHandedness.value = "";
}

/**
 * Fill the participant, hardware and placement fields a file's BIDS sidecars
 * record, as the backend sends them; fields it leaves out keep their value.
 *
 * @param {Els} els
 * @param {JsonObject} data
 */
export function applySidecarFields(els, data) {
  const participant = data.participant_meta;
  if (participant) {
    if (els.bidsParticipantAge && participant.age != null)
      els.bidsParticipantAge.value = naToEmpty(participant.age);
    if (els.bidsParticipantSex && participant.sex != null)
      els.bidsParticipantSex.value = naToEmpty(participant.sex);
    if (els.bidsParticipantHandedness && participant.handedness != null)
      els.bidsParticipantHandedness.value = naToEmpty(participant.handedness);
  }
  if (els.bidsPowerlineFreq && Number(data.powerline_freq) > 0)
    els.bidsPowerlineFreq.value = String(data.powerline_freq);
  /** @type {[HTMLInputElement | HTMLSelectElement | null | undefined, unknown][]} */
  const fields = [
    [els.bidsManufacturer, data.manufacturer],
    [els.bidsDeviceModel, data.manufacturers_model_name],
    [els.bidsPlacementScheme, data.placement_scheme],
    [els.bidsPlacementDescription, data.placement_scheme_description],
    [els.bidsReferenceLocation, data.reference_location],
    [els.bidsGroundLocation, data.ground_location],
  ];
  for (const [field, value] of fields)
    if (field && value) field.value = String(value);
  syncPlacementDescriptionRow(els);
}

/**
 * The sampling rate, rounded to whole hertz; blank when unknown.
 *
 * @param {Els} els
 * @param {unknown} fsamp
 */
export function showFsamp(els, fsamp) {
  if (!els.fsamp) return;
  const n = Number(fsamp);
  els.fsamp.value = Number.isFinite(n) && n > 0 ? String(Math.round(n)) : "";
}

/**
 * The description row shows with the Other scheme, which requires it.
 *
 * @param {Els} els
 */
export function syncPlacementDescriptionRow(els) {
  if (els.bidsPlacementDescRow)
    els.bidsPlacementDescRow.hidden =
      (els.bidsPlacementScheme?.value || "Other") !== "Other";
}
