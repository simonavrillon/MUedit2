import {
  setAuxData,
  setChannelMeans,
  setChannelTraceForGrid,
  setChannelTraces,
  setCoordinates,
  setCurrentStage,
  setFsamp,
  setGridNames,
  setGridSeries,
  setMetadata,
  setMuscle,
  setQcWindowLoading,
  setQcWindowLoadingForGrid,
  setRois,
  setPreviewSeries,
  setSeriesLength,
  setUploadToken,
} from "../state/actions.js";
import {
  beginRawPreviewTransition,
  rollbackRawPreviewTransition,
} from "../state/transitions.js";
import { roiStart, roiEnd } from "../state/selectors.js";

function channelsToEnv(channels) {
  return (Array.isArray(channels) ? channels : [])
    .sort((a, b) => (a.channel_index ?? 0) - (b.channel_index ?? 0))
    .map((c) => c.series);
}

export function syncRois(state, nwin) {
  if (!state.rois) state.rois = [];
  if (state.rois.length > nwin) state.rois = state.rois.slice(0, nwin);
  while (state.rois.length < nwin) {
    state.rois.push({ start: 0, end: state.seriesLength || 0 });
  }
}

export async function requestQcGridWindow(deps, gridIdx, start, end) {
  const { state, api, renderChannelQC, setStatus } = deps;
  const s = Number.isFinite(start) ? start : 0;
  const e = Number.isFinite(end) ? end : state.seriesLength;

  if (!state.uploadToken || !Number.isFinite(gridIdx) || gridIdx < 0) return;
  if (state.qcWindowLoading?.[gridIdx]) return;
  setQcWindowLoadingForGrid(state, gridIdx, true);

  try {
    const requestPayload = {
      upload_token: state.uploadToken,
      grid_index: gridIdx,
      start: s,
      end: e,
      target_fs: 1000,
    };

    const preferBinary = typeof api?.fetchQcWindow === "function";
    const data = await api.fetchQcWindow(requestPayload, { preferBinary });
    const env = channelsToEnv(data.channels);
    setChannelTraceForGrid(state, gridIdx, env);
    if (gridIdx === state.currentGrid) {
      renderChannelQC();
    }
  } catch (err) {
    console.error(err);
    if (typeof setStatus === "function") {
      setStatus(`QC window update failed: ${err.message}`, "error");
    }
  } finally {
    setQcWindowLoadingForGrid(state, gridIdx, false);
  }
}

export async function requestPreview(deps, options = {}) {
  const { silentFailure = false, filepath = null } = options;
  const {
    state,
    api,
    setUploadLoading,
    updateProgress,
    populateAuxSelector,
    ensureDiscardMasks,
    populateGridTabs,
    requestQcGridWindow,
    getCurrentGrid,
    enableRoiSelection,
    renderBidsAutoInfo,
    renderBidsMuscleFields,
    setStatus,
    showWorkspace,
    nextFrame,
    refreshVisuals,
    renderChannelQC,
    applyPreviewMetadata,
    getNwindows,
    hideLanding,
  } = deps;

  if (!state.file && !filepath) return;
  setUploadLoading(true);
  updateProgress(0, "Fetching preview...");

  try {
    let data;
    if (filepath) {
      data = await api.fetchPreviewByPath(filepath);
    } else {
      const formData = new FormData();
      formData.append("file", state.file);
      data = await api.fetchPreview(formData);
    }
    setUploadToken(state, data.upload_token || null);
    setGridSeries(state, data.grid_mean_abs || []);
    setGridNames(state, data.grid_names || []);
    setSeriesLength(state, data.total_samples);
    setChannelMeans(state, data.channel_means || []);
    setCoordinates(state, data.coordinates || []);
    setChannelTraces(state, []);
    setQcWindowLoading(state, {});
    setMetadata(state, data.metadata || {});
    setMuscle(state, data.muscle || []);
    setAuxData(state, data.auxiliary || [], data.auxiliary_names || []);
    setFsamp(state, data.fsamp);
    if (applyPreviewMetadata) applyPreviewMetadata(data);
    setPreviewSeries(state, data.mean_abs || []);
    populateAuxSelector();
    ensureDiscardMasks();
    populateGridTabs();
    const nwin = getNwindows ? getNwindows() : 1;
    const defaultEnd = state.seriesLength || 0;
    const rois = [];
    for (let i = 0; i < nwin; i++) {
      rois.push({ start: 0, end: defaultEnd });
    }
    setRois(state, rois);
    const roiPreview = state.rois?.[0];
    await requestQcGridWindow(
      getCurrentGrid(),
      roiStart(roiPreview),
      roiEnd(roiPreview, state.seriesLength),
    );
    enableRoiSelection("emgCanvas");
    enableRoiSelection("auxCanvas");
    // Raw preview resets edit slice first; ensure BIDS rows render in QC context
    // so they source run grid names instead of edit fallback ("Grid 1").
    setCurrentStage(state, "qc");
    renderBidsAutoInfo();
    renderBidsMuscleFields();

    updateProgress(0, "Preview ready - drag to select ROI");
    setStatus("Preview ready", "success");
    showWorkspace({ keepLandingVisible: true });
    await nextFrame();
    refreshVisuals();
    await renderChannelQC(true);
    if (hideLanding) hideLanding();
    return true;
  } catch (err) {
    console.error(err);
    setUploadToken(state, null);
    updateProgress(0, "Preview failed");
    if (!silentFailure) {
      setStatus("Preview failed", "error");
    }
    return false;
  } finally {
    setUploadLoading(false);
  }
}

export async function handleRawFile(deps, file, options = {}) {
  const { silentPreviewFailure = false } = options;
  const {
    state,
    resetBidsEntityDefaults,
    requestPreview,
    setStatus,
    updateStartAvailability,
  } = deps;

  if (!file) return;
  beginRawPreviewTransition(state, file);
  resetBidsEntityDefaults(file.name);
  setStatus("File ready");
  updateStartAvailability();
  const ok = await requestPreview({ silentFailure: silentPreviewFailure });
  if (!ok) {
    rollbackRawPreviewTransition(state);
    updateStartAvailability();
  }
  return ok;
}

export async function handleLandingFile(deps, file) {
  const {
    setUploadLoading,
    showUnsupportedUploadFormatError,
    clearUploadFormatError,
    isSupportedSignalFile,
    detectLandingFileType,
    handleRawFile,
    handleDecompositionFile,
  } = deps;

  if (!file) return;
  if (!isSupportedSignalFile(file)) {
    setUploadLoading(false);
    showUnsupportedUploadFormatError();
    return;
  }
  clearUploadFormatError();
  const kind = detectLandingFileType(file);
  if (kind === "raw") {
    await handleRawFile(file);
    return;
  }
  if (kind === "decomposition") {
    await handleDecompositionFile(file);
    return;
  }
  if (kind === "ambiguous_mat") {
    const rawOk = await handleRawFile(file, { silentPreviewFailure: true });
    if (!rawOk) {
      await handleDecompositionFile(file);
    }
    return;
  }
  setUploadLoading(false);
  showUnsupportedUploadFormatError();
}
