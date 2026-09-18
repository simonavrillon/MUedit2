/**
 * The session form: file-type detection, the upload indicator, and the BIDS
 * entity, participant and hardware fields that travel with a run or a save.
 * Every DOM read or write of those fields goes through here.
 */
import {
  applyParticipantFields,
  applySessionInfoToDom,
  renderBidsAutoInfo as renderBidsAutoInfoView,
  renderBidsMuscleFields as renderBidsMuscleFieldsView,
} from "../../view/bids-renderer.js";
import {
  buildBidsAutoInfoModel,
  buildBidsMuscleRowsModel,
  buildEntityLabelFromSession,
  buildSessionInfoFromDecomposition,
  listifyMuscles,
  parseBidsEntitiesFromLabel,
} from "../../io/bids.js";
import {
  setEditProject,
  setEditSoftwareVersions,
  setFsamp,
  setMuscle,
} from "../../state/actions.js";

/** @typedef {import("../context.js").App} App */

/**
 * @param {App} app
 * @returns {import("../context.js").FileSessionService}
 */
export function createFileSessionService(app) {
  const { els, state, api } = app;

  function getBidsProject() {
    return (els.bidsProject?.value || "").trim();
  }

  function getBidsMuscleNames() {
    const inputs = /** @type {NodeListOf<HTMLInputElement> | undefined} */ (
      els.bidsMuscleContainer?.querySelectorAll(".bids-muscle-input")
    );
    if (!inputs?.length) return [];
    return Array.from(inputs)
      .map((input) => String(input.value || "").trim())
      .filter(Boolean);
  }

  function clearUploadFormatError() {
    if (!els.uploadFormatError) return;
    els.uploadFormatError.textContent = "";
    els.uploadFormatError.classList.add("hidden");
  }

  function showUnsupportedUploadFormatError() {
    if (!els.uploadFormatError) return;
    els.uploadFormatError.textContent =
      "Accepted: raw (.mat, .otb+, .otb4, .bdf, .edf, .rhd) or decomposition (.npz, .mat)";
    els.uploadFormatError.classList.remove("hidden");
  }

  function detectLandingFileType(file) {
    const name = (file?.name || "").toLowerCase();
    if (name.endsWith(".otb+") || name.endsWith(".otb4")) return "raw";
    if (name.endsWith(".bdf") || name.endsWith(".edf")) return "raw";
    if (name.endsWith(".rhd")) return "raw";
    if (name.endsWith(".npz")) return "decomposition";
    if (name.endsWith(".mat")) return "ambiguous_mat";
    return "unsupported";
  }

  function setUploadLoading(active) {
    if (!els.uploadLoader) return;
    els.uploadLoader.classList.toggle("hidden", !active);
  }

  // Raw BIDS entity inputs (subject/task/session/run) used to compose the
  // entity label. Returned untransformed so the caller owns label assembly.
  function getBidsEntityInputs() {
    return {
      subject: els.bidsSubject?.value,
      task: els.bidsTask?.value,
      session: els.bidsSession?.value,
      run: els.bidsRun?.value,
      acquisition: els.bidsAcquisition?.value,
    };
  }

  // Gather the participant + hardware BIDS form fields into the snake_case
  // shape the /edit/save endpoint expects, ready to spread into the request
  // body. Keeps all save-form DOM reads here rather than in the orchestrator.
  function getBidsSaveFields() {
    const age = String(els.bidsParticipantAge?.value || "").trim();
    const sex = String(els.bidsParticipantSex?.value || "").trim();
    const handedness = String(
      els.bidsParticipantHandedness?.value || "",
    ).trim();
    const participantMeta =
      age || sex || handedness
        ? {
            age: age || "n/a",
            sex: sex || "n/a",
            handedness: handedness || "n/a",
          }
        : null;

    return {
      project: getBidsProject(),
      participant_meta: participantMeta,
      powerline_freq: Number(els.bidsPowerlineFreq?.value || 50),
      manufacturer: String(els.bidsManufacturer?.value || "").trim() || null,
      manufacturers_model_name:
        String(els.bidsDeviceModel?.value || "").trim() || null,
      placement_scheme: String(
        els.bidsPlacementScheme?.value || "ChannelSpecific",
      ),
      placement_scheme_description:
        String(els.bidsPlacementDescription?.value || "").trim() || null,
    };
  }

  // Gather all BIDS entity fields from the DOM into the snake_case shape the
  // decompose endpoint expects. Centralizes DOM reads for the run payload.
  function collectBidsEntities() {
    const entities = {};
    const subject = String(els.bidsSubject?.value || "").trim();
    const task = String(els.bidsTask?.value || "").trim();
    const session = String(els.bidsSession?.value || "").trim();
    const run = String(els.bidsRun?.value || "").trim();
    if (subject) entities.subject = subject;
    if (task) entities.task = task;
    if (session) entities.session = session;
    if (run) entities.run = run;
    const muscleNames = getBidsMuscleNames();
    if (muscleNames.length) entities.target_muscle = muscleNames;
    const powerlineFreq = Number(els.bidsPowerlineFreq?.value || 50);
    if (powerlineFreq) entities.powerline_freq = powerlineFreq;
    const manufacturer = String(els.bidsManufacturer?.value || "").trim();
    if (manufacturer) entities.manufacturer = manufacturer;
    const deviceModel = String(els.bidsDeviceModel?.value || "").trim();
    if (deviceModel) entities.manufacturers_model_name = deviceModel;
    const placementScheme = String(els.bidsPlacementScheme?.value || "").trim();
    if (placementScheme) entities.placement_scheme = placementScheme;
    const placementDesc = String(
      els.bidsPlacementDescription?.value || "",
    ).trim();
    if (placementDesc) entities.placement_scheme_description = placementDesc;

    const age = String(els.bidsParticipantAge?.value || "").trim();
    const sex = String(els.bidsParticipantSex?.value || "").trim();
    const handedness = String(
      els.bidsParticipantHandedness?.value || "",
    ).trim();
    if (age || sex || handedness) {
      entities.participant_meta = {
        age: age || "n/a",
        sex: sex || "n/a",
        handedness: handedness || "n/a",
      };
    }

    return entities;
  }

  function setBidsEntitiesInput(entities) {
    if (els.bidsSubject && entities.subject)
      els.bidsSubject.value = entities.subject;
    if (els.bidsTask && entities.task) els.bidsTask.value = entities.task;
    if (els.bidsSession && entities.session)
      els.bidsSession.value = entities.session;
    if (els.bidsAcquisition && entities.acq)
      els.bidsAcquisition.value = entities.acq;
    if (els.bidsRun && entities.run) els.bidsRun.value = entities.run;
    if (els.bidsProject && entities.project) {
      els.bidsProject.value = entities.project;
      setEditProject(state, entities.project);
    }
  }

  function applyPreviewMetadata(data) {
    if (els.fsamp) {
      const fs = Number(data.fsamp);
      els.fsamp.value =
        Number.isFinite(fs) && fs > 0 ? String(Math.round(fs)) : "";
    }
    applyParticipantFields(els, data?.participant_meta || {});
    if (els.bidsManufacturer && data?.manufacturer)
      els.bidsManufacturer.value = data.manufacturer;
    if (els.bidsDeviceModel && data?.manufacturers_model_name)
      els.bidsDeviceModel.value = data.manufacturers_model_name;
  }

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

  function renderBidsMuscleFields() {
    renderBidsMuscleFieldsView(els, buildBidsMuscleRowsModel(state));
  }

  async function persistNpzBySaveTarget(payload, fallbackName) {
    const { subject, task, session, run, acquisition } = getBidsEntityInputs();
    const entityLabel =
      buildEntityLabelFromSession({
        subject,
        task,
        session,
        run,
        acq: acquisition,
      }) || payload.entity_label;

    const data = await api.editSave({
      ...payload,
      file_label: payload.file_label || fallbackName || "decomposition.npz",
      entity_label: entityLabel,
      ...getBidsSaveFields(),
    });
    app.setStatus("Saved", "success");
    return { mode: "saved", path: data.path || "" };
  }

  return {
    getBidsProject,
    getBidsMuscleNames,
    getBidsEntityInputs,
    getBidsSaveFields,
    collectBidsEntities,
    setBidsEntitiesInput,
    applyPreviewMetadata,
    applySessionInfoFromDecomposition,
    renderBidsAutoInfo,
    renderBidsMuscleFields,
    persistNpzBySaveTarget,
    clearUploadFormatError,
    showUnsupportedUploadFormatError,
    detectLandingFileType,
    setUploadLoading,
  };
}
