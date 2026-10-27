/**
 * Set operations on sample spans: union, subtraction and coverage.
 */
/** @typedef {import("../app/context.js").Span} Span */

/**
 * The spans sorted, with those that overlap or touch joined into one.
 *
 * @param {Span[]} spans
 * @returns {Span[]}
 */
export function unionSpans(spans) {
  const sorted = spans
    .filter((s) => s.end > s.start)
    .map((s) => ({ start: s.start, end: s.end }))
    .sort((a, b) => a.start - b.start);
  /** @type {Span[]} */
  const out = [];
  for (const span of sorted) {
    const last = out.at(-1);
    if (last && span.start <= last.end) last.end = Math.max(last.end, span.end);
    else out.push(span);
  }
  return out;
}

/**
 * The spans with `cut` removed; a piece left shorter than `minLength` is dropped.
 *
 * @param {Span[]} spans
 * @param {Span} cut
 * @param {number} [minLength]
 * @returns {Span[]}
 */
export function subtractSpan(spans, cut, minLength = 1) {
  /** @type {Span[]} */
  const out = [];
  for (const span of unionSpans(spans)) {
    const pieces =
      cut.end <= span.start || cut.start >= span.end
        ? [span]
        : [
            { start: span.start, end: Math.min(span.end, cut.start) },
            { start: Math.max(span.start, cut.end), end: span.end },
          ];
    out.push(...pieces.filter((p) => p.end - p.start >= minLength));
  }
  return out;
}

/**
 * Whether the spans cover every sample of `span`.
 *
 * @param {Span[]} spans
 * @param {Span} span
 */
export function isCovered(spans, span) {
  return unionSpans(spans).some(
    (s) => s.start <= span.start && s.end >= span.end,
  );
}
