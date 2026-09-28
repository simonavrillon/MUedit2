// Every request names its tab's session, and the tab frees it on the way out.
import { test } from "node:test";
import assert from "node:assert/strict";

const { apiFetch, SESSION_HEADER, SESSION_ID } =
  await import("../src/app/http.js");
const { createApiClient } = await import("../src/api/client.js");

test("the session id is one the backend accepts", () => {
  assert.match(SESSION_ID, /^[A-Za-z0-9-]{1,64}$/);
});

test("apiFetch sends the session header and keeps the caller's", async () => {
  /** @type {Headers | undefined} */
  let sent;
  const realFetch = globalThis.fetch;
  globalThis.fetch = async (_url, init) => {
    sent = new Headers(init?.headers);
    return new Response("{}", { status: 200 });
  };
  try {
    await apiFetch("http://api/x", { headers: { Accept: "text/plain" } });
  } finally {
    globalThis.fetch = realFetch;
  }
  assert.equal(sent?.get(SESSION_HEADER), SESSION_ID);
  assert.equal(sent?.get("Accept"), "text/plain");
});

test("closeSession sends a beacon naming the session", () => {
  /** @type {string[]} */
  const beacons = [];
  const realNavigator = Object.getOwnPropertyDescriptor(
    globalThis,
    "navigator",
  );
  Object.defineProperty(globalThis, "navigator", {
    value: { sendBeacon: (/** @type {string} */ url) => beacons.push(url) > 0 },
    configurable: true,
  });
  try {
    const api = createApiClient({
      apiFetch,
      apiJson: async () => ({}),
      API_BASE: "http://api/v1",
      sessionId: "tab-1",
    });
    assert.equal(api.closeSession(), true);
  } finally {
    if (realNavigator)
      Object.defineProperty(globalThis, "navigator", realNavigator);
    else delete (/** @type {any} */ (globalThis).navigator);
  }
  assert.deepEqual(beacons, ["http://api/v1/session/close?session=tab-1"]);
});
