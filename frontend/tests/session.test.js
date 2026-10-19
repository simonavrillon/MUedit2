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

test("closeSession posts a keepalive request naming the session", async () => {
  /** @type {{ url: string, init: RequestInit | undefined }[]} */
  const sent = [];
  const realFetch = globalThis.fetch;
  globalThis.fetch = async (url, init) => {
    sent.push({ url: String(url), init });
    return new Response(null, { status: 204 });
  };
  try {
    const api = createApiClient({
      apiFetch,
      apiJson: async () => ({}),
      API_BASE: "http://api/v1",
      sessionId: "tab-1",
    });
    api.closeSession();
    await new Promise((resolve) => setTimeout(resolve, 0));
  } finally {
    globalThis.fetch = realFetch;
  }
  assert.equal(sent.length, 1);
  assert.equal(sent[0].url, "http://api/v1/session/close?session=tab-1");
  assert.equal(sent[0].init?.method, "POST");
  assert.equal(sent[0].init?.keepalive, true);
  // Unlike a beacon, it carries the headers.
  assert.equal(
    new Headers(sent[0].init?.headers).get(SESSION_HEADER),
    SESSION_ID,
  );
});

test("an error response keeps the envelope's code and field", async () => {
  const realFetch = globalThis.fetch;
  globalThis.fetch = async () =>
    new Response(
      JSON.stringify({
        error: {
          code: "http_400",
          message:
            "Missing or expired upload; request /api/v1/preview-by-path first",
          detail: { field: "upload_token" },
        },
      }),
      { status: 400 },
    );
  try {
    await assert.rejects(apiFetch("http://api/x"), {
      name: "ApiError",
      message:
        "Missing or expired upload; request /api/v1/preview-by-path first",
      status: 400,
      code: "http_400",
      field: "upload_token",
    });
  } finally {
    globalThis.fetch = realFetch;
  }
});

test("a 422 names the first field validation refused", async () => {
  const realFetch = globalThis.fetch;
  globalThis.fetch = async () =>
    new Response(
      JSON.stringify({
        error: {
          code: "validation_error",
          message: "Request validation failed",
          detail: [
            { loc: ["body", "mu"], msg: "Input should be a valid integer" },
          ],
        },
      }),
      { status: 422 },
    );
  try {
    await assert.rejects(apiFetch("http://api/x"), {
      name: "ApiError",
      message:
        "Request validation failed: body.mu Input should be a valid integer",
      status: 422,
      code: "validation_error",
      field: "",
    });
  } finally {
    globalThis.fetch = realFetch;
  }
});
