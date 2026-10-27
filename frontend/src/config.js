// The page's environment: the API origin, and the CSS tokens canvases draw with.

// The MUedit server serves the page and the API on one origin.
export const API_BASE = `${window.location.origin}/api/v1`;

/** @type {Map<string, string>} */
const tokenCache = new Map();

/**
 * A custom property of `:root`, as css/tokens.css defines it.
 *
 * @param {string} name
 */
function cssToken(name) {
  let value = tokenCache.get(name);
  if (value === undefined) {
    value = getComputedStyle(document.documentElement)
      .getPropertyValue(name)
      .trim();
    // Empty until tokens.css applies: a draw that early must not fix it for good.
    if (value) tokenCache.set(name, value);
  }
  return value;
}

const COLOR_TOKENS = {
  primary: "--text",
  warning: "--accent-2",
  muted: "--muted",
  discharge: "--mu-4",
  artifactMarker: "--artifact",
  pulseTrain: "--plot-trace-2",
  empty: "--plot-empty",
  roiFill: "--plot-roi-fill",
  roiStroke: "--plot-roi-stroke",
  artifactWindowFill: "--plot-artifact-fill",
  artifactWindowStroke: "--plot-artifact-stroke",
  roiStrongFill: "--plot-roi-chosen-fill",
  rampFill: "--plot-ramp-fill",
  rampStroke: "--plot-ramp-stroke",
  plateauFill: "--plot-plateau-fill",
  plateauStroke: "--plot-plateau-stroke",
  sinusoidFill: "--plot-sinusoid-fill",
  sinusoidStroke: "--plot-sinusoid-stroke",
  irregularFill: "--plot-irregular-fill",
  irregularStroke: "--plot-irregular-stroke",
  selectionFill: "--plot-selection-fill",
  selectionStroke: "--plot-selection-stroke",
  gridline: "--plot-gridline",
  axis: "--plot-axis",
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
  Object.defineProperty(COLORS, key, { get: () => cssToken(name) });
}

/** @type {string | undefined} */
let fontCache;

/** Canvas text: the page's font at its small size. */
export function canvasFont() {
  if (fontCache) return fontCache;
  // Canvas text is drawn in pixels, so the rem token is resolved against the root size.
  const rootPx = parseFloat(
    getComputedStyle(document.documentElement).fontSize,
  );
  const size = cssToken("--fs-xs");
  const family = cssToken("--font");
  const font = `${parseFloat(size) * (rootPx || 16)}px ${family}`;
  if (size && family) fontCache = font;
  return font;
}

// One per --plot-trace-N in css/tokens.css.
const TRACE_COLOR_COUNT = 7;

/** One colour per grid or auxiliary trace, cycled past the last. */
export function traceColors() {
  return Array.from({ length: TRACE_COLOR_COUNT }, (_, i) =>
    cssToken(`--plot-trace-${i + 1}`),
  );
}
