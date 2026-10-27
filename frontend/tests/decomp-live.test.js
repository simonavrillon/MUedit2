// The run page's live model, fed by the NDJSON run stream: phases, dots,
// units kept and the time left.
import { test, describe } from "node:test";
import assert from "node:assert/strict";

const live = await import("../src/decomp/live.js");
const { DOT, applyRunEvent, createRunLive } = live;

const shape = { phase: "decompose", ngrid: 2, nwindows: 1, niter: 6 };

describe("applyRunEvent", () => {
  test("maps the backend's phases onto the track", () => {
    const run = createRunLive(0);
    applyRunEvent(run, { phase: "preprocess" }, 1);
    assert.equal(run.phase, "preprocess");
    applyRunEvent(run, { phase: "export" }, 2);
    assert.equal(run.phase, "save");
    applyRunEvent(run, { stage: "progress", message: "no phase" }, 3);
    assert.equal(run.phase, "save");
  });

  test("lays out a row per grid × window and fills its dots", () => {
    const run = createRunLive(0);
    applyRunEvent(
      run,
      { ...shape, grid: 0, window: 0, iter: 0, outcomes: "" },
      5,
    );
    assert.equal(run.rows.length, 2);
    assert.equal(run.searchStartedAt, 5);
    const change = applyRunEvent(
      run,
      { ...shape, grid: 1, window: 0, iter: 5, outcomes: "krfkr" },
      6,
    );
    assert.deepEqual(change, { row: 1, from: 0, to: 5 });
    assert.deepEqual(Array.from(run.rows[1].dots), [
      DOT.kept,
      DOT.rejected,
      DOT.fewSpikes,
      DOT.kept,
      DOT.rejected,
      DOT.pending,
    ]);
    assert.deepEqual(run.keptByGrid, [0, 2]);
    assert.equal(live.keptTotal(run), 2);
  });

  test("a window that stops early marks its remaining dots skipped", () => {
    const run = createRunLive(0);
    applyRunEvent(
      run,
      {
        ...shape,
        grid: 0,
        window: 0,
        iter: 2,
        outcomes: "kk",
        window_done: true,
      },
      1,
    );
    assert.deepEqual(Array.from(run.rows[0].dots.slice(2)), [
      DOT.skipped,
      DOT.skipped,
      DOT.skipped,
      DOT.skipped,
    ]);
    assert.equal(run.rows[0].done, 6);
  });
});

describe("searchSecondsLeft", () => {
  test("projects the pace of the iterations so far", () => {
    const run = createRunLive(0);
    applyRunEvent(
      run,
      { ...shape, grid: 0, window: 0, iter: 0, outcomes: "" },
      0,
    );
    run.rows[0].done = 6;
    run.rows[1].done = 0;
    assert.equal(live.searchSecondsLeft(run, 1000), null, "too early");
    assert.equal(
      live.searchSecondsLeft(run, 12_000),
      null,
      "too few iterations",
    );
    run.rows[1].done = 6;
    run.rows[0].done = 6;
    run.niter = 12;
    assert.equal(live.searchSecondsLeft(run, 12_000), 12);
  });
});
