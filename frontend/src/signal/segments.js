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
/** @typedef {import("../state/state.js").SegmentKind} SegmentKind */

const SEGMENT_TYPES = SEGMENT_KINDS.flatMap((kind) =>
  kind === "ramp" ? ["up ramp", "down ramp"] : [kind],
);

/**
 * @param {TargetSegment} seg
 */
export function segmentGlyph(seg) {
  if (seg.kind === "ramp") return seg.direction === "down" ? "↘" : "↗";
  return { plateau: "—", sinusoid: "∿", irregular: "≈" }[seg.kind];
}

/**
 * The segment's kind, with ramps split by direction, e.g. "up ramp".
 *
 * @param {TargetSegment} seg
 */
export function segmentType(seg) {
  return seg.kind === "ramp" ? `${seg.direction} ramp` : seg.kind;
}

/**
 * @param {TargetSegment} seg
 */
function segmentLabel(seg) {
  return `${segmentGlyph(seg)} ${segmentType(seg)}`;
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
 * Choose `segs`, or drop them: chosen segments that touch merge into one
 * window, and dropping one from inside a window splits it.
 *
 * @param {State} state
 * @param {TargetSegment[]} segs
 * @param {boolean} on
 * @returns {number} How many windows are drawn now.
 */
function chooseSegments(state, segs, on) {
  const drawn = drawnRois(state);
  const minLength = MIN_ROI_S * (state.fsamp || 0);
  const next = on
    ? unionSpans([...drawn, ...segs])
    : segs.reduce((spans, seg) => subtractSpan(spans, seg, minLength), drawn);
  setRois(
    state,
    next.length ? next : [{ start: 0, end: state.seriesLength || 0 }],
  );
  return next.length;
}

/**
 * Choose segment `idx`, or drop it when chosen.
 *
 * @param {State} state
 * @param {number} idx
 * @returns {number} How many windows are drawn now.
 */
export function toggleSegment(state, idx) {
  const seg = state.segments[idx];
  return chooseSegments(state, [seg], !isSegmentChosen(state, seg));
}

/**
 * Sync the window count and the traces after choosing segments.
 *
 * @param {App} app
 * @param {number} count How many windows are drawn now.
 * @returns {string} The count for the status, e.g. "2 windows".
 */
export function syncChosenWindows(app, count) {
  if (app.els.nwindows) app.els.nwindows.value = String(Math.max(1, count));
  app.ensureQcTraces();
  return `${count} window${count === 1 ? "" : "s"}`;
}

/**
 * Choose every segment of `type`, or drop them all.
 *
 * @param {State} state
 * @param {string} type
 * @param {boolean} on
 * @returns {number} How many windows are drawn now.
 */
export function chooseSegmentType(state, type, on) {
  const segs = state.segments.filter((s) => segmentType(s) === type);
  return chooseSegments(state, segs, on);
}

/**
 * The segment types present, in a fixed order, with how many of each the windows cover.
 *
 * @param {State} state
 * @returns {{ type: string, kind: SegmentKind, glyph: string, count: number, chosen: number }[]}
 */
export function segmentTypes(state) {
  return SEGMENT_TYPES.flatMap((type) => {
    const segs = state.segments.filter((s) => segmentType(s) === type);
    if (!segs.length) return [];
    return [
      {
        type,
        kind: segs[0].kind,
        glyph: segmentGlyph(segs[0]),
        count: segs.length,
        chosen: segs.filter((s) => isSegmentChosen(state, s)).length,
      },
    ];
  });
}

/**
 * The BIDS task the segments' order describes, e.g. "trapezoid"; irregular
 * blocks are skipped unless they are all there is.
 *
 * @param {TargetSegment[]} segments
 */
export function inferTask(segments) {
  if (segments.some((s) => s.kind === "sinusoid")) return "sinusoid";
  // One letter per segment: Up ramp, Down ramp, Plateau.
  const order = segments
    .map(
      (s) =>
        ({ "up ramp": "U", "down ramp": "D", plateau: "P" })[segmentType(s)],
    )
    .join("");
  const steps = order.match(/UP/g)?.length ?? 0;
  if (/^[UP]*$/.test(order) && steps >= 2) return "stairs";
  if (/^(UD)+$/.test(order)) return "triangle";
  if (/^(UPD)+$/.test(order)) return "trapezoid";
  if (/^[UP]*U[UP]*$/.test(order)) return "ramp up";
  if (/^[DP]*D[DP]*$/.test(order)) return "ramp down";
  return "dynamic";
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
        ...(channel === null ? {} : { target_index: channel }),
      }),
    );
    const segments = toSegments(data?.segments);
    setSegments(state, segments);
    // A task typed in, or read from the file, stays.
    const task =
      els.bidsTask && !els.bidsTask.dataset.keep && segments.length
        ? inferTask(segments)
        : null;
    if (task) els.bidsTask.value = task;
    setTargetIndex(state, data?.target_index);
    if (els.auxSelector && state.targetIndex !== null) {
      els.auxSelector.value = String(state.targetIndex);
    }
    app.refreshVisuals();
    setStatus(
      `${data?.target_name ?? "Target"}: ${summarize(segments)}.${task ? ` Task set to ${task}.` : ""} Click the windows to decompose`,
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
