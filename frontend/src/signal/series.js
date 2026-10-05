/**
 * Pure helpers over viewport series as the API sends them: a row is the
 * samples of a window, or their min/max per bin (an envelope).
 */

/** @typedef {import("../state/state.js").ChannelTrace} ChannelTrace */

/**
 * A window of a series as it was fetched: `row` holds the samples of
 * `[start, end)`, or their min/max per bin; null draws markers only.
 *
 * @typedef {object} TraceWindow
 * @property {ChannelTrace | null} row
 * @property {number} start
 * @property {number} end
 */

/**
 * @param {unknown} x
 * @returns {x is ArrayLike<number>}
 */
export function isValues(x) {
  return (
    Array.isArray(x) || (ArrayBuffer.isView(x) && !(x instanceof DataView))
  );
}

/**
 * @param {unknown} row
 * @returns {row is import("../api/binary-payloads.js").Envelope}
 */
export function isEnvelope(row) {
  if (!row || typeof row !== "object" || isValues(row)) return false;
  const env = /** @type {{ min?: unknown, max?: unknown }} */ (row);
  return isValues(env.min) && isValues(env.max);
}

/**
 * Points in a viewport row: its bins, or its samples (0 when it is neither).
 *
 * @param {ChannelTrace | null | undefined} row
 */
export function seriesPoints(row) {
  if (isEnvelope(row)) return row.min.length;
  return isValues(row) ? row.length : 0;
}

/**
 * Smallest and largest finite value over viewport rows.
 *
 * @param {(ChannelTrace | null | undefined)[]} rows
 */
export function seriesRange(rows) {
  let min = Infinity;
  let max = -Infinity;
  for (const row of rows) {
    /** @type {ArrayLike<number>} */
    let lows;
    /** @type {ArrayLike<number>} */
    let highs;
    if (isEnvelope(row)) {
      lows = row.min;
      highs = row.max;
    } else if (isValues(row)) {
      lows = highs = row;
    } else {
      continue;
    }
    for (let i = 0; i < lows.length; i++) {
      if (lows[i] < min) min = lows[i];
    }
    for (let i = 0; i < highs.length; i++) {
      if (highs[i] > max) max = highs[i];
    }
  }
  return { min, max };
}

/**
 * Smallest and largest value of a trace window, `{0, 0}` when it has none.
 *
 * @param {TraceWindow | null | undefined} trace
 */
export function traceRange(trace) {
  const { min, max } = seriesRange(trace?.row ? [trace.row] : []);
  return Number.isFinite(min) && Number.isFinite(max)
    ? { min, max }
    : { min: 0, max: 0 };
}
