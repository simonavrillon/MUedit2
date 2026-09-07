import {
  API_BASE,
  COLORS,
  DECOMPOSITION_EXTENSIONS,
  RAW_SIGNAL_EXTENSIONS,
} from "../config.js";
import { createApiClient } from "../api/client.js";
import { buildDecomposeParams } from "../decomp/params.js";
import { els } from "./dom.js";
import { apiFetch, apiJson, waitForBackend } from "./http.js";
import {
  applySessionInfoToDom as applySessionInfoToDomController,
  renderBidsAutoInfo as renderBidsAutoInfoController,
  renderBidsMuscleFields as renderBidsMuscleFieldsController,
  resetBidsEntityDefaults,
  applyParticipantFields,
} from "../view/bids-renderer.js";
import {
  buildBidsAutoInfoModel as buildBidsAutoInfoModelFeature,
  buildBidsMuscleRowsModel as buildBidsMuscleRowsModelFeature,
  buildEntityLabelFromSession,
  buildSessionInfoFromDecomposition as buildSessionInfoFromDecompositionFeature,
  getSuggestedNpzName,
  listifyMuscles,
  parseBidsEntitiesFromLabel,
} from "../io/bids.js";
import {
  adjustView as adjustViewFeature,
  getViewForStage as getViewForStageFeature,
  goToMu as goToMuFeature,
  handleKeyboardNavigation as handleKeyboardNavigationFeature,
  setViewForStage as setViewForStageFeature,
} from "./services/navigation.js";
import {
  createImportStageService,
  setupImportEvents,
} from "./stages/import-stage.js";
import { createRunStageService, setupRunEvents } from "./stages/run-stage.js";
import {
  createEditStageService,
  setupEditEvents,
} from "./stages/edit-stage.js";
import {
  createLayoutStageService,
  setupLayoutEvents,
} from "./stages/layout-stage.js";
import {
  drawGridOverlay,
  drawMiniSeries,
  drawSeries,
  getCanvasPlotMetrics,
} from "../view/plots.js";
import { state } from "./state.js";
import {
  ensureDiscardMasks as ensureDiscardMasksAction,
  setCurrentGrid,
  setEditProject,
  setEditMode,
  setEditSoftwareVersions,
  setFsamp,
  setMuscle as setMuscleAction,
  setShowBookmark,
} from "../state/actions.js";
import {
  getCurrentGrid as getCurrentGridSelector,
  roiStart,
  roiEnd,
} from "../state/selectors.js";
import { createUiService } from "./services/ui.js";
import { createFileSessionService } from "./services/file-session.js";
import { createQcStageService } from "./stages/qc-stage.js";

const api = createApiClient({ apiFetch, apiJson, API_BASE });

function nextFrame() {
  return new Promise((resolve) => {
    window.requestAnimationFrame(() => resolve());
  });
}

function updateStartAvailability() {
  if (els.start) {
    els.start.disabled = !state.file || state.isRunning;
  }
}

function ensureDiscardMasks() {
  ensureDiscardMasksAction(state);
}

function getCurrentGrid() {
  return getCurrentGridSelector(state);
}

function buildParams(isToggleOn) {
  return buildDecomposeParams({
    niter: Number(els.niter?.value) || 150,
    nwindows: Number(els.nwindows?.value) || 1,
    peelOn: isToggleOn(els.peelOffToggle),
    postprocessMode: els.postprocessMode?.value || "windowed",
    covOn: isToggleOn(els.covToggle),
    peelWindow: Number(els.peelOffWindow?.value) || 25,
    covVal: Number(els.covValue?.value) || 0.5,
    silVal: Number(els.silValue?.value) || 0.9,
    duplicatesthresh: Number(els.duplicatesthresh?.value) || 0.3,
  });
}

function applySessionInfoFromDecomposition(file, data = {}) {
  const payload = buildSessionInfoFromDecompositionFeature(file, data, {
    parseBidsEntitiesFromLabel,
    listifyMuscles,
  });
  applySessionInfoToDomController(els, payload);
  setMuscleAction(state, payload.muscles);
  setEditSoftwareVersions(state, payload.bids?.softwareVersions ?? null);
  setFsamp(state, payload.fsampText);
}

function renderBidsAutoInfo() {
  const model = buildBidsAutoInfoModelFeature(state);
  renderBidsAutoInfoController(els, model);
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
  const rows = buildBidsMuscleRowsModelFeature(state);
  renderBidsMuscleFieldsController(els, rows);
}

async function persistNpzBySaveTarget(payload, fallbackName, fileSession, ui) {
  const { subject, task, session, run, acquisition } =
    fileSession.getBidsEntityInputs();
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
    ...fileSession.getBidsSaveFields(),
  });
  ui.setStatus("Saved", "success");
  return { mode: "saved", path: data.path || "" };
}

// The QC, run, and edit stages reference each other (e.g. run renders QC,
// navigation drives both explorers), so they cannot be constructed in a single
// dependency order. They are declared here and assigned further below once all
// three factories have run. The wrapper functions that follow forward to the
// late-bound instances with optional chaining, so they are safe to pass into
// other services before assignment completes — prefer reusing these wrappers
// over re-creating inline `(...args) => stage.fn(...args)` thunks.
let qcStage;
let runStage;
let editStage;

function renderChannelQC(...args) {
  return qcStage?.renderChannelQC(...args);
}

function refreshVisuals(...args) {
  return qcStage?.refreshVisuals(...args);
}

function renderEditExplorer(...args) {
  return editStage?.renderEditExplorer(...args);
}

function setSelectedGrid(idx) {
  setCurrentGrid(state, idx);

  const tabs = els.qcGridTabs
    ? els.qcGridTabs.querySelectorAll(".tab-btn")
    : [];
  tabs.forEach((tab, i) => {
    tab.classList.toggle("active", i === state.currentGrid);
  });

  qcStage.renderChannelQC();
  qcStage.renderAuxiliaryChannels();
  const roi = state.rois?.[0];
  qcStage.requestQcGridWindow(
    state.currentGrid,
    roiStart(roi),
    roiEnd(roi, state.seriesLength),
  );
}

const ui = createUiService({
  els,
  state,
  renderChannelQC,
  refreshVisuals,
  renderEditExplorer,
  setSelectedGrid,
});

const fileSession = createFileSessionService({ els });

function getViewForStage(stage) {
  return getViewForStageFeature(state, stage);
}

function setViewForStage(stage, view) {
  setViewForStageFeature(
    {
      state,
      renderEditExplorer,
      renderMuExplorer: () => runStage.renderMuExplorer(),
    },
    stage,
    view,
  );
}

function adjustView(view, total, action) {
  return adjustViewFeature(view, total, action);
}

function goToMu(direction, stage) {
  goToMuFeature(
    {
      state,
      getEditMuIndicesForGrid: (gridIdx) => editStage.getEditMuIndices(gridIdx),
      renderEditExplorer,
      getMuIndicesForGrid: (gridIdx) => runStage.getMuIndicesForGrid(gridIdx),
      renderMuExplorer: () => runStage.renderMuExplorer(),
    },
    direction,
    stage,
  );
}

function refreshEditModeButtons() {
  ui.setEditActionBusy(els.editAddBtn, state.edit.mode === "add");
  ui.setEditActionBusy(
    els.editAddArtifactBtn,
    state.edit.mode === "add_artifact",
  );
  ui.setEditActionBusy(
    els.editDeleteSpikeBtn,
    state.edit.mode === "delete_spikes",
  );
  if (els.editUndoBtn) els.editUndoBtn.disabled = !state.edit.backup;
}

function setEditModeWithStatus(mode, message) {
  setEditMode(state, mode);
  refreshEditModeButtons();
  if (mode) {
    ui.setEditStatus(message || `Mode: ${mode}`, "muted");
  }
}

function handleKeyboardNavigation(e) {
  handleKeyboardNavigationFeature(
    {
      state,
      els,
      setEditMode: setEditModeWithStatus,
      runEditAction: ui.runEditAction,
      removeOutliers: () => editStage.removeOutliers(),
      updateMuFilter: () => editStage.updateMuFilter(),
      goToMuFn: goToMu,
      getViewForStageFn: getViewForStage,
      adjustViewFn: adjustView,
      setViewForStageFn: setViewForStage,
      setShowBookmark: setShowBookmark,
      applyLabeledToggle: ui.applyLabeledToggle,
    },
    e,
  );
}

editStage = createEditStageService({
  state,
  els,
  api,
  COLORS,
  drawSeries,
  getCanvasPlotMetrics,
  getSuggestedNpzName,
  persistNpzBySaveTarget: (payload, fallbackName) =>
    persistNpzBySaveTarget(payload, fallbackName, fileSession, ui),
  getBidsMuscleNames: fileSession.getBidsMuscleNames,
  buildEntityLabelFromSession,
  applySessionInfoFromDecomposition,
  showWorkspace: ui.showWorkspace,
  switchStage: ui.switchStage,
  setUploadLoading: fileSession.setUploadLoading,
  setEditStatus: ui.setEditStatus,
  setEditMode: setEditModeWithStatus,
  refreshEditModeButtons,
  renderBidsMuscleFields,
});

runStage = createRunStageService({
  state,
  els,
  api,
  COLORS,
  drawSeries,
  drawGridOverlay,
  getSuggestedNpzName,
  persistNpzBySaveTarget: (payload, fallbackName) =>
    persistNpzBySaveTarget(payload, fallbackName, fileSession, ui),
  getBidsProject: fileSession.getBidsProject,
  getBidsMuscleNames: fileSession.getBidsMuscleNames,
  collectBidsEntities: fileSession.collectBidsEntities,
  buildParams: () => buildParams(ui.isToggleOn),
  updateStartAvailability,
  switchStage: ui.switchStage,
  setStatus: ui.setStatus,
  updateProgress: ui.updateProgress,
  setProgressText: (text) => {
    if (els.progressText) els.progressText.textContent = text;
  },
  setNwindows: (n) => {
    if (els.nwindows) els.nwindows.value = n;
  },
  emgCanvasId: els.emgCanvas?.id || "emgCanvas",
  ensureDiscardMasks,
  renderChannelQC,
  getCurrentGrid,
  requestQcGridWindow: (...args) => qcStage.requestQcGridWindow(...args),
  showWorkspace: ui.showWorkspace,
  renderBidsAutoInfo,
  renderBidsMuscleFields,
  populateAuxSelector: () => qcStage.populateAuxSelector(),
  renderAuxiliaryChannels: () => qcStage.renderAuxiliaryChannels(),
  enableRoiSelection: (...args) => qcStage.enableRoiSelection(...args),
  loadDecompositionForEditByPath: (path) =>
    editStage.loadDecompositionForEditByPath(path),
});

qcStage = createQcStageService({
  state,
  els,
  api,
  drawMiniSeries,
  drawGridOverlay,
  setStatus: ui.setStatus,
  updateProgress: ui.updateProgress,
  setUploadLoading: fileSession.setUploadLoading,
  showUnsupportedUploadFormatError:
    fileSession.showUnsupportedUploadFormatError,
  clearUploadFormatError: fileSession.clearUploadFormatError,
  isSupportedSignalFile: fileSession.isSupportedSignalFile,
  detectLandingFileType: fileSession.detectLandingFileType,
  rawAndDecompositionExtensions: {
    raw: RAW_SIGNAL_EXTENSIONS,
    decomposition: DECOMPOSITION_EXTENSIONS,
  },
  ensureDiscardMasks,
  populateGridTabs: () => ui.populateGridTabs(),
  getCurrentGrid,
  renderBidsAutoInfo,
  renderBidsMuscleFields,
  showWorkspace: ui.showWorkspace,
  nextFrame,
  updateStartAvailability,
  renderMuExplorer: () => runStage.renderMuExplorer(),
  resetBidsEntityDefaults: (fileName) => resetBidsEntityDefaults(els, fileName),
  applyPreviewMetadata: (data) => {
    if (els.fsamp) {
      const fs = Number(data.fsamp);
      els.fsamp.value =
        Number.isFinite(fs) && fs > 0 ? String(Math.round(fs)) : "";
    }
    const participant = data?.participant_meta || {};
    applyParticipantFields(els, participant);
    if (els.bidsManufacturer && data?.manufacturer)
      els.bidsManufacturer.value = data.manufacturer;
    if (els.bidsDeviceModel && data?.manufacturers_model_name)
      els.bidsDeviceModel.value = data.manufacturers_model_name;
  },
  getNwindows: () => Number(els.nwindows?.value) || 1,
  hideLanding: () => {
    if (els.landing) els.landing.classList.add("hidden");
  },
});

const importStage = createImportStageService({
  api,
  setStatus: ui.setStatus,
  clearUploadFormatError: fileSession.clearUploadFormatError,
  setUploadLoading: fileSession.setUploadLoading,
  showUnsupportedUploadFormatError:
    fileSession.showUnsupportedUploadFormatError,
  detectLandingFileType: fileSession.detectLandingFileType,
  handleRawFilePath: (...args) => qcStage.handleRawFilePath(...args),
  loadDecompositionForEditByPath: (...args) =>
    editStage.loadDecompositionForEditByPath(...args),
  setBidsEntitiesInput: (entities) => {
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
  },
});

const layoutStage = createLayoutStageService({
  ensureSettingsToggleIcon: ui.ensureSettingsToggleIcon,
  toggleSettingsOpen: ui.toggleSettingsOpen,
  setSettingsOpen: ui.setSettingsOpen,
  initLayoutResizePolicy: ui.initLayoutResizePolicy,
});

function handleLandingFile(file) {
  return qcStage.handleLandingFile(file, (inputFile) =>
    editStage.handleDecompositionFile(inputFile),
  );
}

function wireEvents() {
  layoutStage.ensureSettingsToggleIcon();

  setupImportEvents({
    els,
    state,
    clearUploadFormatError: fileSession.clearUploadFormatError,
    setUploadLoading: fileSession.setUploadLoading,
    handleLandingFile,
    handleNativeDialogOpen: importStage.handleNativeDialogOpen,
    setStatus: ui.setStatus,
    showWorkspace: ui.showWorkspace,
    switchStage: ui.switchStage,
    updateWorkflowStepper: ui.updateWorkflowStepper,
  });

  setupRunEvents({
    els,
    state,
    runDecomposition: () => runStage.runDecomposition(),
    enableRoiSelection: (id) => qcStage.enableRoiSelection(id),
    syncRois: (nwin) => qcStage.syncRois(nwin),
    refreshVisuals,
    setupToggle: ui.setupToggle,
    setupLockedOnToggle: ui.setupLockedOnToggle,
    toggleConditional: ui.toggleConditional,
    updateStartAvailability,
    renderAuxiliaryChannels: () => qcStage.renderAuxiliaryChannels(),
    renderMuExplorer: () => runStage.renderMuExplorer(),
  });

  setupEditEvents({
    els,
    state,
    bindEditCanvas: () => editStage.bindEditCanvas(),
    bindEditDrCanvas: () => editStage.bindEditDrCanvas(),
    bindEditTimeline: () => editStage.bindEditTimeline(),
    renderEditExplorer,
    runEditAction: ui.runEditAction,
    saveEditedFile: () => editStage.saveEditedFile(),
    resetCurrentMuEdits: () => editStage.resetCurrentMuEdits(),
    updateMuFilter: () => editStage.updateMuFilter(),
    removeOutliers: () => editStage.removeOutliers(),
    flagMuForDeletion: () => editStage.flagMuForDeletion(),
    duplicateMu: () => editStage.duplicateMu(),
    removeDuplicateMus: () => editStage.removeDuplicateMus(),
    restoreEditBackup: () => editStage.restoreEditBackup(),
    setEditMode: setEditModeWithStatus,
    refreshEditModeButtons,
    handleKeyboardNavigation,
    applyLabeledToggle: ui.applyLabeledToggle,
  });

  setupLayoutEvents({
    els,
    toggleSettingsOpen: layoutStage.toggleSettingsOpen,
    setSettingsOpen: layoutStage.setSettingsOpen,
    initLayoutResizePolicy: layoutStage.initLayoutResizePolicy,
  });
}

export async function initializeApp() {
  wireEvents();
  ui.updateStepAvailability();
  ui.updateWorkflowStepper("import");

  if (els.browseSignalBtn) els.browseSignalBtn.disabled = true;
  ui.setStatus("Connecting to backend…", "muted");

  const ready = await waitForBackend(api.healthUrl());
  if (ready) {
    if (els.browseSignalBtn) els.browseSignalBtn.disabled = false;
    ui.setStatus("", "muted");
  } else {
    ui.setStatus("Backend unreachable — please restart the app", "error");
  }
}
