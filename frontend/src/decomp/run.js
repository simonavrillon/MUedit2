import {
  setIsRunning,
  setLastRunDownloadKey,
  setParameters,
  setRunDownloadInFlight,
  setRunLive,
  setRunResultToken,
} from "../state/actions.js";
import { getSuggestedNpzName } from "../io/bids.js";
import { errorMessage, handleError } from "../app/services/error-service.js";
import {
  applyRunEvent,
  buildRunSummary,
  createRunLive,
  failRun,
  failSave,
  finishRun,
  finishSave,
  startSave,
} from "./live.js";
import { renderRunTime, updateRunDots } from "../view/run-live.js";
import { readNdjson } from "../api/ndjson.js";
import { withUpload } from "../app/services/upload.js";

/** @typedef {import("../app/context.js").App} App */
/** @typedef {import("../app/context.js").JsonObject} JsonObject */

/** How often the elapsed time and estimate refresh during a run. */
const CLOCK_TICK_MS = 1000;
/** How long a run may take to start streaming. */
const RUN_START_TIMEOUT_MS = 15 * 60 * 1000;

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

  const payload = {
    run_result_token: token,
    total_samples: state.seriesLength || 0,
    fsamp: state.fsamp,
    grid_names: state.gridNames,
    mu_grid_index: live.summary.muGridIndex,
    parameters: state.parameters || {},
    muscle: getBidsMuscleNames(),
    artifact_regions: state.artifactRegions,
    file_label: suggestedName,
  };
  setRunDownloadInFlight(state, true);
  startSave(live);
  renderRunStage();
  try {
    setStatus("Saving decomposition...", "muted");
    const saved = await persistNpzBySaveTarget(payload, suggestedName);
    setLastRunDownloadKey(state, key);
    finishSave(live, saved.path);
    setStatus(
      saved.path
        ? `Decomposition saved to ${saved.path}`
        : "Decomposition saved",
      "success",
    );
    // Preloaded for the Edit step; the page stays on the run's result.
    if (saved.path) {
      void loadDecompositionForEditByPath(saved.path, { open: false });
    }
  } catch (err) {
    handleError(err, setStatus, "Save failed");
    failSave(live, errorMessage(err));
  } finally {
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
    checkSessionForm,
  } = app;

  if (state.isRunning) {
    setStatus("Decomposition already running", "muted");
    return;
  }
  if (!state.file) {
    setStatus("Select an EMG data file first", "error");
    return;
  }
  if (!checkSessionForm()) {
    setStatus("Fill in the session info marked in red", "error");
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
  const clock = globalThis.setInterval(
    () => renderRunTime(app.els, state.runLive, Date.now()),
    CLOCK_TICK_MS,
  );

  const buildRunFormData = (/** @type {string} */ token) => {
    const formData = new FormData();
    formData.append("upload_token", token);

    formData.append("params", JSON.stringify(buildParams()));
    formData.append("persist_output", "false");
    if (state.discardMasks.length) {
      formData.append("discard_channels", JSON.stringify(state.discardMasks));
    }
    if (state.rois.length) {
      formData.append("rois", JSON.stringify(state.rois));
    }
    if (state.artifactRegions.length) {
      formData.append(
        "artifact_regions",
        JSON.stringify(state.artifactRegions),
      );
    }

    const project = getBidsProject();
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
    const response = await withUpload(app, (token) =>
      api.decomposeStream(buildRunFormData(token), RUN_START_TIMEOUT_MS),
    );
    // A reloaded file reported itself on the status line.
    setStatus("Running decomposition...", "muted");

    if (!response.body) {
      throw new Error("No response body");
    }

    let malformedEvents = 0;
    /** @param {unknown} err */
    const dropEvent = (err) => {
      console.warn("Dropped malformed stream event", err);
      malformedEvents += 1;
    };
    for await (const msg of readNdjson(response.body, dropEvent)) {
      try {
        handleStreamMessage(msg);
      } catch (err) {
        dropEvent(err);
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
      failRun(
        state.runLive,
        "The decomposition stopped without a result",
        Date.now(),
      );
    }
  } catch (err) {
    handleError(err, setStatus, "Error");
    if (state.runLive) failRun(state.runLive, errorMessage(err), Date.now());
  } finally {
    globalThis.clearInterval(clock);
    setIsRunning(state, false);
    updateStartAvailability();
    renderRunStage();
  }
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
 * @param {JsonObject} msg  One NDJSON progress event from the run stream.
 */
export function handleStreamMessage(app, msg) {
  const {
    state,
    setStatus,
    renderRunStage,
    scheduleRunRender,
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
    failRun(live, `${msg.message || "Run failed"}${detail}`, Date.now());
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
  if (change) updateRunDots(app.els, live, change);

  if (msg.stage !== "done") {
    if (change || msg.phase) scheduleRunRender();
    return;
  }

  // The rest of the preview is the recording the page already shows; the
  // session form the user filled in stays as it is.
  if (msg.preview) setRunResultToken(state, msg.preview.run_result_token);
  if (msg.summary?.parameters) setParameters(state, msg.summary.parameters);
  finishRun(
    live,
    msg.summary ? buildRunSummary(msg.summary, msg.preview) : null,
    Date.now(),
  );
  setStatus("Complete", "success");
  updateStepAvailability();
  renderRunStage();
  void autoSaveRunDecomposition();
}
