// Decomposition parameter mapping. The default values themselves are held to
// the Python side by tests/test_frontend_param_parity.py.
import { test, describe } from "node:test";
import assert from "node:assert/strict";

import {
  DEFAULT_POSTPROCESS_MODE,
  POSTPROCESS_MODES,
  buildDecomposeParams,
  postprocessFlags,
} from "../src/decomp/params.js";

const raw = {
  niter: 150,
  nwindows: 2,
  duplicatesthresh: 0.3,
  silVal: 0.9,
  covVal: 0.5,
  covOn: true,
  peelOn: false,
  peelWindow: 25,
  postprocessMode: "full-trace",
};

describe("postprocessFlags", () => {
  test("each mode maps to its backend flags", () => {
    assert.deepEqual(postprocessFlags("windowed"), {
      use_adaptive: 0,
      full_trace: 0,
    });
    assert.deepEqual(postprocessFlags("full-trace"), {
      use_adaptive: 0,
      full_trace: 1,
    });
    assert.deepEqual(postprocessFlags("adaptive"), {
      use_adaptive: 1,
      full_trace: 0,
    });
  });

  test("unknown or missing modes fall back to the default", () => {
    const fallback = POSTPROCESS_MODES[DEFAULT_POSTPROCESS_MODE].flags;
    assert.deepEqual(postprocessFlags("bogus"), fallback);
    assert.deepEqual(postprocessFlags(undefined), fallback);
  });

  test("no mode sets both adaptive and full-trace", () => {
    for (const [key, mode] of Object.entries(POSTPROCESS_MODES)) {
      assert.ok(
        !(mode.flags.use_adaptive && mode.flags.full_trace),
        `${key} sets both flags`,
      );
    }
  });
});

describe("buildDecomposeParams", () => {
  test("maps the form values onto the wire names", () => {
    assert.deepEqual(buildDecomposeParams(raw), {
      niter: 150,
      nwindows: 2,
      nbextchan: 1000,
      duplicatesthresh: 0.3,
      sil_thr: 0.9,
      cov_thr: 0.5,
      covfilter: 1,
      contrast_func: "skew",
      initialization: 0,
      peel_off_enabled: 0,
      peel_off_win: 0.025,
      auto_mask_artifacts: 0,
      use_adaptive: 0,
      full_trace: 1,
    });
  });

  test("toggles become 0/1 flags", () => {
    const out = buildDecomposeParams({ ...raw, covOn: false, peelOn: true });
    assert.equal(out.covfilter, 0);
    assert.equal(out.peel_off_enabled, 1);
  });

  test("an unknown mode sends the default flags", () => {
    const out = buildDecomposeParams({ ...raw, postprocessMode: "bogus" });
    assert.equal(out.use_adaptive, 0);
    assert.equal(out.full_trace, 0);
  });
});
