// Every request names its tab's session, and an error response reaches the
// caller as an ApiError with the envelope's code and field.
import { test, describe } from "node:test";
import assert from "node:assert/strict";

const { apiFetch, SESSION_HEADER, SESSION_ID } =
  await import("../src/app/http.js");

describe("the tab's session", () => {
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
});

describe("error responses", () => {
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
});
