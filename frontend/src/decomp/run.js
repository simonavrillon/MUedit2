/**
 * Starting, streaming, cancelling and auto-saving a decomposition run.
 * Stream events are applied by `handleStreamMessage` through `live.js`.
 */
import {
  setIsRunning,
  setSavedRunToken,
  setParameters,
  setRunLive,
  setRunResultToken,
} from "../state/actions.js";
import { getSuggestedNpzName } from "../io/bids.js";
import { errorMessage, handleError } from "../app/services/error-service.js";
import {
  applyRunEvent,
  createRunLive,
  failRun,
  failSave,
  finishRun,
  finishSave,
  startSave,
} from "./live.js";
import { renderRunTime, updateRunDots } from "../view/run-live.js";
import { readNdjson } from "../api/ndjson.js";
import { toRunSummary } from "../api/payloads.js";
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
    saveRun,
    getBidsMuscleNames,
    setStatus,
    loadDecompositionForEditByPath,
    renderRunStage,
  } = app;
  const live = state.runLive;

  if (!live?.summary?.nMu || live.saving) return;
  // The server kept this run's pulse trains and discharge times under its token.
  const token = state.runResultToken;
  if (!token || state.savedRunToken === token) return;
  const fileBase = state.file?.name || "decomposition";
  const suggestedName = getSuggestedNpzName(fileBase, "_decomposition");

  const payload = {
    run_result_token: token,
    total_samples: state.totalSamples || 0,
    fsamp: state.fsamp,
    grid_names: state.gridNames,
    mu_grid_index: live.summary.muGridIndex,
    parameters: state.parameters || {},
    muscle_names: getBidsMuscleNames(),
    artifact_regions: state.artifactRegions,
    file_label: suggestedName,
  };
  startSave(live);
  renderRunStage();
  try {
    setStatus("Saving decomposition...", "muted");
    const saved = await saveRun(payload, suggestedName);
    setSavedRunToken(state, token);
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
  const params = buildParams();
  setParameters(state, params);
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

  const buildRunPayload = (/** @type {string} */ token) => {
    const bidsEntities = collectBidsEntities();
    return {
      upload_token: token,
      params,
      discard_channels: state.discardChannels.length
        ? state.discardChannels
        : null,
      rois: state.rois.length ? state.rois : null,
      artifact_regions: state.artifactRegions.length
        ? state.artifactRegions
        : null,
      // The backend segments the same channel again to write the target's events.
      target_index:
        state.segments.length && state.targetIndex !== null
          ? state.targetIndex
          : null,
      project: getBidsProject() || null,
      bids_entities: Object.keys(bidsEntities).length ? bidsEntities : null,
    };
  };

  try {
    const response = await withUpload(app, (token) =>
      api.decomposeStream(buildRunPayload(token), RUN_START_TIMEOUT_MS),
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
    switchStage,
  } = app;
  const live = state.runLive;
  if (!live) return;

  if (msg.stage === "error") {
    const detail = msg.detail ? `: ${msg.detail}` : "";
    setStatus(`Error${detail}`, "error");
    failRun(live, `${msg.message || "Run failed"}${detail}`, Date.now());
    renderRunStage();
    return;
  }

  if (msg.stage === "cancelled") {
    setStatus("Decomposition cancelled", "muted");
    setRunLive(state, null);
    // The run page is empty without a run; QC is where the next one starts.
    if (state.currentStage === "run") switchStage("qc");
    else updateStepAvailability();
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
  if (msg.preview)
    setRunResultToken(state, String(msg.preview.run_result_token ?? ""));
  if (msg.summary?.parameters) setParameters(state, msg.summary.parameters);
  finishRun(
    live,
    msg.summary ? toRunSummary(msg.summary, msg.preview) : null,
    Date.now(),
  );
  setStatus("Complete", "success");
  updateStepAvailability();
  renderRunStage();
  void autoSaveRunDecomposition();
}
