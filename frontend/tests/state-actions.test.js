// State actions and transitions that carry logic beyond a plain set.
import { test, describe, beforeEach } from "node:test";
import assert from "node:assert/strict";

import { freshState, ints, rows } from "./_app.js";

const actions = await import("../src/state/actions.js");
const { beginRawPreviewTransition } =
  await import("../src/state/transitions.js");

let state;

beforeEach(() => {
  state = freshState();
});

describe("setGridNames", () => {
  test("keeps the current grid inside the new grids", () => {
    state.currentGrid = 5;
    actions.setGridNames(state, ["a", "b"]);
    assert.equal(state.currentGrid, 1);
    actions.setGridNames(state, []);
    assert.equal(state.currentGrid, 0);
  });
});

describe("setRoiForIndex", () => {
  test("replaces one window and ignores a missing index", () => {
    state.rois = [
      { start: 0, end: 5 },
      { start: 0, end: 5 },
    ];
    actions.setRoiForIndex(state, 1, { start: 10, end: 20, extra: true });
    assert.deepEqual(state.rois, [
      { start: 0, end: 5 },
      { start: 10, end: 20 },
    ]);
    actions.setRoiForIndex(state, 2, { start: 1, end: 2 });
    actions.setRoiForIndex(state, -1, { start: 1, end: 2 });
    assert.equal(state.rois.length, 2);
  });
});

describe("artifact regions", () => {
  test("removeLastArtifactRegion reports whether it removed anything", () => {
    state.artifactRegions = [{ start: 1, end: 2 }];
    assert.equal(actions.removeLastArtifactRegion(state), true);
    assert.equal(actions.removeLastArtifactRegion(state), false);
  });
});

describe("discard channels", () => {
  test("setDiscardChannels coerces to 0/1, and a non-array changes nothing", () => {
    actions.setDiscardChannels(state, [[true, 0, 2], "bad"]);
    assert.deepEqual(state.discardChannels, [[1, 0, 1], []]);
    actions.setDiscardChannels(state, null);
    assert.deepEqual(state.discardChannels, [[1, 0, 1], []]);
  });

  test("setDiscardChannel creates a missing grid row", () => {
    state.discardChannels = [];
    actions.setDiscardChannel(state, 1, 2, true);
    assert.equal(state.discardChannels[1][2], 1);
  });
});

describe("edit slice", () => {
  /** A session frame as `decodeEditSessionFrame` returns it. */
  function sessionFrame() {
    return {
      meta: {
        token: "tok",
        n_mu: 2,
        flagged: [false, true],
        mu_uids: ["g0_mu0", "g0_mu1"],
        mu_grid_index: [0, 1],
        versions: [3, 4],
        has_pulse: [true, true],
        dirty: false,
        can_undo: false,
        edit_history: [{ type: "flag_mu", mu_uid: "g0_mu1" }],
        fsamp: 2048,
        total_samples: 5000,
        parameters: { duplicatesthresh: 0.3 },
      },
      spikeTimes: [ints(10, 20), ints(30)],
    };
  }

  test("setEditSession takes over the whole session", () => {
    state.edit.pulseView = { mu: 0 };
    actions.setEditSession(state, sessionFrame());
    const e = state.edit;
    assert.equal(e.token, "tok");
    assert.deepEqual(rows(e.spikeTimes), [[10, 20], [30]]);
    assert.deepEqual(e.flagged, [false, true]);
    assert.deepEqual(e.versions, [3, 4]);
    assert.equal(e.totalSamples, 5000);
    assert.equal(e.fsamp, 2048);
    assert.equal(e.editHistory.length, 1);
    assert.equal(e.pulseView, null);
  });

  test("applyEditChange replaces the changed MU and the new log tail", () => {
    actions.setEditSession(state, sessionFrame());
    actions.applyEditChange(state, {
      meta: {
        n_mu: 2,
        changed: [0],
        flagged: [false, true],
        mu_uids: ["g0_mu0", "g0_mu1"],
        mu_grid_index: [0, 1],
        versions: [9, 4],
        has_pulse: [true, true],
        dirty: true,
        can_undo: true,
        history_start: 1,
        history: [{ type: "delete_spikes", mu_uid: "g0_mu0" }],
      },
      spikeTimes: [ints(10)],
    });
    const e = state.edit;
    assert.deepEqual(rows(e.spikeTimes), [[10], [30]]);
    assert.deepEqual(e.versions, [9, 4]);
    assert.equal(e.dirty, true);
    assert.equal(e.canUndo, true);
    assert.deepEqual(
      e.editHistory.map((h) => h.type),
      ["flag_mu", "delete_spikes"],
    );
  });

  test("applyEditChange appends a duplicate and cuts the log on undo", () => {
    actions.setEditSession(state, sessionFrame());
    const perMu = (n) => ({
      n_mu: n,
      flagged: new Array(n).fill(false),
      mu_uids: ["a", "b", "c"].slice(0, n),
      mu_grid_index: new Array(n).fill(0),
      versions: new Array(n).fill(1),
      has_pulse: new Array(n).fill(true),
    });
    actions.applyEditChange(state, {
      meta: {
        ...perMu(3),
        changed: [2],
        history_start: 1,
        history: [{ type: "duplicate_mu" }],
      },
      spikeTimes: [ints(30)],
    });
    assert.deepEqual(rows(state.edit.spikeTimes), [[10, 20], [30], [30]]);
    actions.applyEditChange(state, {
      meta: {
        ...perMu(2),
        changed: [],
        kept_indices: [0, 1],
        history_start: 1,
        history: [],
      },
      spikeTimes: [],
    });
    assert.deepEqual(rows(state.edit.spikeTimes), [[10, 20], [30]]);
    assert.equal(state.edit.editHistory.length, 1);
  });

  test("a log tail keeps its server indices through edits and undo", () => {
    const frame = sessionFrame();
    frame.meta.edit_history = [{ type: "flag_mu" }, { type: "add_spikes" }];
    frame.meta.edit_history_start = 40; // entries 40 and 41 of the server's log
    actions.setEditSession(state, frame);
    const change = (start, history) =>
      actions.applyEditChange(state, {
        meta: { ...frame.meta, changed: [], history_start: start, history },
        spikeTimes: [],
      });
    change(42, [{ type: "delete_spikes" }]);
    assert.deepEqual(
      state.edit.editHistory.map((h) => h.type),
      ["flag_mu", "add_spikes", "delete_spikes"],
    );
    change(41, []); // undo back to entry 41
    assert.deepEqual(
      state.edit.editHistory.map((h) => h.type),
      ["flag_mu"],
    );
    change(30, []); // undo past the tail: nothing left before entry 30
    assert.deepEqual(state.edit.editHistory, []);
    assert.equal(state.edit.editHistoryStart, 30);
  });

  test("kept MUs reorder the times and follow the current MU", () => {
    Object.assign(state.edit, {
      spikeTimes: [ints(0), ints(1), ints(2)],
      currentMu: 2,
      bookmark: { mu: 1, position: 50 },
    });
    actions.applyEditSave(state, { kept_indices: [2, 1], n_mu: 2 });
    const e = state.edit;
    assert.deepEqual(rows(e.spikeTimes), [[2], [1]]);
    assert.equal(e.currentMu, 0);
    assert.deepEqual(e.bookmark, { mu: 1, position: 50 });
  });

  test("removing the current MU falls back to MU 0 and drops its bookmark", () => {
    Object.assign(state.edit, {
      spikeTimes: [ints(0), ints(1)],
      muUids: ["g0_mu0", "g0_mu1"],
      currentMu: 1,
      bookmark: { mu: 1, position: 5 },
    });
    actions.applyEditSave(state, { kept_indices: [0], n_mu: 1 });
    assert.equal(state.edit.currentMu, 0);
    assert.equal(state.edit.bookmark, null);
  });

  test("applyEditSave mirrors the MUs the file kept", () => {
    actions.setEditSession(state, sessionFrame());
    actions.applyEditSave(state, {
      kept_indices: [0],
      n_mu: 1,
      flagged: [false],
      mu_uids: ["g0_mu0"],
      mu_grid_index: [0],
      versions: [3],
      has_pulse: [true],
      dirty: false,
      can_undo: false,
      edit_history: [{ type: "remove_flagged", on_save: true }],
    });
    assert.deepEqual(rows(state.edit.spikeTimes), [[10, 20]]);
    assert.deepEqual(state.edit.muUids, ["g0_mu0"]);
    assert.equal(state.edit.editHistory[0].type, "remove_flagged");
  });
});

describe("setFsamp", () => {
  test("keeps positive numbers and nulls everything else", () => {
    actions.setFsamp(state, "2048");
    assert.equal(state.fsamp, 2048);
    for (const bad of [0, -1, "x", undefined]) {
      actions.setFsamp(state, bad);
      assert.equal(state.fsamp, null, String(bad));
    }
  });
});

describe("raw preview transitions", () => {
  test("begin resets the edit slice and the traces", () => {
    state.edit.dirty = true;
    state.channelTraces = [[1]];
    const file = { name: "new.otb+" };
    beginRawPreviewTransition(state, file);
    assert.deepEqual(state.edit, freshState().edit);
    assert.equal(state.file, file);
    assert.deepEqual(state.channelTraces, []);
  });
});
