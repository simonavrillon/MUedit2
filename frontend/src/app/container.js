import {
  API_BASE,
  COLORS,
  DECOMPOSITION_EXTENSIONS,
  DEFAULT_BIDS_ROOT,
  RAW_SIGNAL_EXTENSIONS,
} from "../config.js";
import { els } from "../dom.js";
import { apiFetch, apiJson } from "../http.js";
import {
  buildBidsAutoInfoModel as buildBidsAutoInfoModelFeature,
  buildBidsMuscleRowsModel as buildBidsMuscleRowsModelFeature,
  buildSessionInfoFromDecomposition as buildSessionInfoFromDecompositionFeature,
} from "../features/bids_ui.js";
import {
  applySessionInfoToDom as applySessionInfoToDomController,
  renderBidsAutoInfo as renderBidsAutoInfoController,
  renderBidsMuscleFields as renderBidsMuscleFieldsController,
} from "../controllers/bids_form.js";
import {
  adjustView as adjustViewFeature,
  getViewForStage as getViewForStageFeature,
  goToMu as goToMuFeature,
  handleKeyboardNavigation as handleKeyboardNavigationFeature,
  setViewForStage as setViewForStageFeature,
} from "../features/navigation.js";
import { setupImportEvents } from "../setup/import.js";
import { setupRunEvents } from "../setup/run.js";
import { setupEditEvents } from "../setup/edit.js";
import { setupLayoutEvents } from "../setup/layout.js";
import {
  drawGridOverlay,
  drawMiniSeries,
  drawSeries,
  getCanvasPlotMetrics,
} from "../plots.js";
import {
  buildEntityLabelFromSession,
  getSuggestedNpzName,
  listifyMuscles,
  parseBidsEntitiesFromLabel,
} from "../session.js";
import { state } from "../state.js";
import {
  ensureDiscardMasks as ensureDiscardMasksAction,
  setCurrentGrid,
  setEditBidsRoot,
  setEditMode,
  setMuscle as setMuscleAction,
} from "../state/actions.js";
import { getCurrentGrid as getCurrentGridSelector } from "../state/selectors.js";
import { createUiService } from "./services/ui.js";
import { createFileSessionService } from "./services/file_session.js";
import { createQcStageService } from "./stages/qc_stage.js";
import { createRunStageService } from "./stages/run_stage.js";
import { createEditStageService } from "./stages/edit_stage.js";
import { createImportStageService } from "./stages/import_stage.js";
import { createLayoutStageService } from "./stages/layout_stage.js";

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
  const niter = Number(els.niter?.value) || 150;
  const nwindows = Number(els.nwindows?.value) || 1;
  const peelOn = isToggleOn(els.peelOffToggle);
  const adaptiveOn = isToggleOn(els.useAdaptiveToggle);
  const covOn = isToggleOn(els.covToggle);
  const silOn = isToggleOn(els.silToggle);
  const peelWindow = Number(els.peelOffWindow?.value) || 25;
  const covVal = Number(els.covValue?.value) || 0.5;
  const silVal = Number(els.silValue?.value) || 0.9;
  const duplicatesthresh = Number(els.duplicatesthresh?.value) || 0.3;

  return {
    niter,
    nwindows,
    nbextchan: 1000,
    duplicatesthresh,
    sil_thr: silVal,
    sil_filter: silOn ? 1 : 0,
    cov_thr: covVal,
    covfilter: covOn ? 1 : 0,
    contrast_func: "skew",
    initialization: 0,
    peel_off_enabled: peelOn ? 1 : 0,
    peel_off_win: peelWindow / 1000,
    use_adaptive: adaptiveOn ? 1 : 0,
  };
}

function applySessionInfoFromDecomposition(file, data = {}) {
  const payload = buildSessionInfoFromDecompositionFeature(file, data, {
    parseBidsEntitiesFromLabel,
    listifyMuscles,
  });
  applySessionInfoToDomController(els, payload);
  setMuscleAction(state, payload.muscles);
}

function renderBidsAutoInfo() {
  const model = buildBidsAutoInfoModelFeature(state);
  renderBidsAutoInfoController(els, model);
}

function renderBidsMuscleFields() {
  const rows = buildBidsMuscleRowsModelFeature(state);
  renderBidsMuscleFieldsController(els, rows);
}

async function persistNpzBySaveTarget(payload, fallbackName, fileSession, ui) {
  const entityLabel =
    payload.entity_label ||
    buildEntityLabelFromSession(
      els.bidsSubject?.value,
      els.bidsTask?.value,
      els.bidsSession?.value,
      els.bidsRun?.value,
    );
  const data = await apiJson(
    `${API_BASE}/edit/save`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        ...payload,
        file_label: payload.file_label || fallbackName || "decomposition.npz",
        entity_label: entityLabel,
        bids_root: fileSession.getBidsRoot(),
      }),
    },
    120000,
  );
  ui.setStatus("Saved", "success");
  return { mode: "saved", path: data.path || "" };
}

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
    Number.isFinite(roi?.start) ? roi.start : 0,
    Number.isFinite(roi?.end) ? roi.end : state.seriesLength,
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
      renderEditExplorer: () => editStage.renderEditExplorer(),
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
      renderEditExplorer: () => editStage.renderEditExplorer(),
      getMuIndicesForGrid: (gridIdx) => runStage.getMuIndicesForGrid(gridIdx),
      renderMuExplorer: () => runStage.renderMuExplorer(),
    },
    direction,
    stage,
  );
}

function refreshEditModeButtons() {
  ui.setEditActionBusy(els.editAddBtn, state.edit.mode === "add");
  ui.setEditActionBusy(els.editDeleteSpikeBtn, state.edit.mode === "delete_spikes");
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
    },
    e,
  );
}

editStage = createEditStageService({
  state,
  els,
  API_BASE,
  apiFetch,
  apiJson,
  COLORS,
  drawSeries,
  getCanvasPlotMetrics,
  getSuggestedNpzName,
  persistNpzBySaveTarget: (payload, fallbackName) =>
    persistNpzBySaveTarget(payload, fallbackName, fileSession, ui),
  getBidsMuscleNames: fileSession.getBidsMuscleNames,
  getBidsRoot: fileSession.getBidsRoot,
  buildEntityLabelFromSession,
  applySessionInfoFromDecomposition,
  showWorkspace: ui.showWorkspace,
  switchStage: ui.switchStage,
  setUploadLoading: fileSession.setUploadLoading,
  setEditStatus: ui.setEditStatus,
  setEditMode: setEditModeWithStatus,
  refreshEditModeButtons,
  inferBidsRootFromSelectedPath: fileSession.inferBidsRootFromSelectedPath,
  renderBidsMuscleFields,
});

runStage = createRunStageService({
  state,
  els,
  API_BASE,
  apiFetch,
  COLORS,
  drawSeries,
  drawGridOverlay,
  getSuggestedNpzName,
  persistNpzBySaveTarget: (payload, fallbackName) =>
    persistNpzBySaveTarget(payload, fallbackName, fileSession, ui),
  getBidsRoot: fileSession.getBidsRoot,
  getBidsMuscleNames: fileSession.getBidsMuscleNames,
  buildParams: () => buildParams(ui.isToggleOn),
  updateStartAvailability,
  switchStage: ui.switchStage,
  setStatus: ui.setStatus,
  updateProgress: ui.updateProgress,
  ensureDiscardMasks,
  renderChannelQC: (...args) => qcStage.renderChannelQC(...args),
  getCurrentGrid,
  requestQcGridWindow: (...args) => qcStage.requestQcGridWindow(...args),
  showWorkspace: ui.showWorkspace,
  renderBidsAutoInfo,
  renderBidsMuscleFields,
  populateAuxSelector: () => qcStage.populateAuxSelector(),
  renderAuxiliaryChannels: () => qcStage.renderAuxiliaryChannels(),
  enableRoiSelection: (...args) => qcStage.enableRoiSelection(...args),
});

qcStage = createQcStageService({
  state,
  els,
  API_BASE,
  apiFetch,
  apiJson,
  drawMiniSeries,
  drawGridOverlay,
  setStatus: ui.setStatus,
  updateProgress: ui.updateProgress,
  setUploadLoading: fileSession.setUploadLoading,
  showUnsupportedUploadFormatError: fileSession.showUnsupportedUploadFormatError,
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
});

const importStage = createImportStageService({
  apiJson,
  API_BASE,
  setStatus: ui.setStatus,
  clearUploadFormatError: fileSession.clearUploadFormatError,
  setUploadLoading: fileSession.setUploadLoading,
  showUnsupportedUploadFormatError: fileSession.showUnsupportedUploadFormatError,
  detectLandingFileType: fileSession.detectLandingFileType,
  inferBidsRootFromSelectedPath: fileSession.inferBidsRootFromSelectedPath,
  handleRawFilePath: (...args) => qcStage.handleRawFilePath(...args),
  loadDecompositionForEditByPath: (...args) =>
    editStage.loadDecompositionForEditByPath(...args),
  setEditBidsRootInput: (bidsRoot) => {
    if (els.editBidsRoot) els.editBidsRoot.value = bidsRoot;
    setEditBidsRoot(state, bidsRoot);
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
    refreshVisuals: () => qcStage.refreshVisuals(),
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
    DEFAULT_BIDS_ROOT,
    bindEditCanvas: () => editStage.bindEditCanvas(),
    bindEditDrCanvas: () => editStage.bindEditDrCanvas(),
    renderEditExplorer: () => editStage.renderEditExplorer(),
    runEditAction: ui.runEditAction,
    saveEditedFile: () => editStage.saveEditedFile(),
    resetCurrentMuEdits: () => editStage.resetCurrentMuEdits(),
    updateMuFilter: () => editStage.updateMuFilter(),
    removeOutliers: () => editStage.removeOutliers(),
    flagMuForDeletion: () => editStage.flagMuForDeletion(),
    restoreEditBackup: () => editStage.restoreEditBackup(),
    setEditMode: setEditModeWithStatus,
    refreshEditModeButtons,
    handleKeyboardNavigation,
  });

  setupLayoutEvents({
    els,
    toggleSettingsOpen: layoutStage.toggleSettingsOpen,
    setSettingsOpen: layoutStage.setSettingsOpen,
    initLayoutResizePolicy: layoutStage.initLayoutResizePolicy,
  });
}

export function initializeApp() {
  wireEvents();
  ui.updateStepAvailability();
  ui.updateWorkflowStepper("import");
}
