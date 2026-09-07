/**
 * Decomposition parameter shape. Extracted from the container so the
 * domain owns the wire format — the container only reads DOM inputs and
 * passes raw values through.
 */

/**
 * The three post-processing routes, in the order they appear in the selector.
 *
 * They are mutually exclusive on the backend: `postprocess_step` checks
 * `use_adaptive` first and only falls through to the `full_trace` branch when
 * it is off, so the two flags must never both be set. Keeping the choice as a
 * single mode here makes that impossible to express.
 */
export const POSTPROCESS_MODES = {
  windowed: {
    label: "Windowed",
    hint: "Filters applied inside each analysis window only. Pulse trains are zero outside the ROI.",
    flags: { use_adaptive: 0, full_trace: 0 },
  },
  full_trace: {
    label: "Full trace",
    hint: "Filters dewhitened and applied across the whole recording, so units extend beyond the ROI.",
    flags: { use_adaptive: 0, full_trace: 1 },
  },
  adaptive: {
    label: "Adaptive",
    hint: "Separation vectors and whitening track the signal batch by batch across the whole recording.",
    flags: { use_adaptive: 1, full_trace: 0 },
  },
};

export const DEFAULT_POSTPROCESS_MODE = "windowed";

/** Resolve a mode key to its backend flags, falling back to the default. */
export function postprocessFlags(mode) {
  return (POSTPROCESS_MODES[mode] || POSTPROCESS_MODES[DEFAULT_POSTPROCESS_MODE])
    .flags;
}

export function buildDecomposeParams(raw) {
  return {
    niter: raw.niter,
    nwindows: raw.nwindows,
    nbextchan: 1000,
    duplicatesthresh: raw.duplicatesthresh,
    sil_thr: raw.silVal,
    cov_thr: raw.covVal,
    covfilter: raw.covOn ? 1 : 0,
    contrast_func: "skew",
    initialization: 0,
    peel_off_enabled: raw.peelOn ? 1 : 0,
    peel_off_win: raw.peelWindow / 1000,
    ...postprocessFlags(raw.postprocessMode),
  };
}
