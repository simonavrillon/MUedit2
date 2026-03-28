import {
  setAuxData,
  setChannelMeans,
  setChannelTraces,
  setCoordinates,
  setGridNames,
  setGridSeries,
  setMetadata,
  setMuPreviewData,
  setMuscle,
  setParameters,
  setPreviewSeries,
  setRois,
  setSeriesLength,
} from "../state/actions.js";
import { decodeDecomposePreviewPayload } from "../api/binary_payloads.js";
import { normalizePreviewPayload } from "../contracts/payloads.js";

export function setRunPhase(els, pct, message = "", stage = "") {
  if (!els.runPhase) return;
  const stageText = typeof stage === "string" ? stage.trim().toLowerCase() : "";
  const messageText = typeof message === "string" ? message.trim() : "";
  const msgLower = messageText.toLowerCase();
  let phase = "Idle";

  if (stageText === "error" || messageText.toLowerCase().includes("error")) {
    phase = "Failed";
  } else if (stageText === "done") {
    phase = "Complete";
  } else if (msgLower.includes("loading")) {
    phase = "Loading";
  } else if (msgLower.includes("preprocess")) {
    phase = "Preprocessing";
  } else if (msgLower.includes("finalizing")) {
    phase = "Finalizing";
  } else if (msgLower.includes("grid")) {
    phase = "Decomposing";
  } else if (stageText) {
    phase = stageText.charAt(0).toUpperCase() + stageText.slice(1);
  } else if (typeof pct === "number" && pct > 0) {
    phase = "Running";
  }
  els.runPhase.textContent = phase;
}

export function updateProgress(deps, pct, message = "", stage = "") {
  const { els, setRunPhaseFn } = deps;
  if (els.progressBar && pct !== undefined) {
    const clamped = Math.max(0, Math.min(100, pct));
    els.progressBar.style.width = `${clamped}%`;
    setRunPhaseFn(clamped, message, stage);
  } else {
    setRunPhaseFn(pct, message, stage);
  }
  if (els.progressText) {
    els.progressText.textContent = message || "";
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
    updateProgressFn,
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
    updateProgressFn(0, msg.message || "Run failed", msg.stage);
    return;
  }

  if (msg.pct !== undefined) {
    updateProgressFn(msg.pct, msg.message || "", msg.stage);
  } else if (msg.message) {
    updateProgressFn(undefined, msg.message, msg.stage);
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
