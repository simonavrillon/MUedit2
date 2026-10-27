/**
 * The QC stage: its service (channel traces and plots, the aux channels, the
 * selected grid) and its event bindings.
 */
import {
  syncRois,
  requestAutoQc,
  createQcTraces,
  requestPreview,
} from "../../signal/qc.js";
import {
  populateAuxSelector as populateAuxSelectorFeature,
  renderAuxiliaryChannels as renderAuxiliaryChannelsFeature,
  refreshVisuals as refreshVisualsFeature,
  enableRoiSelection,
  drawChannelQC,
} from "../../view/qc-renderer.js";
import {
  removeLastArtifactRegion,
  setArtifactMode,
  setCurrentGrid,
} from "../../state/actions.js";
import { oncePerFrame } from "../../view/plots.js";
import {
  chooseSegmentType,
  requestSegmentation,
  syncChosenWindows,
} from "../../signal/segments.js";
import { getCurrentGrid } from "../../state/selectors.js";

/** @typedef {import("../context.js").App} App */
/** @typedef {import("../context.js").QcStage} QcStage */
/** @typedef {import("../context.js").Els} Els */

/**
 * @param {App} app
 * @returns {import("../context.js").QcStage}
 */
export function createQcStageService(app) {
  const { state, els } = app;

  /** @type {QcStage["populateAuxSelector"]} */
  function populateAuxSelector() {
    populateAuxSelectorFeature(els, state);
  }

  /** @type {QcStage["renderAuxiliaryChannels"]} */
  function renderAuxiliaryChannels() {
    renderAuxiliaryChannelsFeature(els, state);
  }

  /** @type {QcStage["ensureQcTraces"]} */
  const ensureQcTraces = createQcTraces(app);

  /** @type {QcStage["handleRawFilePath"]} */
  async function handleRawFilePath(path, name, options = {}) {
    const ok = await requestPreview(app, {
      silentFailure: options.silentFailure,
      file: { name, path },
    });
    if (ok) app.updateStartAvailability();
    return ok;
  }

  /** @type {QcStage["renderChannelQC"]} */
  function renderChannelQC() {
    // A grid the channel data does not reach shows the first one.
    if (!state.channelMeans[getCurrentGrid(state)] && state.channelMeans.length)
      setCurrentGrid(state, 0);
    drawChannelQC(app);
    app.ensureQcTraces();
  }

  /** @type {QcStage["refreshVisuals"]} */
  function refreshVisuals() {
    refreshVisualsFeature(app);
  }

  /** @type {QcStage["scheduleRefreshVisuals"]} */
  const scheduleRefreshVisuals = oncePerFrame(() => app.refreshVisuals());

  /** @type {QcStage["setSelectedGrid"]} */
  function setSelectedGrid(idx) {
    setCurrentGrid(state, idx);
    const tabs = els.qcGridTabs?.querySelectorAll(".tab-btn") || [];
    tabs.forEach((tab, i) => {
      tab.classList.toggle("active", i === state.currentGrid);
    });
    renderChannelQC();
  }

  return {
    populateAuxSelector,
    renderAuxiliaryChannels,
    ensureQcTraces,
    handleRawFilePath,
    renderChannelQC,
    refreshVisuals,
    scheduleRefreshVisuals,
    setSelectedGrid,
  };
}

/**
 * Arm (or cancel) artifact selection for the next drag on the EMG plot.
 *
 * @param {App} app
 */
function toggleArtifactMode(app) {
  const { state } = app;
  setArtifactMode(state, !state.artifactMode);
  app.refreshVisuals();
  app.setStatus(
    state.artifactMode
      ? "Drag on the EMG plot to mark an artifact window"
      : "Artifact selection cancelled",
  );
}

/** @param {App} app */
function removeLastArtifact(app) {
  const { state } = app;
  const removed = removeLastArtifactRegion(state);
  setArtifactMode(state, false);
  app.refreshVisuals();
  const n = state.artifactRegions.length;
  app.setStatus(
    removed
      ? `Artifact window removed (${n} left)`
      : "No artifact windows to remove",
  );
}

/**
 * @param {Els} els
 * @param {boolean} open
 */
function setSegmentMenuOpen(els, open) {
  if (!els.segmentTypeMenu) return;
  els.segmentTypeMenu.hidden = !open;
  els.segmentTypeBtn?.setAttribute("aria-expanded", String(open));
}

/**
 * Choose or drop every segment of the type a picker box names.
 *
 * @param {App} app
 * @param {HTMLInputElement} box
 */
function chooseTypeFromBox(app, box) {
  const count = chooseSegmentType(app.state, box.value, box.checked);
  const total = syncChosenWindows(app, count);
  app.refreshVisuals();
  app.setStatus(
    `${box.checked ? "Added" : "Removed"} ${box.value}s — ${total}`,
  );
}

/** @param {App} app */
export function setupQcEvents(app) {
  const { els, state } = app;

  els.qcAutoBtn?.addEventListener("click", () => void requestAutoQc(app));
  els.qcSegmentBtn?.addEventListener(
    "click",
    () => void requestSegmentation(app),
  );
  els.segmentTypeBtn?.addEventListener("click", () =>
    setSegmentMenuOpen(els, !!els.segmentTypeMenu?.hidden),
  );
  els.segmentTypeMenu?.addEventListener("change", (e) =>
    chooseTypeFromBox(app, /** @type {HTMLInputElement} */ (e.target)),
  );
  window.addEventListener("pointerdown", (e) => {
    const target = /** @type {Node} */ (e.target);
    if (target !== els.segmentTypeBtn && !els.segmentTypeMenu?.contains(target))
      setSegmentMenuOpen(els, false);
  });
  window.addEventListener("keydown", (e) => {
    if (e.key === "Escape") setSegmentMenuOpen(els, false);
  });
  els.artifactAddBtn?.addEventListener("click", () => toggleArtifactMode(app));
  els.artifactRemoveBtn?.addEventListener("click", () =>
    removeLastArtifact(app),
  );
  app.refreshVisuals();
  enableRoiSelection(app, "emgCanvas");
  enableRoiSelection(app, "auxCanvas");

  els.nwindows?.addEventListener("change", () => {
    syncRois(state, Number(els.nwindows.value) || 1);
    app.refreshVisuals();
    els.nwindows.blur();
  });

  els.auxSelector?.addEventListener("change", () => {
    app.renderAuxiliaryChannels();
    els.auxSelector.blur();
  });
}
