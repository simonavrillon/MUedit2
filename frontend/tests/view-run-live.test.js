// The run page's text: the elapsed clock and the time-left estimate.
import { test, describe } from "node:test";
import assert from "node:assert/strict";

import "./_app.js";

const { formatClock, formatRemaining } =
  await import("../src/view/run-live.js");

describe("formatClock and formatRemaining", () => {
  test("formats clocks and estimates", () => {
    assert.equal(formatClock(65), "1:05");
    assert.equal(formatClock(3723), "1:02:03");
    assert.equal(formatRemaining(30), "under a minute");
    assert.equal(formatRemaining(150), "about 3 min");
  });
});
