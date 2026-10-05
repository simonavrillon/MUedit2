import {
  ensureDiscardMasks,
  setArtifactMode,
  setArtifactRegions,
  setAuxData,
  setChannelMeans,
  setChannelTraceForGrid,
  setCoordinates,
  setDiscardMasks,
  setFsamp,
  setGridNames,
  setGridSeries,
  setMetadata,
  setMuscle,
  setRois,
  setSeriesLength,
  setUploadToken,
} from "../state/actions.js";
import { getCurrentGrid, roiStart, roiEnd } from "../state/selectors.js";
import { handleError } from "../app/services/error-service.js";
import { normalizePreviewPayload, toSpans } from "../api/payloads.js";
import { createViewFetcher } from "../app/services/view-fetcher.js";
import { withUpload } from "../app/services/upload.js";
import { beginRawPreviewTransition } from "../state/transitions.js";
import { OVERVIEW_BINS, QC_TRACE_BINS } from "../config.js";

/** @typedef {import("../app/context.js").App} App */
/** @typedef {import("../state/state.js").State} State */
/** @typedef {import("../state/state.js").FileRef} FileRef */

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
    const data = await withUpload(app, (token) =>
      api.runAutoQc({ upload_token: token }),
    );
    if (Array.isArray(data?.bad_channels_per_grid)) {
      setDiscardMasks(state, data.bad_channels_per_grid);
    }
    setArtifactRegions(state, toSpans(data?.artifact_regions));
    setArtifactMode(state, false);

    const nBad = (state.discardMasks || []).reduce(
      (sum, grid) =>
        sum + (grid || []).reduce((acc, v) => acc + (v ? 1 : 0), 0),
      0,
    );
    const nArtifact = state.artifactRegions.length;

    // The channels' traces stand; only which ones are discarded changed.
    renderChannelQC();
    refreshVisuals();
    setStatus(
      `Automatic QC: ${nBad} bad channel(s), ${nArtifact} artifact window(s)`,
      "success",
    );
    return true;
  } catch (err) {
    handleError(err, setStatus, "Automatic QC failed");
    return false;
  } finally {
    if (els?.qcAutoBtn) els.qcAutoBtn.disabled = false;
  }
}

/**
 * Keeps each grid's channel traces on the window the grid should show: its
 * channels over the first analysis window. Asking again is cheap; a grid whose
 * traces already cover that window sends nothing, and traces that arrive
 * after the window moved on are dropped.
 *
 * @param {App} app
 * @returns {() => void} Ask for the current grid's traces.
 */
export function createQcTraces(app) {
  /** @type {Map<number, string>} The window each grid last asked for. */
  const wanted = new Map();
  /** @type {Map<number, string>} The window each grid's traces cover. */
  const shown = new Map();
  const fetcher = createViewFetcher(
    async (
      /** @type {{ key: string, grid: number, start: number, end: number }} */ p,
    ) => ({
      p,
      view: await withUpload(app, (token) =>
        app.api.fetchSeries("emg", {
          upload_token: token,
          grid: p.grid,
          start: p.start,
          end: p.end,
          bins: QC_TRACE_BINS,
        }),
      ),
    }),
    ({ p, view }) => {
      if (wanted.get(p.grid) !== p.key) return;
      setChannelTraceForGrid(app.state, p.grid, view.rows);
      shown.set(p.grid, p.key);
      const { currentGrid, currentStage } = app.state;
      if (p.grid === currentGrid && currentStage === "qc")
        app.renderChannelQC();
    },
    (err) => handleError(err, app.setStatus, "QC window update failed"),
  );

  return function ensureQcTraces() {
    const { state } = app;
    const token = state.uploadToken;
    if (!token) return;
    const grid = getCurrentGrid(state);
    const roi = state.rois[0];
    const start = roiStart(roi);
    const end = roiEnd(roi, state.seriesLength ?? 0);
    const key = `${token}:${grid}:${start}:${end}`;
    wanted.set(grid, key);
    if (shown.get(grid) === key && state.channelTraces[grid]?.length) return;
    fetcher.want(key, { key, grid, start, end });
  };
}

/**
 * Open a raw file. Its preview replaces the file shown only once it has all
 * arrived: a file that fails to open leaves the current one, its edit session
 * included, as it was.
 *
 * @param {App} app
 * @param {{ file: FileRef & { path: string }, silentFailure?: boolean }} options
 */
export async function requestPreview(app, { file, silentFailure = false }) {
  const {
    state,
    els,
    api,
    setUploadLoading,
    populateAuxSelector,
    populateGridTabs,
    renderBidsAutoInfo,
    renderBidsMuscleFields,
    setStatus,
    showWorkspace,
    applyPreviewMetadata,
  } = app;

  setUploadLoading(true);

  try {
    const data = normalizePreviewPayload(
      await api.fetchPreviewByPath(file.path),
    );
    // The whole-recording traces come as envelopes, sized for the canvases.
    const whole = { upload_token: data.upload_token, bins: OVERVIEW_BINS };
    const [overview, aux] = await Promise.all([
      api.fetchSeries("overview", whole),
      api.fetchSeries("aux", whole),
    ]);
    beginRawPreviewTransition(state, file);
    app.resetSessionForm(file.name ?? "");
    setUploadToken(state, data.upload_token || null);
    setGridSeries(state, overview.rows);
    setGridNames(state, data.grid_names);
    setSeriesLength(state, data.total_samples);
    setChannelMeans(state, data.channel_means);
    setCoordinates(state, data.coordinates);
    setMetadata(state, data.metadata);
    setMuscle(state, data.muscle);
    setAuxData(state, aux.rows, data.auxiliary_names);
    setFsamp(state, data.fsamp);
    applyPreviewMetadata(data);
    populateAuxSelector();
    ensureDiscardMasks(state);
    populateGridTabs();
    // Every window starts as the whole recording, waiting for a drag.
    setRois(state, []);
    syncRois(state, Number(els.nwindows?.value) || 1);
    setArtifactRegions(state, []);
    setArtifactMode(state, false);
    // Raw preview resets edit slice first; ensure BIDS rows render in QC context
    // so they source run grid names instead of edit fallback ("Grid 1").
    showWorkspace("qc");
    renderBidsAutoInfo();
    renderBidsMuscleFields();

    setStatus("Preview ready", "success");
    return true;
  } catch (err) {
    if (silentFailure) console.error(err);
    else handleError(err, setStatus, "Preview failed");
    return false;
  } finally {
    setUploadLoading(false);
  }
}
