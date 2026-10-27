/**
 * Normalises backend JSON at the boundary: the preview payload, regions
 * as `Span`s, `/qc/segment` segments and a run's `done` summary. Malformed
 * entries are dropped here.
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
 * Artifact windows arrive as `[start, end]` pairs; everything past this
 * point uses `Span` objects only. Regions with a non-finite bound are dropped.
 *
 * @param {unknown} regions
 * @returns {Span[]}
 */
export function toSpans(regions) {
  if (!Array.isArray(regions)) return [];
  return regions
    .filter(Array.isArray)
    .map(([start, end]) => ({ start: Number(start), end: Number(end) }))
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
    muscle_names: Array.isArray(source.muscle_names) ? source.muscle_names : [],
    aux_names: Array.isArray(source.aux_names) ? source.aux_names : [],
    total_samples: Number.isFinite(Number(source.total_samples))
      ? Number(source.total_samples)
      : 0,
  };
}

/**
 * The finished run from its `done` event, with its units counted per grid.
 *
 * @param {JsonObject} summary The event's summary.
 * @param {JsonObject | null | undefined} preview Its preview metadata.
 * @returns {import("../decomp/live.js").RunSummary}
 */
export function toRunSummary(summary, preview) {
  const nMu = Math.max(0, Number(summary.n_mu) || 0);
  const muGridIndex = (
    Array.isArray(preview?.mu_grid_index) ? preview.mu_grid_index : []
  ).map((g) => Math.max(0, Number(g) || 0));
  const ngrid = Math.max(
    Array.isArray(summary.grid_names) ? summary.grid_names.length : 0,
    muGridIndex.length ? Math.max(...muGridIndex) + 1 : 0,
    nMu > 0 ? 1 : 0,
  );
  const nMuByGrid = new Array(ngrid).fill(0);
  for (let mu = 0; mu < nMu; mu++) {
    const g = muGridIndex[mu] ?? 0;
    nMuByGrid[g < ngrid ? g : 0] += 1;
  }
  return { nMu, nMuByGrid, muGridIndex };
}
