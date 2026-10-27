// The edit service's round trips to the server-side session: each edit sends
// its request and mirrors what the server changed; a save, a timeout, a session
// switched mid-edit and a file that fails to open.
import { test, describe, beforeEach } from "node:test";
import assert from "node:assert/strict";

import { recorder } from "./_dom.js";
import { freshState, ints, rows, testApp } from "./_app.js";

const {
  undoEdit,
  duplicateMu,
  removeDuplicateMus,
  flagMuForDeletion,
  resetCurrentMuEdits,
  saveEditedFile,
  removeOutliers,
  loadDecompositionForEdit,
} = await import("../src/app/services/editing-service.js");
const { RequestTimeout } = await import("../src/app/http.js");

console.error = () => {};

/** Two MUs on one grid. */
function editState() {
  const state = freshState();
  state.gridNames = ["GR08MM1305"];
  Object.assign(state.edit, {
    token: "tok",
    spikeTimes: [ints(2, 5, 8), ints(1, 4)],
    flagged: [false, false],
    muUids: ["g0_mu0", "g0_mu1"],
    muGridIndex: [0, 0],
    versions: [1, 2],
    fsamp: 2000,
    totalSamples: 10,
  });
  return state;
}

/**
 * A change frame as the server sends it for `state`'s two MUs.
 *
 * @param {object} meta
 * @param {number[][]} [spikes] of the MUs in `meta.changed`
 */
function changeFrame(meta, spikes = []) {
  const n = meta.n_mu ?? 2;
  return {
    meta: {
      n_mu: n,
      flagged: new Array(n).fill(false),
      mu_uids: ["g0_mu0", "g0_mu1", "g0_mu2"].slice(0, n),
      mu_grid_index: new Array(n).fill(0),
      versions: new Array(n).fill(5),
      has_pulse: new Array(n).fill(true),
      dirty: true,
      can_undo: true,
      changed: [],
      history_start: 0,
      history: [],
      ...meta,
    },
    spikeTimes: spikes.map((r) => ints(...r)),
  };
}

/** An API whose edit operations answer with `frames[op]` and record requests. */
function fakeApi(frames) {
  const calls = [];
  return {
    calls,
    editOp: async (op, payload) => {
      calls.push([op, payload]);
      const frame = frames[op];
      if (frame instanceof Error) throw frame;
      return frame;
    },
  };
}

let state;
let app;

beforeEach(() => {
  state = editState();
  app = testApp({ state });
  app.renderEditExplorer = recorder();
});

describe("edits on the server", () => {
  test("an ROI edit sends the box and mirrors the changed MU", async () => {
    app.api = fakeApi({
      "delete-spikes": changeFrame(
        {
          changed: [0],
          history: [{ type: "delete_spikes", mu_uid: "g0_mu0" }],
        },
        [[2, 8]],
      ),
    });
    await app.requestRoiEdit("delete-spikes", {
      mu: 0,
      xStart: 4,
      xEnd: 6,
      yMin: 1,
      yMax: 7,
    });
    assert.deepEqual(app.api.calls, [
      [
        "delete-spikes",
        { token: "tok", mu: 0, x_start: 4, x_end: 6, y_min: 1, y_max: 7 },
      ],
    ]);
    assert.deepEqual(rows(state.edit.spikeTimes), [
      [2, 8],
      [1, 4],
    ]);
    assert.deepEqual(state.edit.versions, [5, 5]);
    assert.equal(state.edit.dirty, true);
    assert.equal(state.edit.canUndo, true);
    assert.deepEqual(state.edit.bookmark, { mu: 0, position: 5 });
    assert.equal(state.edit.editHistory.at(-1).type, "delete_spikes");
    assert.deepEqual(app.setStatus.calls.at(-1), ["ROI applied", "success"]);
  });

  test("undo asks the server and drops the log entries it took back", async () => {
    state.edit.canUndo = true;
    state.edit.editHistory = [{ type: "first" }, { type: "delete_spikes" }];
    state.edit.pulseSelection = { start: 1, end: 2 };
    app.api = fakeApi({
      undo: changeFrame(
        { changed: [1], history_start: 1, can_undo: false, dirty: false },
        [[1, 4, 6]],
      ),
    });
    await undoEdit(app);
    assert.deepEqual(rows(state.edit.spikeTimes), [
      [2, 5, 8],
      [1, 4, 6],
    ]);
    assert.deepEqual(
      state.edit.editHistory.map((e) => e.type),
      ["first"],
    );
    assert.equal(state.edit.canUndo, false);
    assert.equal(state.edit.pulseSelection, null);
    assert.deepEqual(app.setStatus.calls.at(-1), ["Undo applied", "success"]);
  });

  test("an edit the server cannot log warns that a crash would lose it", async () => {
    state.edit.canUndo = true;
    app.api = fakeApi({
      undo: changeFrame(
        {
          changed: [1],
          history_start: 0,
          can_undo: false,
          crash_recovery: false,
        },
        [[1, 4, 6]],
      ),
    });
    await undoEdit(app);
    assert.equal(state.edit.crashRecovery, false);
    assert.deepEqual(app.setStatus.calls.at(-1), [
      "Undo applied; unsaved edits will not survive a crash: save to keep them",
      "error",
    ]);
  });

  test("nothing to undo says so and asks nothing", async () => {
    app.api = fakeApi({});
    await undoEdit(app);
    assert.deepEqual(app.setStatus.calls, [["Nothing to undo", "muted"]]);
    assert.equal(app.api.calls.length, 0);
  });

  test("duplicating switches to the new MU", async () => {
    state.edit.currentMu = 1;
    app.api = fakeApi({
      duplicate: changeFrame(
        {
          n_mu: 3,
          changed: [2],
          history_start: 0,
          history: [{ type: "duplicate_mu", mu_uid: "g0_mu2" }],
        },
        [[1, 4]],
      ),
    });
    await duplicateMu(app);
    assert.deepEqual(app.api.calls[0], ["duplicate", { token: "tok", mu: 1 }]);
    assert.equal(state.edit.spikeTimes.length, 3);
    assert.equal(state.edit.currentMu, 2);
    assert.deepEqual(app.setStatus.calls.at(-1), [
      "MU duplicated — now editing MU 3",
      "success",
    ]);
  });

  test("removing duplicates keeps the MUs the server kept", async () => {
    state.edit.spikeTimes.push(ints(2, 5, 9));
    state.edit.muUids.push("g0_mu2");
    state.edit.currentMu = 2;
    app.api = fakeApi({
      "remove-duplicates": changeFrame({
        kept_indices: [1, 2],
        removed_count: 1,
        can_undo: false,
        mu_uids: ["g0_mu1", "g0_mu2"],
      }),
    });
    await removeDuplicateMus(app);
    assert.deepEqual(rows(state.edit.spikeTimes), [
      [1, 4],
      [2, 5, 9],
    ]);
    assert.deepEqual(state.edit.muUids, ["g0_mu1", "g0_mu2"]);
    assert.equal(state.edit.currentMu, 1);
    assert.deepEqual(app.setStatus.calls.at(-1), [
      "1 duplicate removed",
      "success",
    ]);
  });

  test("no duplicates found changes nothing", async () => {
    app.api = fakeApi({
      "remove-duplicates": changeFrame({ removed_count: 0, dirty: false }),
    });
    await removeDuplicateMus(app);
    assert.deepEqual(app.setStatus.calls.at(-1), [
      "No duplicates found",
      "muted",
    ]);
  });

  test("flagging toggles the current MU's flag", async () => {
    app.api = fakeApi({
      flag: changeFrame({ changed: [0], flagged: [true, false] }, [[2, 5, 8]]),
    });
    await flagMuForDeletion(app);
    assert.deepEqual(app.api.calls[0], [
      "flag",
      { token: "tok", mu: 0, flag: true },
    ]);
    assert.deepEqual(state.edit.flagged, [true, false]);
  });

  test("a failed edit reports it and leaves the state alone", async () => {
    app.api = fakeApi({
      reset: new Error("HTTP 400: mu: mu 9 is out of range [0, 3)"),
    });
    await resetCurrentMuEdits(app);
    assert.deepEqual(rows(state.edit.spikeTimes), [
      [2, 5, 8],
      [1, 4],
    ]);
    assert.match(app.setStatus.calls.at(-1)[0], /mu 9 is out of range/);
  });
});

describe("saveEditedFile", () => {
  function stubSave(response) {
    const payloads = [];
    Object.assign(app, {
      getBidsMuscleNames: () => ["TA"],
      withBidsSaveFields: (payload) => ({ ...payload, project: "p" }),
    });
    app.api = {
      editSessionSave: async (payload) => {
        payloads.push(payload);
        return {
          saved: true,
          path: "/out.npz",
          ...changeFrame({ dirty: false, can_undo: false }).meta,
          ...response,
        };
      },
    };
    return payloads;
  }

  test("sends only the session token and the form's fields", async () => {
    state.edit.filename = "sub-01_task-x_decomp.npz";
    const payloads = stubSave({});
    await saveEditedFile(app);
    assert.deepEqual(payloads[0], {
      token: "tok",
      muscle_names: ["TA"],
      file_label: "sub-01_task-x_decomp_edited.npz",
      software_versions: null,
      project: "p",
    });
    assert.equal("remove_duplicates" in payloads[0], false);
  });

  test("mirrors the MUs and log entries the save removed", async () => {
    const saveEntry = {
      type: "remove_flagged",
      on_save: true,
      removed_count: 1,
      removed_mu_uids: ["g0_mu0"],
    };
    stubSave({
      kept_indices: [1],
      n_mu: 1,
      flagged: [false],
      mu_uids: ["g0_mu1"],
      mu_grid_index: [0],
      versions: [2],
      has_pulse: [true],
      edit_history: [saveEntry],
    });
    await saveEditedFile(app);
    assert.deepEqual(state.edit.muUids, ["g0_mu1"]);
    assert.deepEqual(rows(state.edit.spikeTimes), [[1, 4]]);
    assert.deepEqual(state.edit.flagged, [false]);
    assert.deepEqual(state.edit.editHistory, [saveEntry]);
    assert.equal(state.edit.dirty, false);
    assert.equal(app.renderEditExplorer.calls.length, 1);
  });
});

describe("a session left during an edit", () => {
  test("the change it answers is not applied to the new one", async () => {
    app.api = {
      editOp: async () => {
        state.edit.token = "other";
        return changeFrame({ changed: [0] }, [[7]]);
      },
    };
    const before = rows(state.edit.spikeTimes);
    await removeOutliers(app);
    assert.deepEqual(rows(state.edit.spikeTimes), before);
    assert.match(
      app.setStatus.calls.at(-1)[0],
      /Another decomposition was opened meanwhile/,
    );
    assert.equal(state.edit.busy, false);
  });
});

describe("an edit the page gave up on", () => {
  test("the page takes the session as the server left it", async () => {
    const served = changeFrame({ token: "tok" }, []);
    served.spikeTimes = [ints(2, 6), ints(1, 4)];
    app.api = {
      editOp: async () => {
        throw new RequestTimeout();
      },
      editSessionState: async () => served,
    };
    await removeOutliers(app);
    assert.deepEqual(rows(state.edit.spikeTimes), [
      [2, 6],
      [1, 4],
    ]);
    assert.deepEqual(state.edit.versions, [5, 5]);
    assert.match(
      app.setStatus.calls.at(-1)[0],
      /Outlier removal failed: Request timed out; the page now shows the session as the server left it/,
    );
    assert.equal(state.edit.busy, false);
  });

  test("a session the server cannot answer for stays as the page had it", async () => {
    app.api = {
      editOp: async () => {
        throw new RequestTimeout();
      },
      editSessionState: async () => {
        throw new Error("gone");
      },
    };
    const before = rows(state.edit.spikeTimes);
    await removeOutliers(app);
    assert.deepEqual(rows(state.edit.spikeTimes), before);
    assert.match(
      app.setStatus.calls.at(-1)[0],
      /Outlier removal failed: Request timed out$/,
    );
  });
});

describe("a decomposition that fails to open", () => {
  function opening(api) {
    app.api = api;
    Object.assign(app, {
      setUploadLoading: recorder(),
      resetEditState: recorder(),
    });
  }

  test("leaves the one open as it was", async () => {
    opening({
      editOpen: async () => {
        throw new Error("not a decomposition");
      },
    });
    const before = structuredClone(state.edit);
    await loadDecompositionForEdit(app, { name: "b.npz" }, "/data/b.npz");
    assert.deepEqual(state.edit, before);
    assert.equal(app.resetEditState.calls.length, 0);
    assert.match(
      app.setStatus.calls.at(-1)[0],
      /Failed to load: not a decomposition/,
    );
  });

  test("once opened, a failed recovery leaves nothing of the closed one", async () => {
    globalThis.window.confirm = () => true;
    opening({
      editOpen: async () =>
        changeFrame({ token: "new", recoverable_edits: 2 }, []),
      editRecover: async () => {
        throw new Error("log unreadable");
      },
    });
    await loadDecompositionForEdit(app, { name: "b.npz" }, "/data/b.npz");
    delete globalThis.window.confirm;
    assert.equal(app.resetEditState.calls.length, 1);
  });
});
