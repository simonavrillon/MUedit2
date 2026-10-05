/**
 * The session form: the upload indicator, and the BIDS entity, participant
 * and hardware fields that travel with a run or a save. Every DOM read or
 * write of those fields goes through here.
 */
import {
  applyParticipantFields,
  applySessionInfoToDom,
  renderBidsAutoInfo as renderBidsAutoInfoView,
  renderBidsMuscleFields as renderBidsMuscleFieldsView,
  resetBidsEntityDefaults,
} from "../../view/bids-renderer.js";
import {
  buildBidsAutoInfoModel,
  buildBidsMuscleRowsModel,
  buildEntityLabelFromSession,
  buildSessionInfoFromDecomposition,
  listifyMuscles,
  parseBidsEntitiesFromLabel,
  safeBidsToken,
} from "../../io/bids.js";
import {
  setEditProject,
  setEditSoftwareVersions,
  setFsamp,
  setMuscle,
} from "../../state/actions.js";

/** @typedef {import("../context.js").App} App */
/** @typedef {import("../context.js").FileSessionService} FileSessionService */

/**
 * @param {App} app
 * @returns {import("../context.js").FileSessionService}
 */
export function createFileSessionService(app) {
  const { els, state, api } = app;

  /** @type {FileSessionService["getBidsProject"]} */
  function getBidsProject() {
    return (els.bidsProject?.value || "").trim();
  }

  /** @type {FileSessionService["setBidsProject"]} */
  function setBidsProject(project) {
    if (els.bidsProject) els.bidsProject.value = project;
    setEditProject(state, project);
  }

  /** @type {FileSessionService["resetSessionForm"]} */
  function resetSessionForm(fileName) {
    resetBidsEntityDefaults(els, fileName);
  }

  /** @type {FileSessionService["getBidsMuscleNames"]} */
  function getBidsMuscleNames() {
    const inputs = /** @type {NodeListOf<HTMLInputElement> | undefined} */ (
      els.bidsMuscleContainer?.querySelectorAll(".bids-muscle-input")
    );
    if (!inputs?.length) return [];
    return Array.from(inputs)
      .map((input) => String(input.value || "").trim())
      .filter(Boolean);
  }

  /** @type {FileSessionService["setUploadLoading"]} */
  function setUploadLoading(active) {
    if (!els.uploadLoader) return;
    els.uploadLoader.classList.toggle("hidden", !active);
  }

  /**
   * The session form as a run and a save both send it. The entity labels are
   * cleaned as BIDS allows them, so the raw EMG a run exports and the
   * decomposition saved next to it are named alike.
   */
  function readSessionForm() {
    const text = (/** @type {{ value: string } | null} */ field) =>
      String(field?.value || "").trim();
    const age = text(els.bidsParticipantAge);
    const sex = text(els.bidsParticipantSex);
    const handedness = text(els.bidsParticipantHandedness);
    return {
      subject: safeBidsToken(els.bidsSubject?.value),
      task: safeBidsToken(els.bidsTask?.value),
      session: safeBidsToken(els.bidsSession?.value),
      run: safeBidsToken(els.bidsRun?.value),
      acquisition: safeBidsToken(els.bidsAcquisition?.value),
      muscles: getBidsMuscleNames(),
      participantMeta:
        age || sex || handedness
          ? {
              age: age || "n/a",
              sex: sex || "n/a",
              handedness: handedness || "n/a",
            }
          : null,
      powerlineFreq: Number(text(els.bidsPowerlineFreq)) || 50,
      manufacturer: text(els.bidsManufacturer),
      deviceModel: text(els.bidsDeviceModel),
      placementScheme: text(els.bidsPlacementScheme) || "ChannelSpecific",
      placementDescription: text(els.bidsPlacementDescription),
    };
  }

  // The decompose endpoint's `bids_entities`: the fields that are filled in.
  /** @type {FileSessionService["collectBidsEntities"]} */
  function collectBidsEntities() {
    const form = readSessionForm();
    /** @type {Record<string, unknown>} */
    const fields = {
      subject: form.subject,
      task: form.task,
      session: form.session,
      run: form.run,
      acquisition: form.acquisition,
      target_muscle: form.muscles.length ? form.muscles : null,
      powerline_freq: form.powerlineFreq,
      manufacturer: form.manufacturer,
      manufacturers_model_name: form.deviceModel,
      placement_scheme: form.placementScheme,
      placement_scheme_description: form.placementDescription,
      participant_meta: form.participantMeta,
    };
    return Object.fromEntries(
      Object.entries(fields).filter(([, value]) => value),
    );
  }

  /** @type {FileSessionService["setBidsEntitiesInput"]} */
  function setBidsEntitiesInput(entities) {
    if (els.bidsSubject && entities.subject)
      els.bidsSubject.value = entities.subject;
    if (els.bidsTask && entities.task) els.bidsTask.value = entities.task;
    if (els.bidsSession && entities.session)
      els.bidsSession.value = entities.session;
    if (els.bidsAcquisition && entities.acq)
      els.bidsAcquisition.value = entities.acq;
    if (els.bidsRun && entities.run) els.bidsRun.value = entities.run;
    if (entities.project) setBidsProject(entities.project);
  }

  /** @type {FileSessionService["applyPreviewMetadata"]} */
  function applyPreviewMetadata(data) {
    if (els.fsamp) {
      const fs = Number(data.fsamp);
      els.fsamp.value =
        Number.isFinite(fs) && fs > 0 ? String(Math.round(fs)) : "";
    }
    applyParticipantFields(els, data?.participant_meta || {});
    // The project folder the file lies in under the output folder, when it does.
    if (typeof data?.project === "string" && data.project) {
      setBidsEntitiesInput({ project: data.project });
    }
    if (els.bidsManufacturer && data?.manufacturer)
      els.bidsManufacturer.value = data.manufacturer;
    if (els.bidsDeviceModel && data?.manufacturers_model_name)
      els.bidsDeviceModel.value = data.manufacturers_model_name;
  }

  /** @type {FileSessionService["applySessionInfoFromDecomposition"]} */
  function applySessionInfoFromDecomposition(file, data = {}) {
    const payload = buildSessionInfoFromDecomposition(file, data, {
      parseBidsEntitiesFromLabel,
      listifyMuscles,
    });
    applySessionInfoToDom(els, payload);
    setMuscle(state, payload.muscles);
    setEditSoftwareVersions(state, payload.bids?.softwareVersions ?? null);
    setFsamp(state, payload.fsampText);
  }

  /** @type {FileSessionService["renderBidsAutoInfo"]} */
  function renderBidsAutoInfo() {
    const model = buildBidsAutoInfoModel(state);
    renderBidsAutoInfoView(els, model);
    // Pre-fill editable hardware fields from loader metadata when empty.
    if (model && !model.hidden) {
      if (
        els.bidsManufacturer &&
        !els.bidsManufacturer.value &&
        model.manufacturer
      )
        els.bidsManufacturer.value = model.manufacturer;
      if (els.bidsDeviceModel && !els.bidsDeviceModel.value && model.deviceName)
        els.bidsDeviceModel.value = model.deviceName;
      const meta = state.metadata || {};
      if (
        els.bidsPowerlineFreq &&
        !els.bidsPowerlineFreq.value &&
        meta.powerline_freq
      )
        els.bidsPowerlineFreq.value = String(meta.powerline_freq);
    }
  }

  /** @type {FileSessionService["renderBidsMuscleFields"]} */
  function renderBidsMuscleFields() {
    renderBidsMuscleFieldsView(els, buildBidsMuscleRowsModel(state));
  }

  /** The session fields a run cannot start without; Acquisition and Run are optional. */
  function requiredSessionFields() {
    const muscles = /** @type {HTMLInputElement[]} */ (
      Array.from(
        els.bidsMuscleContainer?.querySelectorAll(".bids-muscle-input") || [],
      )
    );
    return [
      els.bidsProject,
      els.bidsSubject,
      els.bidsSession,
      els.bidsTask,
      ...muscles,
      els.bidsParticipantAge,
      els.bidsParticipantSex,
      els.bidsParticipantHandedness,
      els.bidsManufacturer,
      els.bidsDeviceModel,
    ].filter((field) => !!field);
  }

  /** @type {FileSessionService["checkSessionForm"]} */
  function checkSessionForm() {
    const fields = requiredSessionFields();
    const missing = fields.filter((field) => !field.value.trim());
    for (const field of fields) {
      if (missing.includes(field)) field.setAttribute("aria-invalid", "true");
      else field.removeAttribute("aria-invalid");
    }
    if (!missing.length) return true;
    app.setSettingsOpen(true);
    const section = missing[0].closest(".panel-section");
    section?.classList.remove("collapsed");
    section
      ?.querySelector(".section-header")
      ?.setAttribute("aria-expanded", "true");
    missing[0].focus();
    return false;
  }

  // The BIDS entity label from the session form, else the payload's, and
  // the participant and hardware fields every save carries.
  /** @type {FileSessionService["withBidsSaveFields"]} */
  function withBidsSaveFields(payload) {
    const form = readSessionForm();
    const entityLabel =
      buildEntityLabelFromSession({
        subject: form.subject,
        task: form.task,
        session: form.session,
        run: form.run,
        acq: form.acquisition,
      }) || payload.entity_label;
    return {
      ...payload,
      entity_label: entityLabel,
      project: getBidsProject(),
      participant_meta: form.participantMeta,
      powerline_freq: form.powerlineFreq,
      manufacturer: form.manufacturer || null,
      manufacturers_model_name: form.deviceModel || null,
      placement_scheme: form.placementScheme,
      placement_scheme_description: form.placementDescription || null,
    };
  }

  /** @type {FileSessionService["persistNpzBySaveTarget"]} */
  async function persistNpzBySaveTarget(payload, fallbackName) {
    const data = await api.editSave(
      withBidsSaveFields({
        ...payload,
        file_label: payload.file_label || fallbackName || "decomposition.npz",
      }),
    );
    return { path: data.path || "" };
  }

  return {
    getBidsProject,
    setBidsProject,
    resetSessionForm,
    getBidsMuscleNames,
    collectBidsEntities,
    checkSessionForm,
    setBidsEntitiesInput,
    applyPreviewMetadata,
    applySessionInfoFromDecomposition,
    renderBidsAutoInfo,
    renderBidsMuscleFields,
    persistNpzBySaveTarget,
    withBidsSaveFields,
    setUploadLoading,
  };
}
