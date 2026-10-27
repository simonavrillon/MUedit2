// A recording segmented into target phases, and the QC page over it, for the
// segment tests of signal/segments.js and view/qc-renderer.js. Not a test file.
import { fakeCanvas, fakeElement, recorder } from "./_dom.js";
import { freshState, testApp } from "./_app.js";

/** A segment as `toSegments` returns it. */
export const seg = (start, end, kind, extra = {}) => ({
  start,
  end,
  kind,
  direction: null,
  cycles: [],
  frequencyHz: null,
  ...extra,
});

// A trapezoid, rest, then a sinusoid block, over 1000 samples at 10 Hz.
const SEGMENTS = [
  seg(100, 200, "ramp", { direction: "up" }),
  seg(200, 400, "plateau"),
  seg(400, 500, "ramp", { direction: "down" }),
  seg(600, 900, "sinusoid", { cycles: [650, 750, 850], frequencyHz: 0.1 }),
];

/** The QC page over the segmented recording, one canvas pixel per sample. */
export function segmentedApp() {
  const state = Object.assign(freshState(), {
    totalSamples: 1000,
    fsamp: 10,
    uploadToken: "tok",
    auxNames: ["Acc", "Target"],
    targetIndex: 1,
    rois: [{ start: 0, end: 1000 }],
    segments: structuredClone(SEGMENTS),
  });
  const els = {
    emgCanvas: fakeCanvas({ width: 1000, height: 80 }),
    auxCanvas: fakeCanvas({ width: 1000, height: 50 }),
    auxSelector: Object.assign(fakeElement("select"), { value: "-1" }),
    nwindows: Object.assign(fakeElement("input"), { value: "1" }),
    qcSegmentBtn: fakeElement("button"),
    segmentTypeBtn: fakeElement("button"),
    segmentTypeMenu: Object.assign(fakeElement("div"), {
      hidden: true,
      contains: () => false,
    }),
  };
  const app = testApp({ state, els });
  Object.assign(app, {
    refreshVisuals: recorder(),
    scheduleRefreshVisuals: recorder(),
    ensureQcTraces: recorder(),
  });
  return { app, els };
}
