// Target segments: choosing them merges touching ones into analysis windows,
// by segment or by type, and the request that loads them names the task.
import { test, describe, beforeEach } from "node:test";
import assert from "node:assert/strict";

import { fakeElement, recorder, resetDom } from "./_dom.js";
import { seg, segmentedApp } from "./_segments.js";

const { setupSessionFormEvents } =
  await import("../src/app/services/file-session.js");
const {
  chooseSegmentType,
  describeWindow,
  isSegmentChosen,
  requestSegmentation,
  segmentAt,
  segmentTypes,
  toggleSegment,
} = await import("../src/signal/segments.js");

console.error = () => {};

let app;
let els;

beforeEach(() => {
  resetDom();
  ({ app, els } = segmentedApp());
});

const chosen = () =>
  app.state.segments.map((s) => isSegmentChosen(app.state, s));

describe("toggleSegment", () => {
  test("touching segments merge into one window", () => {
    toggleSegment(app.state, 0);
    toggleSegment(app.state, 1);
    assert.equal(toggleSegment(app.state, 2), 1);
    assert.deepEqual(app.state.rois, [{ start: 100, end: 500 }]);
    assert.deepEqual(chosen(), [true, true, true, false]);
  });

  test("the order of the clicks does not matter", () => {
    toggleSegment(app.state, 2);
    toggleSegment(app.state, 0);
    assert.equal(app.state.rois.length, 2);
    toggleSegment(app.state, 1);
    assert.deepEqual(app.state.rois, [{ start: 100, end: 500 }]);
  });

  test("dropping the middle segment splits the window", () => {
    [0, 1, 2].forEach((i) => toggleSegment(app.state, i));
    assert.equal(toggleSegment(app.state, 1), 2);
    assert.deepEqual(app.state.rois, [
      { start: 100, end: 200 },
      { start: 400, end: 500 },
    ]);
    assert.deepEqual(chosen(), [true, false, true, false]);
  });

  test("segments apart across rest stay separate windows", () => {
    toggleSegment(app.state, 2);
    assert.equal(toggleSegment(app.state, 3), 2);
    assert.deepEqual(app.state.rois, [
      { start: 400, end: 500 },
      { start: 600, end: 900 },
    ]);
  });

  test("a segment touching a drawn window merges into it", () => {
    app.state.rois = [{ start: 50, end: 100 }];
    toggleSegment(app.state, 0);
    assert.deepEqual(app.state.rois, [{ start: 50, end: 200 }]);
  });

  test("dropping the last segment brings back the whole recording", () => {
    toggleSegment(app.state, 3);
    assert.equal(toggleSegment(app.state, 3), 0);
    assert.deepEqual(app.state.rois, [{ start: 0, end: 1000 }]);
    assert.deepEqual(chosen(), [false, false, false, false]);
  });
});

describe("segment types", () => {
  const ramps = () => [
    seg(100, 200, "ramp", { direction: "up" }),
    seg(200, 300, "ramp", { direction: "down" }),
    seg(400, 500, "ramp", { direction: "up" }),
    seg(500, 550, "plateau"),
    seg(550, 650, "ramp", { direction: "down" }),
  ];

  test("segmentTypes keeps up and down ramps apart, in a fixed order", () => {
    app.state.segments = ramps();
    toggleSegment(app.state, 0);
    assert.deepEqual(segmentTypes(app.state), [
      { type: "up ramp", kind: "ramp", glyph: "↗", count: 2, chosen: 1 },
      { type: "down ramp", kind: "ramp", glyph: "↘", count: 2, chosen: 0 },
      { type: "plateau", kind: "plateau", glyph: "—", count: 1, chosen: 0 },
    ]);
  });

  test("choosing up then down ramps merges the touching ones", () => {
    app.state.segments = ramps();
    assert.equal(chooseSegmentType(app.state, "up ramp", true), 2);
    assert.equal(chooseSegmentType(app.state, "down ramp", true), 3);
    assert.deepEqual(app.state.rois, [
      { start: 100, end: 300 },
      { start: 400, end: 500 },
      { start: 550, end: 650 },
    ]);
  });

  test("dropping a type keeps the other type's segments", () => {
    app.state.segments = ramps();
    chooseSegmentType(app.state, "up ramp", true);
    chooseSegmentType(app.state, "down ramp", true);
    assert.equal(chooseSegmentType(app.state, "up ramp", false), 2);
    assert.deepEqual(app.state.rois, [
      { start: 200, end: 300 },
      { start: 550, end: 650 },
    ]);
  });

  test("a type after a clicked segment of it holds each segment once", () => {
    app.state.segments = ramps();
    toggleSegment(app.state, 2);
    chooseSegmentType(app.state, "up ramp", true);
    assert.deepEqual(app.state.rois, [
      { start: 100, end: 200 },
      { start: 400, end: 500 },
    ]);
    assert.equal(chooseSegmentType(app.state, "up ramp", false), 0);
    assert.deepEqual(app.state.rois, [{ start: 0, end: 1000 }]);
  });
});

describe("the task segmentation infers", () => {
  const up = { kind: "ramp", direction: "up" };
  const down = { kind: "ramp", direction: "down" };
  const flat = { kind: "plateau" };
  const sine = { kind: "sinusoid" };
  const rough = { kind: "irregular" };

  for (const [segments, task] of [
    [[up, flat, sine], "sinusoid"],
    [[up, flat, up, flat, up], "stairs"],
    [[up, down], "triangle"],
    [[up, down, up, down], "triangle"],
    [[up, flat, down], "trapezoid"],
    [[up, flat, down, rough, up, flat, down], "trapezoid"],
    [[up], "ramp up"],
    [[up, flat], "ramp up"],
    [[flat, down], "ramp down"],
    [[up, down, up, flat, down], "dynamic"],
    [[rough], "dynamic"],
  ]) {
    test(`${segments.map((s) => s.direction ?? s.kind).join(" ")} is ${task}`, async () => {
      els.bidsTask = fakeElement("input");
      app.api.segmentTarget = async () => ({
        segments: segments.map((s, i) => ({ start: i, end: i + 1, ...s })),
      });
      await requestSegmentation(app);
      assert.equal(els.bidsTask.value, task);
    });
  }
});

describe("segment helpers", () => {
  test("segmentAt finds the segment under a sample, or -1 over rest", () => {
    assert.equal(segmentAt(app.state.segments, 250), 1);
    assert.equal(segmentAt(app.state.segments, 200), 1);
    assert.equal(segmentAt(app.state.segments, 550), -1);
  });

  test("describeWindow names the segments it holds and its length", () => {
    assert.equal(
      describeWindow(app.state, { start: 100, end: 500 }),
      "↗ up ramp + — plateau + ↘ down ramp (40.0 s)",
    );
    assert.equal(
      describeWindow(app.state, { start: 600, end: 900 }),
      "∿ sinusoid (30.0 s)",
    );
  });
});

describe("requestSegmentation", () => {
  test("loads the segments and shows the target channel", async () => {
    app.state.segments = [];
    const sent = recorder();
    app.api.segmentTarget = async (payload) => {
      sent(payload);
      return {
        target_index: 1,
        target_name: "Target",
        segments: [
          { start: 100, end: 200, kind: "ramp", direction: "up" },
          { start: 200, end: 400, kind: "plateau" },
          { start: 600, end: 900, kind: "sinusoid", frequency_hz: 0.5 },
        ],
      };
    };
    assert.equal(await requestSegmentation(app), true);
    assert.deepEqual(sent.calls, [[{ upload_token: "tok", target_index: 1 }]]);
    assert.equal(app.state.segments.length, 3);
    assert.equal(els.auxSelector.value, "1");
    assert.equal(els.qcSegmentBtn.disabled, false);
    assert.deepEqual(app.setStatus.calls.at(-1), [
      "Target: 1 ramp, 1 plateau, 1 sinusoid (0.50 Hz). Click the windows to decompose",
      "success",
    ]);
  });

  test("segments the channel shown alone, else lets the server find it", async () => {
    const sent = recorder();
    app.api.segmentTarget = async (payload) => (
      sent(payload),
      { segments: [] }
    );
    els.auxSelector.value = "0";
    await requestSegmentation(app);
    app.state.targetIndex = null;
    els.auxSelector.value = "-1";
    await requestSegmentation(app);
    assert.deepEqual(sent.calls, [
      [{ upload_token: "tok", target_index: 0 }],
      [{ upload_token: "tok" }],
    ]);
  });

  test("fills the task, unless one was typed in", async () => {
    els.bidsTask = Object.assign(fakeElement("input"), { value: "trapezoid" });
    app.api.segmentTarget = async () => ({
      segments: [
        { start: 100, end: 200, kind: "ramp", direction: "up" },
        { start: 200, end: 300, kind: "ramp", direction: "down" },
      ],
    });
    await requestSegmentation(app);
    assert.equal(els.bidsTask.value, "triangle");
    assert.match(app.setStatus.calls.at(-1)[0], /Task set to triangle\./);
    setupSessionFormEvents(app);
    els.bidsTask.value = "dynamic";
    els.bidsTask.dispatch("input");
    await requestSegmentation(app);
    assert.equal(els.bidsTask.value, "dynamic");
    assert.doesNotMatch(app.setStatus.calls.at(-1)[0], /Task set/);
  });

  test("reports a refusal and keeps the segments it had", async () => {
    app.api.segmentTarget = async () => {
      throw new Error("The target holds no contraction to segment");
    };
    assert.equal(await requestSegmentation(app), false);
    assert.equal(app.state.segments.length, 4);
    assert.match(app.setStatus.calls.at(-1)[0], /^Target segmentation failed/);
  });
});
