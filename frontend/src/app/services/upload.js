/**
 * The upload token behind the QC and run requests. The server can drop an
 * upload (it expired, or the server restarted); a request that finds it gone
 * reloads the file from its path, once, and is sent again.
 */
import { ApiError } from "../http.js";
import { setUploadToken } from "../../state/actions.js";

/** @typedef {import("../context.js").App} App */

/** @param {unknown} err */
function isMissingUpload(err) {
  return err instanceof ApiError && err.field === "upload_token";
}

/**
 * Reloads under way, by the token they replace: requests that find the same
 * upload gone share one reload.
 *
 * @type {Map<string, Promise<string | null>>}
 */
const reloads = new Map();

/**
 * A token for the file whose upload `stale` was, or null when it cannot be
 * had: the file has no path, or another file was opened meanwhile.
 *
 * @param {App} app
 * @param {string} stale
 */
function reloadUpload(app, stale) {
  const { state, api, setStatus } = app;
  const path = state.file?.path;
  if (!path) return Promise.resolve(null);
  let reload = reloads.get(stale);
  if (!reload) {
    reload = (async () => {
      setStatus("Session expired, reloading file...", "muted");
      const preview = await api.fetchPreviewByPath(path);
      const fresh = preview?.upload_token;
      if (typeof fresh !== "string" || !fresh) return null;
      if (state.file?.path !== path) return null;
      setUploadToken(state, fresh);
      setStatus("File reloaded", "muted");
      return fresh;
    })().finally(() => reloads.delete(stale));
    reloads.set(stale, reload);
  }
  return reload;
}

/**
 * Send `request` with the current upload token; when the server no longer
 * has that upload, reload the file and send it once more.
 *
 * @template T
 * @param {App} app
 * @param {(token: string) => Promise<T>} request
 * @returns {Promise<T>}
 */
export async function withUpload(app, request) {
  const token = app.state.uploadToken;
  if (!token) throw new Error("Load a signal first");
  try {
    return await request(token);
  } catch (err) {
    if (!isMissingUpload(err)) throw err;
    // Another request may have reloaded it already.
    const current = app.state.uploadToken;
    const fresh =
      current && current !== token ? current : await reloadUpload(app, token);
    if (!fresh) throw err;
    return request(fresh);
  }
}
