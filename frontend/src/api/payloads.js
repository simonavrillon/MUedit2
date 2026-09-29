/** @typedef {import("../app/context.js").JsonObject} JsonObject */
/** @typedef {import("../app/context.js").Span} Span */

/**
 * A decomposition file as loaded for editing; fields beyond these pass through.
 *
 * @typedef {JsonObject & {
 *   pulse_trains: number[][],
 *   pulse_trains_full: number[][],
 *   distime_all: number[][],
 *   grid_names: string[],
 *   mu_grid_index: number[],
 *   parameters: JsonObject,
 *   total_samples: number,
 *   fsamp: number | null,
 *   file_label: string,
 *   edit_signal_token: string,
 * }} EditLoadPayload
 */

/**
 * A run's preview; fields beyond these pass through. The overview and aux
 * traces are not read from it: they come from the upload as envelopes
 * (`/series/*`).
 *
 * @typedef {JsonObject & {
 *   grid_names: string[],
 *   rois: Span[],
 *   channel_means: number[][],
 *   coordinates: number[][][],
 *   metadata: JsonObject,
 *   muscle: string[],
 *   pulse_trains_full: number[][],
 *   pulse_trains_all: number[][],
 *   distime_all: number[][],
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
 * @param {(number[] | null | undefined)[]} distimes
 * @returns {number}
 */
export function totalSamplesFromDistimes(distimes) {
  // Not Math.max(...spikes): millions of arguments throw a RangeError.
  let maxSpike = 0;
  for (const mu of distimes) {
    for (const v of mu || []) {
      const n = Number(v);
      if (n > maxSpike) maxSpike = n;
    }
  }
  return maxSpike + 1;
}

/**
 * @param {unknown} payload
 * @returns {EditLoadPayload}
 */
export function normalizeEditLoadPayload(payload) {
  /** @type {JsonObject} */
  const source = payload && typeof payload === "object" ? payload : {};
  const distRaw = source.distime_all || source.distime || [];
  return {
    ...source,
    pulse_trains: Array.isArray(source.pulse_trains) ? source.pulse_trains : [],
    pulse_trains_full: Array.isArray(source.pulse_trains_full)
      ? source.pulse_trains_full
      : [],
    distime_all: Array.isArray(distRaw)
      ? distRaw.map((row) =>
          Array.isArray(row)
            ? row.map((v) => toFiniteNumber(v, NaN)).filter(Number.isFinite)
            : [],
        )
      : [],
    grid_names: Array.isArray(source.grid_names) ? source.grid_names : [],
    mu_grid_index: Array.isArray(source.mu_grid_index)
      ? source.mu_grid_index
      : [],
    parameters:
      source.parameters && typeof source.parameters === "object"
        ? source.parameters
        : {},
    total_samples: toFiniteNumber(source.total_samples, 0),
    fsamp: source.fsamp ?? null,
    file_label: String(source.file_label || ""),
    edit_signal_token: String(source.edit_signal_token || ""),
  };
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
    pulse_trains_full: Array.isArray(source.pulse_trains_full)
      ? source.pulse_trains_full
      : [],
    pulse_trains_all: Array.isArray(source.pulse_trains_all)
      ? source.pulse_trains_all
      : [],
    distime_all: Array.isArray(source.distime_all) ? source.distime_all : [],
    mu_grid_index: Array.isArray(source.mu_grid_index)
      ? source.mu_grid_index
      : [],
    total_samples: toFiniteNumber(source.total_samples, 0),
  };
}
