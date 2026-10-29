/**
 * The session form: the upload indicator, and the BIDS entity, participant
 * and hardware fields that travel with a run or a save. Every DOM read or
 * write of those fields, and every event binding on them, goes through here.
 */
import {
  applySidecarFields,
  renderBidsMuscleFields as renderBidsMuscleFieldsFeature,
  resetBidsEntityDefaults,
  resetSidecarFields,
  showFsamp,
  syncPlacementDescriptionRow,
} from "../../view/bids-renderer.js";
import {
  buildBidsMuscleRowsModel,
  buildEntityLabelFromSession,
  parseBidsEntitiesFromLabel,
  safeBidsToken,
} from "../../io/bids.js";
import {
  setEditProject,
  setEditSoftwareVersions,
  setFsamp,
  setMuscleNames,
} from "../../state/actions.js";
import { renderProjectTree } from "../../view/project-tree.js";
import { handleError } from "./error-service.js";

/** @typedef {import("../context.js").App} App */
/** @typedef {import("../context.js").FileSessionService} FileSessionService */

/** The project last picked in the project tree, kept across visits. */
const LAST_PROJECT_KEY = "muedit.project";

/**
 * @param {App} app
 * @returns {import("../context.js").FileSessionService}
 */
export function createFileSessionService(app) {
  const { els, state, api } = app;

  /** @type {string[]} The projects the tree lists. */
  let projects = [];
  // "" is "New project…": its name is typed below the tree.
  let picked = "";
  // A decomposition open for editing saves back where it came from.
  let locked = false;

  /** @type {FileSessionService["getBidsProject"]} */
  function getBidsProject() {
    return (picked || els.bidsProjectNew?.value || "").trim();
  }

  /** @type {FileSessionService["setBidsProject"]} */
  function setBidsProject(project) {
    if (project && !projects.includes(project))
      projects = [...projects, project];
    picked = project;
    if (els.bidsProjectNew) els.bidsProjectNew.value = "";
    renderProjectTree(els, projects, picked, locked);
    syncBidsProject();
  }

  /** @type {FileSessionService["syncBidsProject"]} */
  function syncBidsProject() {
    if (els.bidsProjectNewRow) els.bidsProjectNewRow.hidden = !!picked;
    setEditProject(state, getBidsProject());
    try {
      if (picked) localStorage.setItem(LAST_PROJECT_KEY, picked);
    } catch {
      // storage blocked: the field starts on the first project next time
    }
  }

  /** @type {FileSessionService["loadProjects"]} */
  async function loadProjects() {
    const { projects: listed = [], data_root: dataRoot } =
      await api.listProjects();
    if (els.dataFolderHint && dataRoot) {
      els.dataFolderHint.textContent = `Projects are saved in ${dataRoot}.`;
    }
    let last = "";
    try {
      last = localStorage.getItem(LAST_PROJECT_KEY) || "";
    } catch {
      // storage blocked
    }
    projects = listed;
    setBidsProject(listed.includes(last) ? last : listed[0] || "");
  }

  /** @type {FileSessionService["setProjectLocked"]} */
  function setProjectLocked(value) {
    locked = value;
    if (els.bidsProjectNew) els.bidsProjectNew.disabled = locked;
    if (els.dataFolderBtn) els.dataFolderBtn.disabled = locked;
    // The rows drawn already; a later redraw takes `locked` from here.
    const rows = els.projectTree?.querySelectorAll(".tree-project") ?? [];
    for (const row of rows) {
      /** @type {HTMLButtonElement} */ (row).disabled = locked;
    }
  }

  /** @type {FileSessionService["onChooseDataFolder"]} */
  async function onChooseDataFolder() {
    let dataRoot;
    try {
      ({ data_root: dataRoot } = await api.chooseDataFolder());
    } catch (err) {
      handleError(err, app.setStatus, "Output folder");
      return;
    }
    if (!dataRoot) return;
    try {
      await loadProjects();
    } catch (err) {
      handleError(err, app.setStatus, "Projects");
      return;
    }
    app.setStatus(`The output folder is now ${dataRoot}.`, "success");
  }

  /** @type {FileSessionService["resetSessionForm"]} */
  function resetSessionForm(fileName) {
    setProjectLocked(false);
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
    els.uploadLoader.hidden = !active;
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
      muscleNames: getBidsMuscleNames(),
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
      target_muscle: form.muscleNames.length ? form.muscleNames : null,
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
    if (els.bidsAcquisition && entities.acquisition)
      els.bidsAcquisition.value = entities.acquisition;
    if (els.bidsRun && entities.run) els.bidsRun.value = entities.run;
    if (entities.project) setBidsProject(entities.project);
  }

  /** @type {FileSessionService["applyPreviewMetadata"]} */
  function applyPreviewMetadata(data) {
    showFsamp(els, data.fsamp);
    // The project folder the file lies in under the output folder, when it does.
    if (typeof data.project === "string" && data.project) {
      setBidsProject(data.project);
    }
    // A recording in a BIDS dataset brings the placement its sidecar records.
    const meta = data.metadata || {};
    applySidecarFields(els, {
      ...data,
      placement_scheme: data.placement_scheme || meta.placement_scheme,
      placement_scheme_description:
        data.placement_scheme_description || meta.placement_scheme_description,
    });
  }

  /** @type {FileSessionService["applySessionInfoFromDecomposition"]} */
  function applySessionInfoFromDecomposition(file, data) {
    const fileLabel = file?.name || data.file_label || "decomposition";
    // The project is the caller's: it comes with the edit session's project.
    resetBidsEntityDefaults(els, fileLabel);
    resetSidecarFields(els);
    setBidsEntitiesInput(parseBidsEntitiesFromLabel(fileLabel));
    applySidecarFields(els, data);
    showFsamp(els, data.fsamp);
    setMuscleNames(state, data.muscle_names || []);
    setEditSoftwareVersions(state, data.software_versions ?? null);
    setFsamp(state, data.fsamp);
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
    renderBidsMuscleFieldsFeature(els, buildBidsMuscleRowsModel(state));
  }

  /** The session fields a run cannot start without; Acquisition and Run are optional. */
  function requiredSessionFields() {
    const muscles = /** @type {HTMLInputElement[]} */ (
      Array.from(
        els.bidsMuscleContainer?.querySelectorAll(".bids-muscle-input") || [],
      )
    );
    return [
      picked ? null : els.bidsProjectNew,
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

  // The session form's BIDS entity label, and the participant and hardware
  // fields every save carries.
  /** @type {FileSessionService["withBidsSaveFields"]} */
  function withBidsSaveFields(payload) {
    const form = readSessionForm();
    return {
      ...payload,
      entity_label: buildEntityLabelFromSession(form),
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

  /** @type {FileSessionService["saveRun"]} */
  async function saveRun(payload, fallbackName) {
    const data = await api.saveRun(
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
    onChooseDataFolder,
    resetSessionForm,
    setProjectLocked,
    getBidsMuscleNames,
    collectBidsEntities,
    checkSessionForm,
    checkPlacementForm,
    setBidsEntitiesInput,
    applyPreviewMetadata,
    applySessionInfoFromDecomposition,
    prefillHardwareFields,
    renderBidsMuscleFields,
    saveRun,
    withBidsSaveFields,
    setUploadLoading,
  };
}

/** @param {App} app */
export function setupSessionFormEvents(app) {
  const { els } = app;

  els.projectTree?.addEventListener("click", (e) => {
    const row =
      e.target instanceof Element ? e.target.closest(".tree-project") : null;
    if (!(row instanceof HTMLButtonElement) || row.disabled) return;
    const project = row.dataset.project ?? "";
    app.setBidsProject(project);
    if (!project) els.bidsProjectNew?.focus();
  });
  els.bidsProjectNew?.addEventListener("input", () => app.syncBidsProject());
  els.dataFolderBtn?.addEventListener("click", () => {
    void app.onChooseDataFolder();
  });
  els.bidsPlacementScheme?.addEventListener("change", () =>
    syncPlacementDescriptionRow(els),
  );
  // A task typed in stays when the target is segmented.
  els.bidsTask?.addEventListener("input", () => {
    els.bidsTask.dataset.keep = "1";
  });

  // A session field marked missing clears once it is filled.
  const clearFilled = (/** @type {Event} */ e) => {
    const field = /** @type {HTMLInputElement} */ (e.target);
    if (field.getAttribute?.("aria-invalid") && field.value.trim()) {
      field.removeAttribute("aria-invalid");
    }
  };
  els.settingsPanel?.addEventListener("input", clearFilled);
  els.settingsPanel?.addEventListener("change", clearFilled);
}
