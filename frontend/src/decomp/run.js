import {
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
import { decodeDecomposePreviewPayload } from "../api/binary_payloads.js";
import { normalizePreviewPayload } from "../api/payloads.js";

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
    updateProgress,
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
  updateProgress(5, "Starting decomposition");

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
        updateProgress(5, "Session expired, retrying with file upload...");
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
    updateProgress(0, "Idle");
  } finally {
    setIsRunning(state, false);
    updateStartAvailability();
  }
}

function applyPreviewData(deps, preview, options = {}) {
  const {
    state,
    els,
    ensureDiscardMasks,
    renderChannelQC,
    getCurrentGrid,
    requestQcGridWindow,
    drawGridOverlay,
    showWorkspace,
    renderMuExplorer,
    renderBidsAutoInfo,
    renderBidsMuscleFields,
    populateAuxSelector,
    renderAuxiliaryChannels,
    enableRoiSelection,
  } = deps;
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
  } = preview || {};

  if (total_samples) {
    setSeriesLength(state, total_samples);
  }
  if (rois && Array.isArray(rois) && rois.length) {
    setRois(
      state,
      rois.map((r) => ({ start: r[0] ?? r.start, end: r[1] ?? r.end })),
    );
    if (els.nwindows) els.nwindows.value = state.rois.length;
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
    // Preview messages may deliver MU fields incrementally; update each slice independently
    // while preserving previously populated slices.
    if (pulse_trains_full && pulse_trains_full.length) {
      setMuPreviewData(
        state,
        pulse_trains_full,
        state.muDistimes,
        state.muGridIndex,
      );
    } else if (pulse_trains_all) {
      setMuPreviewData(
        state,
        pulse_trains_all,
        state.muDistimes,
        state.muGridIndex,
      );
    }
    if (distime_all) {
      setMuPreviewData(
        state,
        state.muPulseTrains,
        distime_all,
        state.muGridIndex,
      );
    }
    if (mu_grid_index) {
      setMuPreviewData(
        state,
        state.muPulseTrains,
        state.muDistimes,
        mu_grid_index,
      );
    }
  }
  if (auxiliary) {
    setAuxData(state, auxiliary, auxiliary_names || []);
    populateAuxSelector();
    renderAuxiliaryChannels();
    enableRoiSelection("auxCanvas");
  }
  ensureDiscardMasks();
  renderChannelQC();
  const roiStream = state.rois?.[0];
  requestQcGridWindow(
    getCurrentGrid(),
    Number.isFinite(roiStream?.start) ? roiStream.start : 0,
    Number.isFinite(roiStream?.end) ? roiStream.end : state.seriesLength,
  );
  setPreviewSeries(state, mean_abs);
  const emgCanvas = els?.emgCanvas || "emgCanvas";
  drawGridOverlay(
    emgCanvas,
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

async function hydrateBinaryDecomposePreview(deps) {
  const { apiFetch, API_BASE, token, applyPreview, onError } = deps;
  try {
    const res = await apiFetch(
      `${API_BASE}/decompose_preview/${encodeURIComponent(token)}`,
      {
        method: "GET",
        headers: { Accept: "application/octet-stream" },
      },
      120000,
    );
    const payload = await res.arrayBuffer();
    // Binary hydration carries the full MU preview payload after the lightweight stream event.
    applyPreview(
      normalizePreviewPayload(
        decodeDecomposePreviewPayload(
          payload,
          res.headers.get("x-muedit-format"),
        ),
      ),
    );
  } catch (err) {
    onError(err);
  }
}

export function handleStreamMessage(deps, msg) {
  const {
    state,
    els,
    apiFetch,
    API_BASE,
    setStatus,
    updateProgress,
    ensureDiscardMasks,
    renderChannelQC,
    getCurrentGrid,
    requestQcGridWindow,
    drawGridOverlay,
    showWorkspace,
    renderMuExplorer,
    renderBidsAutoInfo,
    renderBidsMuscleFields,
    populateAuxSelector,
    renderAuxiliaryChannels,
    enableRoiSelection,
  } = deps;

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
    const commonPreviewDeps = {
      state,
      els,
      ensureDiscardMasks,
      renderChannelQC,
      getCurrentGrid,
      requestQcGridWindow,
      drawGridOverlay,
      showWorkspace,
      renderMuExplorer,
      renderBidsAutoInfo,
      renderBidsMuscleFields,
      populateAuxSelector,
      renderAuxiliaryChannels,
      enableRoiSelection,
    };

    if (
      msg.preview.preview_binary_token &&
      typeof apiFetch === "function" &&
      API_BASE
    ) {
      // Fast path: render immediate non-MU preview fields from stream event, then hydrate
      // heavy MU arrays asynchronously using the token endpoint.
      const previewNoToken = { ...msg.preview };
      delete previewNoToken.preview_binary_token;
      applyPreviewData(
        commonPreviewDeps,
        normalizePreviewPayload(previewNoToken),
        {
          skipMuData: true,
        },
      );
      void hydrateBinaryDecomposePreview({
        apiFetch,
        API_BASE,
        token: msg.preview.preview_binary_token,
        applyPreview: (previewPayload) =>
          applyPreviewData(
            commonPreviewDeps,
            normalizePreviewPayload(previewPayload),
          ),
        onError: (err) => {
          console.error(err);
          setStatus("Preview hydration failed", "error");
        },
      });
    } else {
      applyPreviewData(commonPreviewDeps, normalizePreviewPayload(msg.preview));
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
      mapping.length ? Math.max(...mapping.map((v) => Number(v) || 0)) + 1 : 0,
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
    if (els.progressText) {
      els.progressText.textContent = summaryText;
    }
    if (parameters) {
      setParameters(state, parameters);
    }
  }

  if (msg.stage === "done") {
    if (Array.isArray(state.muPulseTrains) && state.muPulseTrains.length) {
      // Finalize explorer selection/render after stream completion so the first
      // detected MU is shown immediately when at least one MU exists.
      renderMuExplorer();
    }
    setStatus("Complete", "success");
  }
}
