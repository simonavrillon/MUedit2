import {
  setAuxData,
  setChannelMeans,
  setChannelTraceForGrid,
  setChannelTraces,
  setCoordinates,
  setCurrentStage,
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

export function syncRois(state, nwin) {
  if (!state.rois) state.rois = [];
  if (state.rois.length > nwin) state.rois = state.rois.slice(0, nwin);
  while (state.rois.length < nwin) {
    state.rois.push({ start: 0, end: state.seriesLength || 0 });
  }
}

export async function requestQcGridWindow(
  deps,
  gridIdx,
  start,
  end,
  targetPoints = 96,
) {
  const { state, apiJson, apiFetch, API_BASE, renderChannelQC, setStatus } =
    deps;
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
      representation: state.qcRepresentation || "raw",
      target_fs: 1000,
      target_points: targetPoints,
    };

    let env = [];
    // Raw mode prefers binary transport for lower payload size; fall back to JSON decoding
    // when the backend responds in JSON (or when callers choose JSON mode).
    if (
      (state.qcRepresentation || "raw") === "raw" &&
      typeof apiFetch === "function"
    ) {
      const res = await apiFetch(
        `${API_BASE}/qc/window`,
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            Accept: "application/octet-stream",
          },
          body: JSON.stringify(requestPayload),
        },
        120000,
      );
      const payload = await res.arrayBuffer();
      if (isQcRawF32Payload(payload, res.headers.get("x-muedit-format"))) {
        const decoded = decodeQcRawF32(payload);
        env = decoded.channels
          .sort((a, b) => (a.channel_index ?? 0) - (b.channel_index ?? 0))
          .map((c) => c.series || []);
      } else {
        const data = decodeQcJsonPayload(payload);
        const channels = Array.isArray(data.channels) ? data.channels : [];
        env = channels
          .sort((a, b) => (a.channel_index ?? 0) - (b.channel_index ?? 0))
          .map((c) =>
            Array.isArray(c.series)
              ? c.series
              : { min: c.min || [], max: c.max || [] },
          );
      }
    } else {
      const data = await apiJson(
        `${API_BASE}/qc/window`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(requestPayload),
        },
        120000,
      );
      const channels = Array.isArray(data.channels) ? data.channels : [];
      env = channels
        .sort((a, b) => (a.channel_index ?? 0) - (b.channel_index ?? 0))
        .map((c) =>
          Array.isArray(c.series)
            ? c.series
            : { min: c.min || [], max: c.max || [] },
        );
    }
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

function isQcRawF32Payload(buffer, formatHeader = "") {
  if (formatHeader === "qc-raw-f32-v1") return true;
  if (!buffer || buffer.byteLength < 4) return false;
  const sig = new Uint8Array(buffer, 0, 4);
  return sig[0] === 77 && sig[1] === 81 && sig[2] === 67 && sig[3] === 82; // "MQCR"
}

function decodeQcJsonPayload(buffer) {
  const text = new TextDecoder().decode(new Uint8Array(buffer));
  return JSON.parse(text);
}

function decodeQcRawF32(buffer) {
  // Wire format:
  // 4 bytes magic "MQCR" + uint32 version + fixed metadata fields + repeated channel blocks.
  // Each channel block is: int32 channel_index, uint32 n, float32[n] samples.
  const view = new DataView(buffer);
  const decodeText = (offset, len) =>
    String.fromCharCode(...new Uint8Array(buffer.slice(offset, offset + len)));
  if (decodeText(0, 4) !== "MQCR") {
    throw new Error("Invalid QC raw payload");
  }
  let offset = 4;
  const version = view.getUint32(offset, true);
  offset += 4;
  if (version !== 1) {
    throw new Error(`Unsupported QC raw payload version: ${version}`);
  }
  const grid_index = view.getInt32(offset, true);
  offset += 4;
  const channel_index = view.getInt32(offset, true);
  offset += 4;
  const start = view.getInt32(offset, true);
  offset += 4;
  const end = view.getInt32(offset, true);
  offset += 4;
  const total_samples = view.getInt32(offset, true);
  offset += 4;
  const fsamp = view.getFloat32(offset, true);
  offset += 4;
  const nChannels = view.getUint32(offset, true);
  offset += 4;

  const channels = [];
  for (let i = 0; i < nChannels; i++) {
    const chIdx = view.getInt32(offset, true);
    offset += 4;
    const n = view.getUint32(offset, true);
    offset += 4;
    const series = new Float32Array(buffer, offset, n);
    offset += n * 4;
    channels.push({ channel_index: chIdx, series: Array.from(series) });
  }
  return {
    grid_index,
    channel_index,
    start,
    end,
    total_samples,
    fsamp,
    channels,
  };
}

export async function requestPreview(deps, options = {}) {
  const { silentFailure = false, filepath = null } = options;
  const {
    state,
    apiJson,
    API_BASE,
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
    els,
  } = deps;

  if (!state.file && !filepath) return;
  setUploadLoading(true);
  updateProgress(0, "Fetching preview...");

  try {
    let data;
    if (filepath) {
      data = await apiJson(
        `${API_BASE}/preview-by-path`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ path: filepath }),
        },
        120000,
      );
    } else {
      const formData = new FormData();
      formData.append("file", state.file);
      data = await apiJson(
        `${API_BASE}/preview`,
        { method: "POST", body: formData },
        120000,
      );
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
    if (els.fsamp) {
      const fs = Number(data.fsamp);
      els.fsamp.value = Number.isFinite(fs) && fs > 0 ? String(Math.round(fs)) : "";
    }
    setPreviewSeries(state, data.mean_abs || []);
    populateAuxSelector();
    ensureDiscardMasks();
    populateGridTabs();
    const nwin = Number(els.nwindows?.value) ?? 1;
    const defaultEnd = state.seriesLength || 0;
    const rois = [];
    for (let i = 0; i < nwin; i++) {
      rois.push({ start: 0, end: defaultEnd });
    }
    setRois(state, rois);
    const roiPreview = state.rois?.[0];
    await requestQcGridWindow(
      getCurrentGrid(),
      Number.isFinite(roiPreview?.start) ? roiPreview.start : 0,
      Number.isFinite(roiPreview?.end) ? roiPreview.end : state.seriesLength,
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
    if (els.landing) els.landing.classList.add("hidden");
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
  const { state, els, requestPreview, setStatus, updateStartAvailability } =
    deps;

  if (!file) return;
  beginRawPreviewTransition(state, file);
  if (els.fileName) {
    els.fileName.textContent = file.name;
    els.fileName.classList.remove("loading");
  }
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
