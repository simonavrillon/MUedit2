// The MUedit server serves the page and the API on one origin.
export const API_BASE = `${window.location.origin}/api/v1`;

/** @type {Map<string, string>} */
const tokenCache = new Map();

/**
 * A colour custom property of `:root`, as css/tokens.css defines it.
 *
 * @param {string} name
 */
function cssToken(name) {
  let value = tokenCache.get(name);
  if (value === undefined) {
    value = getComputedStyle(document.documentElement)
      .getPropertyValue(name)
      .trim();
    tokenCache.set(name, value);
  }
  return value;
}

const COLOR_TOKENS = {
  primary: "--text",
  warning: "--accent-2",
  muted: "--muted",
  muPurple: "--mu-4",
  artifactMarker: "--artifact-clr",
  pulse: "--plot-trace-2",
  gridEmpty: "--plot-empty",
  roiFill: "--plot-roi-fill",
  roiStroke: "--plot-roi-stroke",
  artifactFill: "--plot-artifact-fill",
  artifactStroke: "--plot-artifact-stroke",
  selectionFill: "--plot-selection-fill",
  selectionStroke: "--plot-selection-stroke",
  gridLineDim: "--plot-gridline",
  gridAxis: "--plot-axis",
  markerOutline: "--plot-marker-outline",
  bookmark: "--plot-bookmark",
  timelineTrack: "--plot-timeline-track",
  timelineAdded: "--plot-timeline-added",
  timelineRemoved: "--plot-timeline-removed",
  timelineSpikes: "--plot-timeline-spikes",
  timelineViewFill: "--plot-timeline-view-fill",
  timelineViewStroke: "--plot-timeline-view-stroke",
};

/**
 * Canvas colours, read from css/tokens.css on first use.
 *
 * @type {{ readonly [K in keyof typeof COLOR_TOKENS]: string }}
 */
export const COLORS = /** @type {any} */ ({});
for (const [key, name] of Object.entries(COLOR_TOKENS)) {
  Object.defineProperty(COLORS, key, {
    get: () => cssToken(name),
    enumerable: true,
  });
}

const TRACE_COLOR_COUNT = 7;

/** One colour per grid or auxiliary trace, cycled past the last. */
export function traceColors() {
  return Array.from({ length: TRACE_COLOR_COUNT }, (_, i) =>
    cssToken(`--plot-trace-${i + 1}`),
  );
}

/** Bins of the whole-recording envelopes (grid overview, aux): about a wide canvas in pixels. */
export const OVERVIEW_BINS = 2048;
/** Bins of each channel's mini trace in the QC grid, a few dozen pixels wide. */
export const QC_TRACE_BINS = 128;
