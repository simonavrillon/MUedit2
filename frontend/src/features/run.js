import {
  setIsRunning,
  setLastRunDownloadKey,
  setParameters,
  setRunDownloadInFlight,
  setUploadToken,
} from "../state/actions.js";

export async function autoDownloadRunDecomposition(deps) {
  const {
    state,
    els,
    getSuggestedNpzName,
    persistNpzBySaveTarget,
    getBidsMuscleNames,
    setStatus,
  } = deps;

  if (state.runDownloadInFlight) return;
  if (!state.muDistimes?.length) return;
  const fileBase = state.file?.name || "decomposition";
  const suggestedName = getSuggestedNpzName(fileBase, "_decomposition");
  const key = `${suggestedName}:${state.muDistimes.length}:${state.seriesLength || 0}`;
  if (state.lastRunDownloadKey === key) return;

  const muscleNames =
    typeof getBidsMuscleNames === "function" ? getBidsMuscleNames() : [];
  const totalSamples =
    state.seriesLength ||
    (state.muPulseTrains?.[0]?.length ?? 0) ||
    Math.max(
      0,
      ...state.muDistimes.flatMap((d) => (d || []).map((v) => Number(v) || 0)),
    ) + 1;
  const fs = Number(els.fsamp?.value);
  const payload = {
    distimes: state.muDistimes || [],
    pulse_trains: state.muPulseTrains || [],
    total_samples: totalSamples,
    fsamp: Number.isFinite(fs) && fs > 0 ? fs : null,
    grid_names: state.gridNames || ["Grid 1"],
    mu_grid_index: state.muGridIndex || [],
    parameters: state.parameters || {},
    muscle_names: muscleNames,
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
  } catch (err) {
    console.error(err);
    setStatus(`Save failed: ${err.message}`, "error");
  } finally {
    setRunDownloadInFlight(state, false);
  }
}

export async function runDecomposition(deps) {
  const {
    state,
    els,
    API_BASE,
    apiFetch,
    getBidsRoot,
    getBidsMuscleNames,
    buildParams,
    updateStartAvailability,
    switchStage,
    setStatus,
    updateProgressFn,
    handleStreamMessageFn,
  } = deps;

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

  setStatus("Running decomposition...", "muted");
  updateProgressFn(5, "Starting decomposition");

  // Build payload once per request attempt so retries can switch upload strategy
  // without mutating shared FormData instances.
  const buildRunFormData = (useToken = true) => {
    const formData = new FormData();
    if (useToken && state.uploadToken) {
      formData.append("upload_token", state.uploadToken);
    } else {
      formData.append("file", state.file);
    }

    formData.append("params", JSON.stringify(buildParams()));
    formData.append("persist_output", "false");
    if (state.discardMasks && state.discardMasks.length) {
      formData.append("discard_channels", JSON.stringify(state.discardMasks));
    }
    if (state.rois && state.rois.length) {
      formData.append("rois", JSON.stringify(state.rois));
    }

    const bidsRoot =
      typeof getBidsRoot === "function"
        ? String(getBidsRoot() || "").trim()
        : "";
    if (bidsRoot) {
      formData.append("bids_root", bidsRoot);
    }
    const bidsEntities = {};
    const subject = String(els.bidsSubject?.value || "").trim();
    const task = String(els.bidsTask?.value || "").trim();
    const session = String(els.bidsSession?.value || "").trim();
    const run = String(els.bidsRun?.value || "").trim();
    if (subject) bidsEntities.subject = subject;
    if (task) bidsEntities.task = task;
    if (session) bidsEntities.session = session;
    if (run) bidsEntities.run = run;
    const muscleNames =
      typeof getBidsMuscleNames === "function" ? getBidsMuscleNames() : [];
    if (muscleNames.length) bidsEntities.target_muscle = muscleNames;
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
      // Preferred path: reuse upload token from preview to avoid re-uploading the raw file.
      response = await apiFetch(
        `${API_BASE}/decompose_stream`,
        {
          method: "POST",
          headers: { "x-muedit-binary": "1" },
          body: buildRunFormData(true),
        },
        15 * 60 * 1000,
      );
    } catch (err) {
      const message = String(err?.message || "");
      if (state.uploadToken && message.includes("upload_token")) {
        // Token can expire after backend restart/session loss; retry once with full file upload.
        setUploadToken(state, null);
        updateProgressFn(5, "Session expired, retrying with file upload...");
        response = await apiFetch(
          `${API_BASE}/decompose_stream`,
          {
            method: "POST",
            headers: { "x-muedit-binary": "1" },
            body: buildRunFormData(false),
          },
          15 * 60 * 1000,
        );
      } else {
        throw err;
      }
    }

    if (!response.body) {
      throw new Error("No response body");
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    // Backend streams one JSON object per line. Keep the trailing partial line in `buffer`
    // until the next chunk arrives to avoid parse errors on split frames.
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
          handleStreamMessageFn(JSON.parse(line));
        } catch (e) {
          console.warn("Dropped malformed stream event", e);
          malformedEvents += 1;
        }
      }
    }
    if (buffer.trim()) {
      try {
        handleStreamMessageFn(JSON.parse(buffer));
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
    setStatus(`Error: ${err.message}`, "error");
    if (els.runPhase) els.runPhase.textContent = "Failed";
    if (els.progressText) {
      els.progressText.textContent = "Run failed. Check console for details.";
    }
    updateProgressFn(0, "Idle");
  } finally {
    setIsRunning(state, false);
    updateStartAvailability();
  }
}
