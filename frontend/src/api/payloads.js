/**
 * Normalises backend JSON at the boundary: the preview payload, regions
 * as `Span`s and `/qc/segment` segments. Malformed entries are dropped here.
 */
/** @typedef {import("../app/context.js").JsonObject} JsonObject */
/** @typedef {import("../app/context.js").Span} Span */

/**
 * A raw file's preview, as the page reads it; fields beyond these pass
 * through. The overview and aux traces are not in it: they come from the
 * upload as envelopes (`/series/*`).
 *
 * @typedef {JsonObject & {
 *   grid_names: string[],
 *   channel_means: number[][],
 *   coordinates: number[][][],
 *   metadata: JsonObject,
 *   muscle_names: string[],
 *   aux_names: string[],
 *   total_samples: number,
 * }} PreviewPayload
 */

/**
 * @param {unknown} value
 * @param {number} [fallback]
 * @returns {number}
 */
function toFiniteNumber(value, fallback = 0) {
  const n = Number(value);
  return Number.isFinite(n) ? n : fallback;
}

/**
 * Artifact windows arrive as `{start, end}` objects or as `[start, end]`
 * pairs; everything past this point uses `Span` objects only.
 * Regions with a non-finite bound are dropped.
 *
 * @param {unknown} regions
 * @returns {Span[]}
 */
export function toSpans(regions) {
  if (!Array.isArray(regions)) return [];
  return regions
    .map((r) => ({
      start: Number(Array.isArray(r) ? r[0] : r?.start),
      end: Number(Array.isArray(r) ? r[1] : r?.end),
    }))
    .filter((r) => Number.isFinite(r.start) && Number.isFinite(r.end));
}

export const SEGMENT_KINDS = ["ramp", "plateau", "sinusoid", "irregular"];

/**
 * The segments of a `/qc/segment` response; one with an unknown kind or bounds is dropped.
 *
 * @param {unknown} segments
 * @returns {import("../state/state.js").TargetSegment[]}
 */
export function toSegments(segments) {
  if (!Array.isArray(segments)) return [];
  return segments
    .map((s) => ({
      start: Number(s?.start),
      end: Number(s?.end),
      kind: s?.kind,
      direction:
        s?.direction === "up" || s?.direction === "down" ? s.direction : null,
      cycles: Array.isArray(s?.cycles)
        ? s.cycles.map(Number).filter(Number.isFinite)
        : [],
      frequencyHz: Number.isFinite(s?.frequency_hz) ? s.frequency_hz : null,
    }))
    .filter(
      (s) =>
        SEGMENT_KINDS.includes(s.kind) &&
        Number.isFinite(s.start) &&
        Number.isFinite(s.end) &&
        s.end > s.start,
    );
}

/**
 * @param {unknown} payload
 * @returns {PreviewPayload}
 */
export function normalizePreviewPayload(payload) {
  /** @type {JsonObject} */
  const source = payload && typeof payload === "object" ? payload : {};
  return {
    ...source,
    grid_names: Array.isArray(source.grid_names) ? source.grid_names : [],
    channel_means: Array.isArray(source.channel_means)
      ? source.channel_means
      : [],
    coordinates: Array.isArray(source.coordinates) ? source.coordinates : [],
    metadata:
      source.metadata && typeof source.metadata === "object"
        ? source.metadata
        : {},
    muscle_names: Array.isArray(source.muscle_names)
      ? source.muscle_names
      : Array.isArray(source.muscle)
        ? source.muscle
        : [],
    aux_names: Array.isArray(source.aux_names)
      ? source.aux_names
      : Array.isArray(source.auxiliary_names)
        ? source.auxiliary_names
        : [],
    total_samples: toFiniteNumber(source.total_samples, 0),
  };
}
