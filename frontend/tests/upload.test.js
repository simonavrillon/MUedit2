// A request that finds its upload gone reloads the file once and goes again.
import { test, describe } from "node:test";
import assert from "node:assert/strict";

const { ApiError } = await import("../src/app/http.js");
const { withUpload } = await import("../src/app/services/upload.js");

const missingUpload = () =>
  new ApiError("Request failed: upload_token Token expired", {
    status: 400,
    field: "upload_token",
  });

/** @param {(path: string) => Promise<unknown>} fetchPreviewByPath */
function appWith(fetchPreviewByPath) {
  const statuses = [];
  return {
    statuses,
    state: { uploadToken: "old", file: { name: "a.otb+", path: "/a.otb+" } },
    api: { fetchPreviewByPath },
    setStatus: (/** @type {string} */ text) => statuses.push(text),
  };
}

/** A request the server answers only for `fresh`. */
function requestFor(fresh, sent = []) {
  return async (/** @type {string} */ token) => {
    sent.push(token);
    if (token !== fresh) throw missingUpload();
    return `ok:${token}`;
  };
}

describe("withUpload", () => {
  test("requests that find the upload gone together reload it once", async () => {
    let reloads = 0;
    const app = appWith(async () => {
      reloads += 1;
      return { upload_token: "new" };
    });
    const sent = [];
    const results = await Promise.all([
      withUpload(/** @type {any} */ (app), requestFor("new", sent)),
      withUpload(/** @type {any} */ (app), requestFor("new", sent)),
    ]);
    assert.deepEqual(results, ["ok:new", "ok:new"]);
    assert.equal(reloads, 1);
    assert.deepEqual(sent, ["old", "old", "new", "new"]);
    assert.equal(app.state.uploadToken, "new");
  });

  test("a reload is not taken over by a file opened meanwhile", async () => {
    const app = appWith(async () => {
      app.state.file = { name: "b.otb+", path: "/b.otb+" };
      return { upload_token: "new" };
    });
    await assert.rejects(
      withUpload(/** @type {any} */ (app), requestFor("new")),
      { field: "upload_token" },
    );
    assert.equal(app.state.uploadToken, "old");
  });

  test("other errors pass through without a reload", async () => {
    const app = appWith(async () => assert.fail("no reload"));
    await assert.rejects(
      withUpload(/** @type {any} */ (app), async () => {
        throw new ApiError("Request failed", { status: 500 });
      }),
      { status: 500 },
    );
  });
});
