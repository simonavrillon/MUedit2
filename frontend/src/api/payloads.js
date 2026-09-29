/** @typedef {import("../app/context.js").JsonObject} JsonObject */
/** @typedef {import("../app/context.js").Span} Span */

/**
 * A run's preview; fields beyond these pass through. The overview and aux
 * traces are not read from it: they come from the upload as envelopes
 * (`/series/*`), and the pulse trains from the run (`/series/pulse`).
 *
 * @typedef {JsonObject & {
 *   grid_names: string[],
 *   rois: Span[],
 *   channel_means: number[][],
 *   coordinates: number[][][],
 *   metadata: JsonObject,
 *   muscle: string[],
 *   distime_all: Int32Array[],
 *   mu_grid_index: number[],
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
 * One MU's discharge times as an `Int32Array` (a JSON list is converted; a
 * typed array is kept as it is).
 *
 * @param {unknown} row
 * @returns {Int32Array}
 */
export function toSpikeArray(row) {
  if (row instanceof Int32Array) return row;
  if (!Array.isArray(row)) return new Int32Array(0);
  return Int32Array.from(
    row.map((v) => Number(v)).filter((v) => Number.isFinite(v) && v >= 0),
  );
}

/**
 * Regions (ROIs, artifact windows) arrive as `{start, end}` objects or as
 * `[start, end]` pairs; everything past this point uses `Span` objects only.
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

/**
 * Samples needed to hold every discharge: the last spike + 1, or 1 with none.
 *
 * @param {(ArrayLike<number> | null | undefined)[]} distimes
 * @returns {number}
 */
export function totalSamplesFromDistimes(distimes) {
  // Not Math.max(...spikes): millions of arguments throw a RangeError.
  let maxSpike = 0;
  for (const mu of distimes) {
    for (let i = 0; i < (mu?.length ?? 0); i++) {
      const n = Number(mu?.[i]);
      if (n > maxSpike) maxSpike = n;
    }
  }
  return maxSpike + 1;
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
    rois: toSpans(source.rois),
    channel_means: Array.isArray(source.channel_means)
      ? source.channel_means
      : [],
    coordinates: Array.isArray(source.coordinates) ? source.coordinates : [],
    metadata:
      source.metadata && typeof source.metadata === "object"
        ? source.metadata
        : {},
    muscle: Array.isArray(source.muscle) ? source.muscle : [],
    distime_all: Array.isArray(source.distime_all)
      ? source.distime_all.map(toSpikeArray)
      : [],
    mu_grid_index: Array.isArray(source.mu_grid_index)
      ? source.mu_grid_index
      : [],
    total_samples: toFiniteNumber(source.total_samples, 0),
  };
}
