import {
  ensureDiscardMasks,
  setArtifactMode,
  setArtifactRegions,
  setAuxData,
  setChannelMeans,
  setChannelTraceForGrid,
  setChannelTraces,
  setCoordinates,
  setDiscardMasks,
  setFsamp,
  setGridNames,
  setGridSeries,
  setMetadata,
  setMuscle,
  setQcWindowLoading,
  setQcWindowLoadingForGrid,
  setRois,
  setSeriesLength,
  setUploadToken,
} from "../state/actions.js";
import { getCurrentGrid, roiStart, roiEnd } from "../state/selectors.js";
import { nextFrame } from "../view/plots.js";
import { errorMessage } from "../app/services/error-service.js";
import { toSpans } from "../api/payloads.js";
import { OVERVIEW_BINS, QC_TRACE_BINS } from "../config.js";

/** @typedef {import("../app/context.js").App} App */
/** @typedef {import("../app/state.js").State} State */

/**
 * The analysis window a new drag should replace. A drag over a drawn window
 * adjusts the one it overlaps most; otherwise it fills the first window not
 * drawn yet (a placeholder spanning the whole recording); once every window is
 * drawn, the one starting nearest the drag.
 *
 * @param {{ start: number, end: number }[]} rois
 * @param {{ start: number, end: number }} span
 * @param {number} total
 */
export function pickRoiSlot(rois, span, total) {
  const drawn = (/** @type {{ start: number, end: number }} */ r) =>
    !(r.start <= 0 && r.end >= total);
  let overlapIdx = -1;
  let overlapBest = 0;
  rois.forEach((r, i) => {
    if (!drawn(r)) return;
    const overlap = Math.min(r.end, span.end) - Math.max(r.start, span.start);
    if (overlap > overlapBest) {
      overlapBest = overlap;
      overlapIdx = i;
    }
  });
  if (overlapIdx >= 0) return overlapIdx;
  const empty = rois.findIndex((r) => !drawn(r));
  if (empty >= 0) return empty;
  let idx = 0;
  let best = Number.MAX_SAFE_INTEGER;
  rois.forEach((r, i) => {
    const dist = Math.abs(r.start - span.start);
    if (dist < best) {
      best = dist;
      idx = i;
    }
  });
  return idx;
}

/**
 * @param {State} state
 * @param {number} nwin
 */
export function syncRois(state, nwin) {
  if (!state.rois) state.rois = [];
  if (state.rois.length > nwin) state.rois = state.rois.slice(0, nwin);
  while (state.rois.length < nwin) {
    state.rois.push({ start: 0, end: state.seriesLength || 0 });
  }
}

/**
 * Run the automatic QC pipeline server-side and load the result into the
 * interactive controls: detected bad channels replace the discard masks (click
 * a cell to correct one) and detected artifacts replace the artifact windows
 * (+/- to correct those). Nothing is sent to the decomposition here — the
 * corrected masks travel with the run request.
 */
/** @param {App} app */
export async function requestAutoQc(app) {
  const { state, els, api, setStatus, renderChannelQC, refreshVisuals } = app;

  if (!state.uploadToken) {
    setStatus("Load a signal first", "error");
    return false;
  }

  if (els?.qcAutoBtn) els.qcAutoBtn.disabled = true;
  setStatus("Running automatic QC...", "muted");

  try {
    const data = await api.runAutoQc({ upload_token: state.uploadToken });
    if (Array.isArray(data?.bad_channels_per_grid)) {
      setDiscardMasks(state, data.bad_channels_per_grid);
    }
    setArtifactRegions(state, toSpans(data?.artifact_regions));
    setArtifactMode(state, false);
    setChannelTraces(state, []);

    const nBad = (state.discardMasks || []).reduce(
      (sum, grid) =>
        sum + (grid || []).reduce((acc, v) => acc + (v ? 1 : 0), 0),
      0,
    );
    const nArtifact = state.artifactRegions.length;

    await renderChannelQC(false);
    refreshVisuals();
    setStatus(
      `Automatic QC: ${nBad} bad channel(s), ${nArtifact} artifact window(s)`,
      "success",
    );
    return true;
  } catch (err) {
    console.error(err);
    setStatus(`Automatic QC failed: ${errorMessage(err)}`, "error");
    return false;
  } finally {
    if (els?.qcAutoBtn) els.qcAutoBtn.disabled = false;
  }
}

/**
 * @param {App} app
 * @param {number} gridIdx
 * @param {number} [start]
 * @param {number | null} [end]
 */
export async function requestQcGridWindow(app, gridIdx, start, end) {
  const { state, api, renderChannelQC, setStatus } = app;
  const s = Number.isFinite(start) ? start : 0;
  const e = Number.isFinite(end) ? end : state.seriesLength;

  if (!state.uploadToken || !Number.isFinite(gridIdx) || gridIdx < 0) return;
  if (state.qcWindowLoading?.[gridIdx]) return;
  setQcWindowLoadingForGrid(state, gridIdx, true);

  try {
    const { rows } = await api.fetchSeries("emg", {
      upload_token: state.uploadToken,
      grid: gridIdx,
      start: s,
      end: e ?? 0,
      bins: QC_TRACE_BINS,
    });
    setChannelTraceForGrid(state, gridIdx, rows);
    if (gridIdx === state.currentGrid) {
      renderChannelQC();
    }
  } catch (err) {
    console.error(err);
    if (typeof setStatus === "function") {
      setStatus(`QC window update failed: ${errorMessage(err)}`, "error");
    }
  } finally {
    setQcWindowLoadingForGrid(state, gridIdx, false);
  }
}

/**
 * @param {App} app
 * @param {{ silentFailure?: boolean, filepath?: string | null }} [options]
 */
export async function requestPreview(app, options = {}) {
  const { silentFailure = false, filepath = null } = options;
  const {
    state,
    els,
    api,
    setUploadLoading,
    populateAuxSelector,
    populateGridTabs,
    requestQcGridWindow,
    enableRoiSelection,
    renderBidsAutoInfo,
    renderBidsMuscleFields,
    setStatus,
    showWorkspace,
    switchStage,
    refreshVisuals,
    renderChannelQC,
    applyPreviewMetadata,
  } = app;

  if (!filepath) return false;
  setUploadLoading(true);

  try {
    const data = await api.fetchPreviewByPath(filepath);
    setUploadToken(state, data.upload_token || null);
    // The whole-recording traces come as envelopes, sized for the canvases.
    const whole = { upload_token: data.upload_token, bins: OVERVIEW_BINS };
    const [overview, aux] = await Promise.all([
      api.fetchSeries("overview", whole),
      api.fetchSeries("aux", whole),
    ]);
    setGridSeries(state, overview.rows);
    setGridNames(state, data.grid_names || []);
    setSeriesLength(state, data.total_samples);
    setChannelMeans(state, data.channel_means || []);
    setCoordinates(state, data.coordinates || []);
    setChannelTraces(state, []);
    setQcWindowLoading(state, {});
    setMetadata(state, data.metadata || {});
    setMuscle(state, data.muscle || []);
    setAuxData(state, aux.rows, data.auxiliary_names || []);
    setFsamp(state, data.fsamp);
    applyPreviewMetadata(data);
    populateAuxSelector();
    ensureDiscardMasks(state);
    populateGridTabs();
    const nwin = Number(els.nwindows?.value) || 1;
    const defaultEnd = state.seriesLength || 0;
    const rois = [];
    for (let i = 0; i < nwin; i++) {
      rois.push({ start: 0, end: defaultEnd });
    }
    setRois(state, rois);
    setArtifactRegions(state, []);
    setArtifactMode(state, false);
    const roiPreview = state.rois?.[0];
    await requestQcGridWindow(
      getCurrentGrid(state),
      roiStart(roiPreview),
      roiEnd(roiPreview, state.seriesLength),
    );
    enableRoiSelection("emgCanvas");
    enableRoiSelection("auxCanvas");
    // Raw preview resets edit slice first; ensure BIDS rows render in QC context
    // so they source run grid names instead of edit fallback ("Grid 1").
    switchStage("qc");
    renderBidsAutoInfo();
    renderBidsMuscleFields();

    setStatus("Preview ready", "success");
    showWorkspace({ keepLandingVisible: true });
    await nextFrame();
    refreshVisuals();
    await renderChannelQC(true);
    els.landing?.classList.add("hidden");
    return true;
  } catch (err) {
    console.error(err);
    setUploadToken(state, null);
    if (!silentFailure) {
      setStatus("Preview failed", "error");
    }
    return false;
  } finally {
    setUploadLoading(false);
  }
}
