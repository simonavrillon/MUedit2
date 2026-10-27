// The real app context over stand-in elements and a fake API, and the small
// values tests build edit sessions and failures from. Not a test file itself.
// Importing it installs the DOM first: config.js reads window.location at import.
import { installDom, recorder } from "./_dom.js";

installDom();

const { state: initialState } = await import("../src/state/state.js");
const { createApp } = await import("../src/app/create-app.js");
const { ApiError } = await import("../src/app/http.js");

const pristine = structuredClone(initialState);

/** A fresh copy of the page's initial state. */
export const freshState = () => structuredClone(pristine);

/** State as it is once a raw file has opened: its upload, traces and channels. */
export function loadedState() {
  return Object.assign(freshState(), {
    file: { name: "old.otb+", path: "/data/old.otb+" },
    uploadToken: "old-token",
    gridSeries: [new Float32Array([1, 2, 3])],
    gridNames: ["GR08MM1305"],
    channelMeans: [[1, 1]],
    channelTraces: [[0.5]],
    totalSamples: 3,
    discardChannels: [[0, 1]],
  });
}

/**
 * `createApp` over `state`, `els` and `api`, with `setStatus` recording what
 * the page would show.
 */
export function testApp({ state = freshState(), els = {}, api = {} } = {}) {
  const app = createApp({ state, els, api });
  app.setStatus = recorder();
  return app;
}

/** The error the backend answers for an upload it no longer holds. */
export const missingUpload = () =>
  new ApiError(
    "Missing or expired upload; request /api/v1/preview-by-path first",
    { status: 400, code: "http_400", field: "upload_token" },
  );

/** Let pending promise callbacks run. */
export const settle = () => new Promise((resolve) => setTimeout(resolve, 0));

export const ints = (...values) => Int32Array.from(values);

/** Typed-array rows as plain arrays, for deepEqual. */
export const rows = (list) => list.map((r) => Array.from(r));
