import {
  ensureDiscardMasks,
  setAuxData,
  setChannelMeans,
  setChannelTraces,
  setCoordinates,
  setGridNames,
  setGridSeries,
  setIsRunning,
  setLastRunDownloadKey,
  setMetadata,
  setMuPreviewData,
  setMuscle,
  setParameters,
  setPreviewSeries,
  setRois,
  setRunDownloadInFlight,
  setSeriesLength,
  setUploadToken,
} from "../state/actions.js";
import { getCurrentGrid, roiStart, roiEnd } from "../state/selectors.js";
import { normalizePreviewPayload } from "../api/payloads.js";
import { getSuggestedNpzName } from "../io/bids.js";
import { drawGridOverlay } from "../view/plots.js";
import { errorMessage } from "../app/services/error-service.js";

/** @typedef {import("../app/context.js").App} App */
/** @typedef {import("../app/context.js").JsonObject} JsonObject */
/** @typedef {import("../api/payloads.js").PreviewPayload} PreviewPayload */

/** @param {App} app */
export async function autoSaveRunDecomposition(app) {
  const {
    state,
    persistNpzBySaveTarget,
    getBidsMuscleNames,
    setStatus,
    loadDecompositionForEditByPath,
  } = app;

  if (state.runDownloadInFlight) return;
  if (!state.muDistimes?.length) return;
  const fileBase = state.file?.name || "decomposition";
  const suggestedName = getSuggestedNpzName(fileBase, "_decomposition");
  const key = `${suggestedName}:${state.muDistimes.length}:${state.seriesLength || 0}`;
  if (state.lastRunDownloadKey === key) return;

  const muscleNames = getBidsMuscleNames();
  const totalSamples =
    state.seriesLength ||
    (state.muPulseTrains?.[0]?.length ?? 0) ||
    Math.max(
      0,
      ...state.muDistimes.flatMap((d) => (d || []).map((v) => Number(v) || 0)),
    ) + 1;
  const fs = state.fsamp;
  const payload = {
    distimes: state.muDistimes || [],
    pulse_trains: state.muPulseTrains || [],
    total_samples: totalSamples,
    fsamp: fs != null && Number.isFinite(fs) && fs > 0 ? fs : null,
    grid_names: state.gridNames || ["Grid 1"],
    mu_grid_index: state.muGridIndex || [],
    parameters: state.parameters || {},
    muscle: muscleNames,
    artifact_regions: state.artifactRegions || [],
    file_label: suggestedName,
  };
  setRunDownloadInFlight(state, true);
  try {
    setStatus("Saving decomposition...", "muted");
    const saved = await persistNpzBySaveTarget(payload, suggestedName);
    setLastRunDownloadKey(state, key);
    setStatus(
      saved?.path
        ? `Decomposition saved to ${saved.path}`
        : "Decomposition saved",
      "success",
    );
    if (saved?.path) {
      loadDecompositionForEditByPath(saved.path);
    }
  } catch (err) {
    console.error(err);
    setStatus(`Save failed: ${errorMessage(err)}`, "error");
  } finally {
    setRunDownloadInFlight(state, false);
  }
}

/** @param {App} app */
export async function runDecomposition(app) {
  const {
    state,
    api,
    getBidsProject,
    collectBidsEntities,
    buildParams,
    updateStartAvailability,
    switchStage,
    setStatus,
    updateProgress,
    handleStreamMessage,
  } = app;

  if (state.isRunning) {
    setStatus("Decomposition already running", "muted");
    return;
  }
  if (!state.file) {
    setStatus("Select an EMG data file first", "error");
    return;
  }

  setIsRunning(state, true);
  updateStartAvailability();
  switchStage("run");
  setParameters(state, buildParams());
  setMuPreviewData(state, [], [], []);
  setLastRunDownloadKey(state, "");

  setStatus("Running decomposition...", "muted");
  updateProgress(5, "Starting decomposition");

  const buildRunFormData = () => {
    const formData = new FormData();
    formData.append("upload_token", state.uploadToken || "");

    formData.append("params", JSON.stringify(buildParams()));
    formData.append("persist_output", "false");
    if (state.discardMasks && state.discardMasks.length) {
      formData.append("discard_channels", JSON.stringify(state.discardMasks));
    }
    if (state.rois && state.rois.length) {
      formData.append("rois", JSON.stringify(state.rois));
    }
    if (state.artifactRegions && state.artifactRegions.length) {
      formData.append(
        "artifact_regions",
        JSON.stringify(state.artifactRegions),
      );
    }

    const project = String(getBidsProject() || "").trim();
    if (project) {
      formData.append("project", project);
    }
    const bidsEntities = collectBidsEntities();
    if (Object.keys(bidsEntities).length) {
      formData.append("bids_entities", JSON.stringify(bidsEntities));
    }

    formData.append("bids_export", "true");
    formData.append("full_preview", "true");
    return formData;
  };

  try {
    let response;
    try {
      response = await api.decomposeStream(buildRunFormData(), 15 * 60 * 1000);
    } catch (err) {
      const message = errorMessage(err);
      const sourcePath = state.file?.path;
      if (!message.includes("upload_token") || !sourcePath) throw err;
      setUploadToken(state, null);
      updateProgress(5, "Session expired, reloading file...");
      const preview = await api.fetchPreviewByPath(sourcePath);
      if (!preview?.upload_token) throw err;
      setUploadToken(state, preview.upload_token);
      updateProgress(5, "Starting decomposition");
      response = await api.decomposeStream(buildRunFormData(), 15 * 60 * 1000);
    }

    if (!response.body) {
      throw new Error("No response body");
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    let malformedEvents = 0;
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop() || "";
      for (const line of lines) {
        if (!line.trim()) continue;
        try {
          handleStreamMessage(JSON.parse(line));
        } catch (e) {
          console.warn("Dropped malformed stream event", e);
          malformedEvents += 1;
        }
      }
    }
    if (buffer.trim()) {
      try {
        handleStreamMessage(JSON.parse(buffer));
      } catch (e) {
        console.warn("Dropped malformed final stream event", e);
        malformedEvents += 1;
      }
    }
    if (malformedEvents > 0) {
      setStatus(
        `Run completed with ${malformedEvents} malformed progress event(s) skipped`,
        "muted",
      );
    }
  } catch (err) {
    console.error(err);
    setStatus(`Error: ${errorMessage(err)}`, "error");
    updateProgress(0, "Run failed. Check console for details.", "error");
  } finally {
    setIsRunning(state, false);
    updateStartAvailability();
  }
}

/**
 * @param {App} app
 * @param {PreviewPayload} preview
 * @param {{ skipMuData?: boolean }} [options]
 */
function applyPreviewData(app, preview, options = {}) {
  const {
    state,
    els,
    renderChannelQC,
    requestQcGridWindow,
    showWorkspace,
    renderMuExplorer,
    renderBidsAutoInfo,
    renderBidsMuscleFields,
    populateAuxSelector,
    renderAuxiliaryChannels,
    enableRoiSelection,
  } = app;
  const { skipMuData = false } = options;

  const {
    mean_abs,
    total_samples,
    grid_mean_abs,
    grid_names,
    rois,
    channel_means,
    coordinates,
    metadata,
    muscle,
    pulse_trains_full,
    pulse_trains_all,
    distime_all,
    mu_grid_index,
    auxiliary,
    auxiliary_names,
  } = preview;

  if (total_samples) {
    setSeriesLength(state, total_samples);
  }
  if (rois.length) {
    setRois(state, rois);
    if (els.nwindows) els.nwindows.value = String(state.rois.length);
  }
  if (grid_mean_abs && grid_names) {
    setGridSeries(state, grid_mean_abs);
    setGridNames(state, grid_names);
  }
  if (channel_means) {
    setChannelMeans(state, channel_means);
  }
  if (coordinates) {
    setCoordinates(state, coordinates);
  }
  setChannelTraces(state, []);
  if (metadata) {
    setMetadata(state, metadata);
  }
  if (muscle) {
    setMuscle(state, muscle);
  }
  if (!skipMuData) {
    const newPulseTrains =
      pulse_trains_full && pulse_trains_full.length
        ? pulse_trains_full
        : pulse_trains_all || state.muPulseTrains;
    const newDistimes = distime_all || state.muDistimes;
    const newGridIndex = mu_grid_index || state.muGridIndex;
    setMuPreviewData(state, newPulseTrains, newDistimes, newGridIndex);
  }
  if (auxiliary) {
    setAuxData(state, auxiliary, auxiliary_names || []);
    populateAuxSelector();
    renderAuxiliaryChannels();
    enableRoiSelection("auxCanvas");
  }
  ensureDiscardMasks(state);
  renderChannelQC();
  const roiStream = state.rois?.[0];
  requestQcGridWindow(
    getCurrentGrid(state),
    roiStart(roiStream),
    roiEnd(roiStream, state.seriesLength),
  );
  setPreviewSeries(state, mean_abs);
  drawGridOverlay(
    els.emgCanvas,
    state.gridSeries,
    state.gridColors,
    state.rois,
    state.seriesLength,
  );
  showWorkspace();
  renderMuExplorer();
  renderBidsAutoInfo();
  renderBidsMuscleFields();
}

/**
 * @param {{ api: App["api"], token: string, applyPreview: (preview: JsonObject) => void, onError: (err: unknown) => void }} deps
 */
async function hydrateBinaryDecomposePreview(deps) {
  const { api, token, applyPreview, onError } = deps;
  try {
    const preview = await api.fetchDecomposePreview(token);
    applyPreview(preview);
  } catch (err) {
    onError(err);
  }
}

/**
 * @param {App} app
 * @param {JsonObject} msg  One NDJSON progress event from the run stream.
 */
export function handleStreamMessage(app, msg) {
  const {
    state,
    els,
    api,
    setStatus,
    updateProgress,
    renderMuExplorer,
    autoSaveRunDecomposition,
  } = app;

  let pendingAutoSave = false;

  if (msg.stage === "error") {
    const detail = msg.detail
      ? `: ${typeof msg.detail === "string" ? msg.detail : JSON.stringify(msg.detail)}`
      : "";
    setStatus(`Error${detail}`, "error");
    updateProgress(0, msg.message || "Run failed", msg.stage);
    return;
  }

  if (msg.pct !== undefined) {
    updateProgress(msg.pct, msg.message || "", msg.stage);
  } else if (msg.message) {
    updateProgress(undefined, msg.message, msg.stage);
  }

  if (msg.preview) {
    if (msg.preview.preview_binary_token && api) {
      const previewNoToken = { ...msg.preview };
      delete previewNoToken.preview_binary_token;
      applyPreviewData(app, normalizePreviewPayload(previewNoToken), {
        skipMuData: true,
      });
      void hydrateBinaryDecomposePreview({
        api,
        token: msg.preview.preview_binary_token,
        applyPreview: (previewPayload) => {
          applyPreviewData(app, normalizePreviewPayload(previewPayload));
          if (pendingAutoSave) {
            renderMuExplorer();
            void autoSaveRunDecomposition();
          }
        },
        onError: (err) => {
          console.error(err);
          setStatus("Preview hydration failed", "error");
        },
      });
    } else {
      applyPreviewData(app, normalizePreviewPayload(msg.preview));
    }
  }

  if (msg.summary) {
    const { mu_count, grid_names, parameters } = msg.summary;
    const totalMu = Number.isFinite(mu_count)
      ? mu_count
      : state.muDistimes?.length || 0;
    const gridNames = Array.isArray(grid_names) ? grid_names : [];
    const previewMapping = Array.isArray(msg.preview?.mu_grid_index)
      ? msg.preview.mu_grid_index
      : [];
    const stateMapping = Array.isArray(state.muGridIndex)
      ? state.muGridIndex
      : [];
    const mapping = previewMapping.length ? previewMapping : stateMapping;
    const gridCount = Math.max(
      gridNames.length,
      mapping.length
        ? Math.max(
            ...mapping.map((/** @type {number} */ v) => Number(v) || 0),
          ) + 1
        : 0,
      totalMu > 0 ? 1 : 0,
    );
    const counts = new Array(gridCount).fill(0);
    for (let idx = 0; idx < totalMu; idx++) {
      const g = Number(mapping[idx]);
      const gridIdx = Number.isFinite(g) && g >= 0 && g < gridCount ? g : 0;
      counts[gridIdx] += 1;
    }
    const perGrid = counts.map((n, idx) => `Grid ${idx + 1}: ${n} MU`);
    const summaryText = `${perGrid.join(" • ")}${perGrid.length ? " • " : ""}Total: ${totalMu} MU`;
    if (els.progressText) els.progressText.textContent = summaryText;
    if (parameters) {
      setParameters(state, parameters);
    }
  }

  if (msg.stage === "done") {
    if (Array.isArray(state.muPulseTrains) && state.muPulseTrains.length) {
      renderMuExplorer();
      void autoSaveRunDecomposition();
    } else {
      pendingAutoSave = true;
    }
    setStatus("Complete", "success");
  }
}
