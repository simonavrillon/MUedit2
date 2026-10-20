/**
 * The session form: the upload indicator, and the BIDS entity, participant
 * and hardware fields that travel with a run or a save. Every DOM read or
 * write of those fields goes through here.
 */
import {
  applyParticipantFields,
  applySessionInfoToDom,
  renderBidsMuscleFields as renderBidsMuscleFieldsView,
  resetBidsEntityDefaults,
} from "../../view/bids-renderer.js";
import {
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

/** The project last picked in the Project field, kept across visits. */
const LAST_PROJECT_KEY = "muedit.project";

/**
 * @param {App} app
 * @returns {import("../context.js").FileSessionService}
 */
export function createFileSessionService(app) {
  const { els, state, api } = app;

  // The "New project…" option has the empty value: its name is typed below.
  /** @type {FileSessionService["getBidsProject"]} */
  function getBidsProject() {
    const project = els.bidsProject?.value || els.bidsProjectNew?.value || "";
    return project.trim();
  }

  /** @type {FileSessionService["setBidsProject"]} */
  function setBidsProject(project) {
    const select = els.bidsProject;
    if (select) {
      const listed = Array.from(select.options, (option) => option.value);
      if (project && !listed.includes(project)) {
        select.add(new Option(project, project), select.options.length - 1);
      }
      select.value = project;
    }
    if (els.bidsProjectNew) els.bidsProjectNew.value = "";
    syncBidsProject();
  }

  /** @type {FileSessionService["syncBidsProject"]} */
  function syncBidsProject() {
    const picked = els.bidsProject?.value || "";
    els.bidsProjectNewRow?.classList.toggle("hidden", !!picked);
    setEditProject(state, getBidsProject());
    try {
      if (picked) localStorage.setItem(LAST_PROJECT_KEY, picked);
    } catch {
      // storage blocked: the field starts on the first project next time
    }
  }

  /** @type {FileSessionService["loadProjects"]} */
  async function loadProjects() {
    const { projects = [] } = await api.listProjects();
    let last = "";
    try {
      last = localStorage.getItem(LAST_PROJECT_KEY) || "";
    } catch {
      // storage blocked
    }
    els.bidsProject?.replaceChildren(
      ...projects.map((/** @type {string} */ name) => new Option(name, name)),
      new Option("+ New project…", ""),
    );
    setBidsProject(projects.includes(last) ? last : projects[0] || "");
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
      powerlineFreq: Number(text(els.bidsPowerlineFreq)) || null,
      manufacturer: text(els.bidsManufacturer),
      deviceModel: text(els.bidsDeviceModel),
      placementScheme: text(els.bidsPlacementScheme) || "Other",
      placementDescription: text(els.bidsPlacementDescription),
      referenceLocation: text(els.bidsReferenceLocation),
      groundLocation: text(els.bidsGroundLocation),
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
      reference_location: form.referenceLocation,
      ground_location: form.groundLocation,
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
    if (els.bidsTask && entities.task) {
      els.bidsTask.value = entities.task;
      els.bidsTask.dataset.keep = "1";
    }
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
      const fsamp = Number(data.fsamp);
      els.fsamp.value =
        Number.isFinite(fsamp) && fsamp > 0 ? String(Math.round(fsamp)) : "";
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
    // A recording in a BIDS dataset brings the placement its sidecar records.
    const meta = data?.metadata || {};
    const scheme = data?.placement_scheme || meta.placement_scheme;
    if (els.bidsPlacementScheme && scheme) {
      els.bidsPlacementScheme.value = String(scheme);
      els.bidsPlacementDescRow?.classList.toggle("hidden", scheme !== "Other");
    }
    /** @type {[HTMLInputElement | null | undefined, unknown][]} */
    const placement = [
      [
        els.bidsPlacementDescription,
        data?.placement_scheme_description || meta.placement_scheme_description,
      ],
      [els.bidsReferenceLocation, data?.reference_location],
      [els.bidsGroundLocation, data?.ground_location],
    ];
    for (const [field, value] of placement)
      if (field && value) field.value = String(value);
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

  /** @type {FileSessionService["prefillHardwareFields"]} */
  function prefillHardwareFields() {
    const meta = state.metadata || {};
    if (
      els.bidsManufacturer &&
      !els.bidsManufacturer.value &&
      meta.manufacturer
    )
      els.bidsManufacturer.value = String(meta.manufacturer);
    if (els.bidsDeviceModel && !els.bidsDeviceModel.value && meta.device_name)
      els.bidsDeviceModel.value = String(meta.device_name);
    if (
      els.bidsPowerlineFreq &&
      !els.bidsPowerlineFreq.value &&
      Number(meta.powerline_freq) > 0
    )
      els.bidsPowerlineFreq.value = String(meta.powerline_freq);
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
      els.bidsProject?.value ? els.bidsProject : els.bidsProjectNew,
      els.bidsSubject,
      els.bidsSession,
      els.bidsTask,
      ...muscles,
      els.bidsParticipantAge,
      els.bidsParticipantSex,
      els.bidsParticipantHandedness,
      els.bidsManufacturer,
      els.bidsDeviceModel,
      ...placementFields(),
    ].filter((field) => !!field);
  }

  /** The placement fields BIDS requires: a description when the scheme is Other. */
  function placementFields() {
    const scheme = els.bidsPlacementScheme?.value.trim() || "Other";
    return scheme === "Other" && els.bidsPlacementDescription
      ? [els.bidsPlacementDescription]
      : [];
  }

  /** @type {FileSessionService["checkSessionForm"]} */
  function checkSessionForm() {
    return markMissing(requiredSessionFields());
  }

  /** @type {FileSessionService["checkPlacementForm"]} */
  function checkPlacementForm() {
    return markMissing(placementFields());
  }

  /**
   * Whether every field in `fields` is filled; if not, mark the empty ones
   * and open the panel on the first.
   *
   * @param {(HTMLInputElement | HTMLSelectElement)[]} fields
   */
  function markMissing(fields) {
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
      reference_location: form.referenceLocation || null,
      ground_location: form.groundLocation || null,
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
    syncBidsProject,
    loadProjects,
    resetSessionForm,
    getBidsMuscleNames,
    collectBidsEntities,
    checkSessionForm,
    checkPlacementForm,
    setBidsEntitiesInput,
    applyPreviewMetadata,
    applySessionInfoFromDecomposition,
    prefillHardwareFields,
    renderBidsMuscleFields,
    persistNpzBySaveTarget,
    withBidsSaveFields,
    setUploadLoading,
  };
}
