// Opening a file from Browse while the decomposition being edited has unsaved edits.
import { test, describe, beforeEach, afterEach } from "node:test";
import assert from "node:assert/strict";

// config.js reads window.location at import time.
globalThis.window = {
  location: {
    port: "8080",
    protocol: "http:",
    hostname: "localhost",
    origin: "http://localhost:8080",
  },
};

const { state: initialState } = await import("../src/state/state.js");
const { createApp } = await import("../src/app/create-app.js");
const { setupImportEvents } = await import("../src/app/stages/import-stage.js");

const pristine = structuredClone(initialState);

function recorder(result) {
  const calls = [];
  const fn = (...args) => {
    calls.push(args);
    return result;
  };
  fn.calls = calls;
  return fn;
}

/** A Browse button whose click handler can be awaited. */
function browseButton() {
  const button = {
    disabled: false,
    /** @type {(() => void) | null} */
    onClick: null,
    addEventListener(type, listener) {
      if (type === "click") button.onClick = listener;
    },
  };
  return button;
}

const settle = () => new Promise((resolve) => setTimeout(resolve, 0));

/**
 * @param {string} picked The path the file dialog answers.
 * @param {boolean} dirty Whether the open decomposition has unsaved edits.
 */
function browsing(picked, dirty) {
  const state = structuredClone(pristine);
  state.edit.token = "edit-1";
  state.edit.dirty = dirty;
  const browse = browseButton();
  const app = createApp({
    state,
    els: { browseSignalBtn: browse },
    api: {
      openFileDialog: async () => ({
        path: picked,
        name: picked.split("/").pop(),
      }),
    },
  });
  Object.assign(app, {
    setStatus: recorder(),
    setUploadLoading: recorder(),
    handleRawFilePath: recorder(Promise.resolve(true)),
    loadDecompositionForEditByPath: recorder(Promise.resolve()),
  });
  setupImportEvents(app);
  return { app, browse };
}

describe("a file opened over unsaved edits", () => {
  /** @type {string[]} */
  let asked;
  let answer = true;

  beforeEach(() => {
    asked = [];
    globalThis.window.confirm = (message) => {
      asked.push(message);
      return answer;
    };
  });

  afterEach(() => {
    delete globalThis.window.confirm;
  });

  test("is opened only once the user agrees", async () => {
    answer = false;
    const { app, browse } = browsing("/data/b_decomp.npz", true);
    browse.onClick?.();
    await settle();
    assert.equal(asked.length, 1);
    assert.match(asked[0], /unsaved edits\. Open b_decomp\.npz anyway\?/);
    assert.equal(app.loadDecompositionForEditByPath.calls.length, 0);
    assert.equal(browse.disabled, false);

    answer = true;
    browse.onClick?.();
    await settle();
    assert.deepEqual(app.loadDecompositionForEditByPath.calls, [
      ["/data/b_decomp.npz"],
    ]);
  });

  test("a raw file asks too", async () => {
    answer = false;
    const { app, browse } = browsing("/data/rec.otb4", true);
    browse.onClick?.();
    await settle();
    assert.equal(asked.length, 1);
    assert.equal(app.handleRawFilePath.calls.length, 0);
  });

  test("without unsaved edits nothing is asked", async () => {
    const { app, browse } = browsing("/data/rec.otb4", false);
    browse.onClick?.();
    await settle();
    assert.equal(asked.length, 0);
    assert.deepEqual(app.handleRawFilePath.calls, [
      ["/data/rec.otb4", "rec.otb4"],
    ]);
  });
});
