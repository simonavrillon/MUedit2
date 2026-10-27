// Decomposition parameter mapping and the post-processing modes.
import { test, describe } from "node:test";
import assert from "node:assert/strict";

import { buildDecomposeParams, postprocessMode } from "../src/decomp/params.js";

const raw = {
  niter: 150,
  nwindows: 2,
  duplicatesthresh: 0.3,
  silThr: 0.9,
  covThr: 0.5,
  covFilter: true,
  peelOffEnabled: false,
  peelOffWinMs: 25,
  postprocessMode: "full-trace",
};

describe("postprocessMode", () => {
  test("each mode maps to its backend flags", () => {
    assert.deepEqual(postprocessMode("windowed").flags, {
      use_adaptive: 0,
      full_trace: 0,
    });
    assert.deepEqual(postprocessMode("full-trace").flags, {
      use_adaptive: 0,
      full_trace: 1,
    });
    assert.deepEqual(postprocessMode("adaptive").flags, {
      use_adaptive: 1,
      full_trace: 0,
    });
  });

  test("unknown or missing modes fall back to windowed", () => {
    const fallback = postprocessMode("windowed");
    assert.equal(postprocessMode("bogus"), fallback);
    assert.equal(postprocessMode(undefined), fallback);
  });
});

describe("buildDecomposeParams", () => {
  test("maps the form values onto the wire names", () => {
    assert.deepEqual(buildDecomposeParams(raw), {
      niter: 150,
      nwindows: 2,
      duplicatesthresh: 0.3,
      sil_thr: 0.9,
      cov_thr: 0.5,
      covfilter: 1,
      peel_off_enabled: 0,
      peel_off_win: 0.025,
      use_adaptive: 0,
      full_trace: 1,
    });
  });

  test("toggles become 0/1 flags", () => {
    const out = buildDecomposeParams({
      ...raw,
      covFilter: false,
      peelOffEnabled: true,
    });
    assert.equal(out.covfilter, 0);
    assert.equal(out.peel_off_enabled, 1);
  });

  test("an unknown mode sends the default flags", () => {
    const out = buildDecomposeParams({ ...raw, postprocessMode: "bogus" });
    assert.equal(out.use_adaptive, 0);
    assert.equal(out.full_trace, 0);
  });
});
