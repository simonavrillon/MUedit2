// The desktop page takes its token and dialogs from the pywebview bridge; the
// browser page sends no token and uses the backend's dialog route.
import { test } from "node:test";
import assert from "node:assert/strict";

const { apiFetch, setAppToken, TOKEN_HEADER } =
  await import("../src/app/http.js");

/** Headers of one `apiFetch` call. */
async function sentHeaders() {
  /** @type {Headers | undefined} */
  let headers;
  const realFetch = globalThis.fetch;
  globalThis.fetch = async (_url, init) => {
    headers = new Headers(init?.headers);
    return new Response("{}", { status: 200 });
  };
  try {
    await apiFetch("http://api/x");
  } finally {
    globalThis.fetch = realFetch;
  }
  return headers;
}

/**
 * A fresh copy of platform.js loaded as the page at `search` would load it.
 *
 * @param {string} search
 */
async function loadPlatform(search) {
  const realLocation = globalThis.location;
  Object.defineProperty(globalThis, "location", {
    value: { search },
    configurable: true,
  });
  try {
    return await import(`../src/app/platform.js?${Math.random()}`);
  } finally {
    Object.defineProperty(globalThis, "location", {
      value: realLocation,
      configurable: true,
    });
  }
}

test("the browser page sends no token and uses the dialog route", async () => {
  assert.equal((await sentHeaders())?.has(TOKEN_HEADER), false);
  const platform = await loadPlatform("");
  assert.equal(platform.IS_DESKTOP, false);
  assert.equal(await platform.initPlatform(), true);
  const picked = { path: "/data/rec.otb+", name: "rec.otb+" };
  assert.deepEqual(await platform.openFile(async () => picked), picked);
  assert.equal((await sentHeaders())?.has(TOKEN_HEADER), false);
});

test("the desktop page waits for the bridge and sends its token", async () => {
  const platform = await loadPlatform("?desktop=1");
  assert.equal(platform.IS_DESKTOP, true);
  const target = new EventTarget();
  const realAdd = globalThis.addEventListener;
  globalThis.addEventListener = target.addEventListener.bind(target);
  try {
    const ready = platform.initPlatform();
    // pywebview injects the bridge after the page's modules have run.
    globalThis.pywebview = {
      api: {
        token: async () => "t0ken",
        app_info: async () => ({
          version: "2.1.0",
          data_root: "/Users/me/Documents/MUedit",
          log_file: "/tmp/muedit.log",
        }),
        open_file: async () => ({ path: "/data/a.npz", name: "a.npz" }),
        choose_output_folder: async () => ({ path: null }),
      },
    };
    target.dispatchEvent(new Event("pywebviewready"));
    assert.equal(await ready, true);
    assert.equal((await sentHeaders())?.get(TOKEN_HEADER), "t0ken");
    const fallback = async () => {
      throw new Error("the browser dialog must not open");
    };
    assert.deepEqual(await platform.openFile(fallback), {
      path: "/data/a.npz",
      name: "a.npz",
    });
    assert.equal(await platform.outputFolder(), "/Users/me/Documents/MUedit");
    assert.equal(await platform.chooseOutputFolder(), null);
  } finally {
    globalThis.addEventListener = realAdd;
    delete globalThis.pywebview;
    setAppToken("");
  }
});

test("the desktop page gives up when the bridge never comes", async () => {
  const platform = await loadPlatform("?desktop=1");
  const realAdd = globalThis.addEventListener;
  globalThis.addEventListener = () => {};
  try {
    assert.equal(await platform.initPlatform({ timeoutMs: 10 }), false);
    assert.equal((await sentHeaders())?.has(TOKEN_HEADER), false);
  } finally {
    globalThis.addEventListener = realAdd;
  }
});
