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
 * @param {ChannelTrace | null | undefined} row
 * @returns {row is Float32Array}
 */
function isValues(row) {
  return row instanceof Float32Array;
}

/**
 * @param {ChannelTrace | null | undefined} row
 * @returns {row is import("../api/binary-payloads.js").Envelope}
 */
export function isEnvelope(row) {
  return !!row && !isValues(row);
}

/**
 * Points in a viewport row: its bins, or its samples (0 without a row).
 *
 * @param {ChannelTrace | null | undefined} row
 */
export function seriesPoints(row) {
  if (!row) return 0;
  return isEnvelope(row) ? row.min.length : row.length;
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
