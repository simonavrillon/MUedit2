/**
 * The /decompose run parameters: the post-processing modes and the mapping
 * from the run form's values to the wire names.
 */

/**
 * @typedef {object} PostprocessMode
 * @property {string} hint
 * @property {{ use_adaptive: number, full_trace: number }} flags
 */

/**
 * The run settings as read from the form, before mapping to the wire format.
 *
 * @typedef {object} DecomposeInputs
 * @property {number} niter
 * @property {number} nwindows
 * @property {number} duplicatesthresh
 * @property {number} silThr
 * @property {number} covThr
 * @property {boolean} covFilter
 * @property {boolean} peelOffEnabled
 * @property {number} peelOffWinMs  Milliseconds.
 * @property {string | undefined} postprocessMode
 */

/** @typedef {ReturnType<typeof buildDecomposeParams>} DecomposeParams The /decompose run parameters. */

/**
 * The three post-processing routes, in the order they appear in the selector.
 * Keys match `POSTPROCESS_MODES` in `python/src/muedit/decomp/types.py` and the
 * CLI's `--postprocess` choices.
 *
 * They are mutually exclusive on the backend: `postprocess_step` checks
 * `use_adaptive` first and only falls through to the `full_trace` branch when
 * it is off, so the two flags must never both be set. Keeping the choice as a
 * single mode here makes that impossible to express.
 *
 * @type {Record<string, PostprocessMode>}
 */
const POSTPROCESS_MODES = {
  windowed: {
    hint: "Filters applied inside each analysis window only. Pulse trains are zero outside the ROI.",
    flags: { use_adaptive: 0, full_trace: 0 },
  },
  "full-trace": {
    hint: "Filters applied batch by batch across the whole recording, without adaptation. Units extend beyond the ROI.",
    flags: { use_adaptive: 0, full_trace: 1 },
  },
  adaptive: {
    hint: "Filters and whitening are adapted batch by batch across the whole recording.",
    flags: { use_adaptive: 1, full_trace: 0 },
  },
};

const DEFAULT_POSTPROCESS_MODE = "windowed";

/**
 * Resolve a mode key, falling back to the default.
 *
 * @param {string | undefined} mode
 */
export function postprocessMode(mode) {
  return (
    POSTPROCESS_MODES[mode ?? ""] || POSTPROCESS_MODES[DEFAULT_POSTPROCESS_MODE]
  );
}

/**
 * @param {DecomposeInputs} raw
 */
export function buildDecomposeParams(raw) {
  return {
    niter: raw.niter,
    nwindows: raw.nwindows,
    duplicatesthresh: raw.duplicatesthresh,
    sil_thr: raw.silThr,
    cov_thr: raw.covThr,
    covfilter: raw.covFilter ? 1 : 0,
    peel_off_enabled: raw.peelOffEnabled ? 1 : 0,
    peel_off_win: raw.peelOffWinMs / 1000,
    ...postprocessMode(raw.postprocessMode).flags,
  };
}
