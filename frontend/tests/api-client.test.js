// The API client: the tab frees its server session on the way out.
import { test, describe } from "node:test";
import assert from "node:assert/strict";

const { apiFetch, SESSION_HEADER, SESSION_ID } =
  await import("../src/app/http.js");
const { createApiClient } = await import("../src/api/client.js");

describe("closeSession", () => {
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
});
