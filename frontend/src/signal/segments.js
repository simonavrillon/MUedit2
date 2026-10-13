import { isCovered, subtractSpan, unionSpans } from "./intervals.js";
import { setRois, setSegments, setTargetIndex } from "../state/actions.js";
import { SEGMENT_KINDS, toSegments } from "../api/payloads.js";
import { handleError } from "../app/services/error-service.js";
import { withUpload } from "../app/services/upload.js";
import { MIN_ROI_S } from "../config.js";

/** @typedef {import("../app/context.js").App} App */
/** @typedef {import("../app/context.js").Span} Span */
/** @typedef {import("../state/state.js").State} State */
/** @typedef {import("../state/state.js").TargetSegment} TargetSegment */

/**
 * @param {TargetSegment} seg
 */
export function segmentGlyph(seg) {
  if (seg.kind === "ramp") return seg.direction === "down" ? "↘" : "↗";
  return { plateau: "—", sinusoid: "∿", irregular: "≈" }[seg.kind];
}

/**
 * @param {TargetSegment} seg
 */
function segmentLabel(seg) {
  const name = seg.kind === "ramp" ? `${seg.direction} ramp` : seg.kind;
  return `${segmentGlyph(seg)} ${name}`;
}

/**
 * Whether `roi` is a whole-recording placeholder, a window not drawn yet.
 *
 * @param {Span} roi
 * @param {number} total
 */
export function isPlaceholder(roi, total) {
  return roi.start <= 0 && roi.end >= total;
}

/**
 * The windows the user drew or chose.
 *
 * @param {State} state
 * @returns {Span[]}
 */
export function drawnRois(state) {
  return state.rois.filter((r) => !isPlaceholder(r, state.seriesLength || 0));
}

/**
 * @param {State} state
 * @param {Span} seg
 */
export function isSegmentChosen(state, seg) {
  return isCovered(drawnRois(state), seg);
}

/**
 * Index of the segment under `sample`, or -1.
 *
 * @param {TargetSegment[]} segments
 * @param {number} sample
 */
export function segmentAt(segments, sample) {
  return segments.findIndex((s) => s.start <= sample && sample < s.end);
}

/**
 * Choose segment `idx`, or drop it when chosen: chosen segments that touch
 * merge into one window, and dropping one from inside a window splits it.
 *
 * @param {State} state
 * @param {number} idx
 * @returns {number} How many windows are drawn now.
 */
export function toggleSegment(state, idx) {
  const seg = state.segments[idx];
  const drawn = drawnRois(state);
  const minLength = MIN_ROI_S * (state.fsamp || 0);
  const next = isCovered(drawn, seg)
    ? subtractSpan(drawn, seg, minLength)
    : unionSpans([...drawn, seg]);
  setRois(
    state,
    next.length ? next : [{ start: 0, end: state.seriesLength || 0 }],
  );
  return next.length;
}

/**
 * What a window holds, e.g. "↗ up ramp + — plateau (14.2 s)".
 *
 * @param {State} state
 * @param {Span} roi
 */
export function describeWindow(state, roi) {
  const parts = state.segments
    .filter((s) => s.start >= roi.start && s.end <= roi.end)
    .map(segmentLabel);
  const seconds = state.fsamp
    ? ` (${((roi.end - roi.start) / state.fsamp).toFixed(1)} s)`
    : "";
  return `${parts.join(" + ") || "window"}${seconds}`;
}

/**
 * The auxiliary channel to segment: the one shown alone, else the detected target.
 *
 * @param {App} app
 * @returns {number | null}
 */
function segmentationChannel(app) {
  const shown = parseInt(app.els.auxSelector?.value ?? "-1", 10);
  if (shown >= 0) return shown;
  return app.state.targetIndex;
}

/**
 * @param {TargetSegment[]} segments
 */
function summarize(segments) {
  /** @type {Record<string, number>} */
  const counts = {};
  for (const s of segments) counts[s.kind] = (counts[s.kind] ?? 0) + 1;
  const parts = SEGMENT_KINDS.filter((k) => counts[k]).map(
    (k) => `${counts[k]} ${k}${counts[k] > 1 ? "s" : ""}`,
  );
  const sine = segments.find((s) => s.kind === "sinusoid" && s.frequencyHz);
  return (
    parts.join(", ") + (sine ? ` (${sine.frequencyHz?.toFixed(2)} Hz)` : "")
  );
}

/**
 * Split the target into segments the user then clicks to decompose; the
 * windows already drawn stay.
 *
 * @param {App} app
 */
export async function requestSegmentation(app) {
  const { state, els, api, setStatus } = app;
  if (!state.uploadToken) {
    setStatus("Load a signal first", "error");
    return false;
  }
  const channel = segmentationChannel(app);
  if (els.qcSegmentBtn) els.qcSegmentBtn.disabled = true;
  setStatus("Segmenting the target...", "muted");
  try {
    const data = await withUpload(app, (token) =>
      api.segmentTarget({
        upload_token: token,
        ...(channel === null ? {} : { aux_index: channel }),
      }),
    );
    const segments = toSegments(data?.segments);
    setSegments(state, segments);
    setTargetIndex(state, data?.target_index);
    if (els.auxSelector && state.targetIndex !== null) {
      els.auxSelector.value = String(state.targetIndex);
    }
    app.refreshVisuals();
    setStatus(
      `${data?.target_name ?? "Target"}: ${summarize(segments)}. Click the windows to decompose`,
      "success",
    );
    return true;
  } catch (err) {
    handleError(err, setStatus, "Target segmentation failed");
    return false;
  } finally {
    if (els.qcSegmentBtn) els.qcSegmentBtn.disabled = !state.auxNames.length;
  }
}
