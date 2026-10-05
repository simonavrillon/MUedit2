import {
  ensureDiscardMasks,
  setChannelMeans,
  setChannelTraces,
  setCoordinates,
  setGridNames,
  setIsRunning,
  setLastRunDownloadKey,
  setMetadata,
  setMuscle,
  setParameters,
  setRois,
  setRunDownloadInFlight,
  setRunLive,
  setRunResultToken,
  setSeriesLength,
  setUploadToken,
} from "../state/actions.js";
import { getCurrentGrid, roiStart, roiEnd } from "../state/selectors.js";
import { normalizePreviewPayload } from "../api/payloads.js";
import { getSuggestedNpzName } from "../io/bids.js";
import { drawGridOverlay } from "../view/plots.js";
import { traceColors } from "../config.js";
import { errorMessage, handleError } from "../app/services/error-service.js";
import { applyRunEvent, buildRunSummary, createRunLive } from "./live.js";

/** @typedef {import("../app/context.js").App} App */
/** @typedef {import("../app/context.js").JsonObject} JsonObject */
/** @typedef {import("../api/payloads.js").PreviewPayload} PreviewPayload */

/** How often the elapsed time and estimate refresh during a run. */
const CLOCK_TICK_MS = 1000;

/** @param {App} app */
export async function autoSaveRunDecomposition(app) {
  const {
    state,
    persistNpzBySaveTarget,
    getBidsMuscleNames,
    setStatus,
    loadDecompositionForEditByPath,
    renderRunStage,
  } = app;
  const live = state.runLive;

  if (state.runDownloadInFlight || !live?.summary?.muCount) return;
  // The server kept this run's pulse trains and discharge times under its token.
  const token = state.runResultToken;
  if (!token) return;
  const fileBase = state.file?.name || "decomposition";
  const suggestedName = getSuggestedNpzName(fileBase, "_decomposition");
  const key = `${suggestedName}:${token}`;
  if (state.lastRunDownloadKey === key) return;

  const fs = state.fsamp;
  const payload = {
    run_result_token: token,
    total_samples: state.seriesLength || 0,
    fsamp: fs != null && Number.isFinite(fs) && fs > 0 ? fs : null,
    grid_names: state.gridNames || ["Grid 1"],
    mu_grid_index: live.summary.muGridIndex,
    parameters: state.parameters || {},
    muscle: getBidsMuscleNames(),
    artifact_regions: state.artifactRegions || [],
    file_label: suggestedName,
  };
  setRunDownloadInFlight(state, true);
  live.saving = true;
  live.saveError = "";
  renderRunStage();
  try {
    setStatus("Saving decomposition...", "muted");
    const saved = await persistNpzBySaveTarget(payload, suggestedName);
    setLastRunDownloadKey(state, key);
    live.savedPath = saved?.path || "";
    setStatus(
      saved?.path
        ? `Decomposition saved to ${saved.path}`
        : "Decomposition saved",
      "success",
    );
    // Preloaded for the Edit step; the page stays on the run's result.
    if (saved?.path) {
      void loadDecompositionForEditByPath(saved.path, { open: false });
    }
  } catch (err) {
    handleError(err, setStatus, "Save failed");
    live.saveError = errorMessage(err);
  } finally {
    live.saving = false;
    setRunDownloadInFlight(state, false);
    renderRunStage();
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
    handleStreamMessage,
    renderRunStage,
    renderRunClock,
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
  setParameters(state, buildParams());
  setLastRunDownloadKey(state, "");
  setRunResultToken(state, "");
  setRunLive(state, createRunLive(Date.now()));
  updateStartAvailability();
  switchStage("run");
  renderRunStage();
  setStatus("Running decomposition...", "muted");
  const clock = globalThis.setInterval(renderRunClock, CLOCK_TICK_MS);

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
      setStatus("Session expired, reloading file...", "muted");
      const preview = await api.fetchPreviewByPath(sourcePath);
      if (!preview?.upload_token) throw err;
      setUploadToken(state, preview.upload_token);
      setStatus("Running decomposition...", "muted");
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
    // A stream that ends without `done`, `error` or `cancelled` left the run unfinished.
    if (state.runLive?.status === "running") {
      failRun(app, "The decomposition stopped without a result");
    }
  } catch (err) {
    handleError(err, setStatus, "Error");
    failRun(app, errorMessage(err));
  } finally {
    globalThis.clearInterval(clock);
    setIsRunning(state, false);
    updateStartAvailability();
    renderRunStage();
  }
}

/**
 * @param {App} app
 * @param {string} message
 */
function failRun(app, message) {
  const live = app.state.runLive;
  if (!live) return;
  live.status = "failed";
  live.error = message;
  live.finishedAt = Date.now();
}

/** @param {App} app */
export async function cancelDecomposition(app) {
  const { state, els, api, setStatus } = app;
  if (!state.isRunning) return;
  if (els.cancelRun) els.cancelRun.disabled = true;
  try {
    // The run's stream ends with a `cancelled` event, which resets the page.
    await api.cancelDecomposition();
    setStatus("Cancelling decomposition...", "muted");
  } catch (err) {
    handleError(err, setStatus, "Cancel failed");
    if (els.cancelRun) els.cancelRun.disabled = false;
  }
}

/**
 * @param {App} app
 * @param {PreviewPayload} preview
 */
function applyPreviewData(app, preview) {
  const {
    state,
    els,
    renderChannelQC,
    requestQcGridWindow,
    showWorkspace,
    renderBidsAutoInfo,
    renderBidsMuscleFields,
    populateAuxSelector,
    renderAuxiliaryChannels,
    enableRoiSelection,
  } = app;

  // The overview and aux traces stay the upload's envelopes (/series/*); the
  // run preview's copies of them are not used.
  const {
    total_samples,
    grid_names,
    rois,
    channel_means,
    coordinates,
    metadata,
    muscle,
  } = preview;

  if (total_samples) {
    setSeriesLength(state, total_samples);
  }
  if (rois.length) {
    setRois(state, rois);
    if (els.nwindows) els.nwindows.value = String(state.rois.length);
  }
  if (grid_names) {
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
  populateAuxSelector();
  renderAuxiliaryChannels();
  enableRoiSelection("auxCanvas");
  ensureDiscardMasks(state);
  renderChannelQC();
  const roiStream = state.rois?.[0];
  requestQcGridWindow(
    getCurrentGrid(state),
    roiStart(roiStream),
    roiEnd(roiStream, state.seriesLength),
  );
  drawGridOverlay(
    els.emgCanvas,
    state.gridSeries,
    traceColors(),
    state.rois,
    state.seriesLength,
  );
  showWorkspace();
  renderBidsAutoInfo();
  renderBidsMuscleFields();
}

/**
 * @param {App} app
 * @param {JsonObject} msg  One NDJSON progress event from the run stream.
 */
export function handleStreamMessage(app, msg) {
  const {
    state,
    setStatus,
    renderRunStage,
    scheduleRunRender,
    updateRunDots,
    autoSaveRunDecomposition,
    updateStepAvailability,
  } = app;
  const live = state.runLive;
  if (!live) return;

  if (msg.stage === "error") {
    const detail = msg.detail
      ? `: ${typeof msg.detail === "string" ? msg.detail : JSON.stringify(msg.detail)}`
      : "";
    setStatus(`Error${detail}`, "error");
    failRun(app, `${msg.message || "Run failed"}${detail}`);
    renderRunStage();
    return;
  }

  if (msg.stage === "cancelled") {
    setStatus("Decomposition cancelled", "muted");
    setRunLive(state, null);
    renderRunStage();
    return;
  }

  // Progress events can arrive faster than frames: the dots they change are
  // restyled now, and the rest of the page once per frame.
  const change = applyRunEvent(live, msg, Date.now());
  if (change) updateRunDots(change);

  if (msg.stage !== "done") {
    if (change || msg.phase) scheduleRunRender();
    return;
  }

  if (msg.preview) {
    setRunResultToken(state, msg.preview.run_result_token);
    applyPreviewData(app, normalizePreviewPayload(msg.preview));
  }
  if (msg.summary) {
    live.summary = buildRunSummary(msg.summary, msg.preview);
    if (msg.summary.parameters) setParameters(state, msg.summary.parameters);
  }
  live.phase = "save";
  live.status = "done";
  live.finishedAt = Date.now();
  setStatus("Complete", "success");
  updateStepAvailability();
  renderRunStage();
  void autoSaveRunDecomposition();
}
