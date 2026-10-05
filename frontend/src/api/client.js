/**
 * API client seam. Wraps URL composition and response decoding so domain
 * modules (signal/, decomp/, editing/) never import routes or binary
 * decoders directly. Created once in the container and injected via deps.
 */
import { routes } from "./routes.js";
import {
  decodeSeriesFrame,
  decodeEditSessionFrame,
  decodePulseFrame,
} from "./binary-payloads.js";

/** @typedef {import("../app/context.js").JsonObject} JsonObject */
/** @typedef {"emg" | "overview" | "aux"} SeriesKind */

/** Automatic QC filters every channel of the recording. */
const AUTO_QC_TIMEOUT_MS = 300000;
/**
 * Filtering one grid for the edit stage takes a while on long recordings; a
 * filter update waits for it, and so does a request queued behind that update.
 */
const GRID_FILTER_TIMEOUT_MS = 600000;

const SERIES_ROUTES = {
  emg: routes.seriesEmg,
  overview: routes.seriesOverview,
  aux: routes.seriesAux,
};

/**
 * @param {{ apiFetch: typeof import("../app/http.js").apiFetch, apiJson: typeof import("../app/http.js").apiJson, API_BASE: string, sessionId: string }} deps
 */
export function createApiClient({ apiFetch, apiJson, API_BASE, sessionId }) {
  /**
   * @param {string} url
   * @param {JsonObject} body
   * @param {number} [timeoutMs]
   */
  function postJson(url, body, timeoutMs) {
    return apiJson(
      url,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      },
      timeoutMs,
    );
  }

  /**
   * @param {string} url
   * @param {JsonObject} body
   * @param {number} [timeoutMs]
   */
  async function postForSession(url, body, timeoutMs) {
    const res = await apiFetch(
      url,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      },
      timeoutMs,
    );
    return decodeEditSessionFrame(await res.arrayBuffer());
  }

  /** @param {Record<string, unknown>} params */
  function queryOf(params) {
    const query = new URLSearchParams();
    for (const [key, value] of Object.entries(params)) {
      if (value !== undefined && value !== null) query.set(key, String(value));
    }
    return query;
  }

  /**
   * GET a binary frame and decode it.
   *
   * @template T
   * @param {string} route
   * @param {Record<string, unknown>} params
   * @param {(buffer: ArrayBuffer) => T} decode
   * @param {number} [timeoutMs]
   * @returns {Promise<T>}
   */
  async function getFrame(route, params, decode, timeoutMs) {
    const res = await apiFetch(
      `${API_BASE}${route}?${queryOf(params)}`,
      {
        method: "GET",
        headers: { Accept: "application/octet-stream" },
      },
      timeoutMs,
    );
    return decode(await res.arrayBuffer());
  }

  return {
    /**
     * A viewport of the upload's EMG (one grid), grid overview or auxiliary
     * channels, as `Float32Array` views: min/max per bin, or the samples
     * when the window has no more samples than bins.
     *
     * @param {SeriesKind} kind
     * @param {{ upload_token: string, grid?: number, start?: number, end?: number, bins: number }} params
     * @returns {Promise<import("./binary-payloads.js").SeriesView>}
     */
    fetchSeries(kind, params) {
      return getFrame(SERIES_ROUTES[kind], params, decodeSeriesFrame);
    },

    /**
     * @param {JsonObject} payload
     */
    runAutoQc(payload) {
      return postJson(
        `${API_BASE}${routes.qcAuto}`,
        payload,
        AUTO_QC_TIMEOUT_MS,
      );
    },

    /**
     * @param {string} path
     */
    fetchPreviewByPath(path) {
      return postJson(`${API_BASE}${routes.previewByPath}`, { path });
    },

    /**
     * @param {FormData} formData
     * @param {number} [timeoutMs]
     */
    decomposeStream(formData, timeoutMs) {
      return apiFetch(
        `${API_BASE}${routes.decomposeStream}`,
        { method: "POST", headers: {}, body: formData },
        timeoutMs,
      );
    },

    /** Stops this tab's decomposition; its stream then ends with a `cancelled` event. */
    cancelDecomposition() {
      return postJson(`${API_BASE}${routes.decomposeCancel}`, {});
    },

    /**
     * Frees what the backend holds for this tab. `keepalive` lets the request
     * outlive the page being unloaded; unlike a beacon, it can carry the
     * session header.
     */
    closeSession() {
      const query = `session=${encodeURIComponent(sessionId)}`;
      apiFetch(`${API_BASE}${routes.sessionClose}?${query}`, {
        method: "POST",
        keepalive: true,
      }).catch(() => {});
    },

    /** @returns {Promise<{ path: string | null, name: string | null }>} */
    async openFileDialog() {
      const picked = await apiJson(`${API_BASE}${routes.dialogOpenFile}`);
      return {
        path: typeof picked.path === "string" ? picked.path : null,
        name: typeof picked.name === "string" ? picked.name : null,
      };
    },

    /**
     * One MU's pulse train over a window, as `Float32Array` views: min/max
     * per bin, or the samples when the window has no more samples than bins.
     * `token` names an edit session or a finished run.
     *
     * @param {{ token: string, mu: number, start?: number, end?: number, bins: number }} params
     * @returns {Promise<import("./binary-payloads.js").PulseView>}
     */
    fetchPulse(params) {
      return getFrame(routes.seriesPulse, params, decodePulseFrame);
    },

    /**
     * Open a decomposition in a server-side edit session: its fields and every
     * MU's discharge times. The pulse trains stay on the server.
     *
     * @param {string} filepath
     */
    editOpen(filepath) {
      return postForSession(`${API_BASE}${routes.editSessionOpen}`, {
        path: filepath,
      });
    },

    /**
     * The whole state of an edit session, once the edit it may be running is
     * done: for a page reloaded while the session was open, or one that gave
     * up waiting on an edit.
     *
     * @param {string} token
     */
    editSessionState(token) {
      return getFrame(
        routes.editSession,
        { token },
        decodeEditSessionFrame,
        GRID_FILTER_TIMEOUT_MS,
      );
    },

    /**
     * Replay (`apply`) or drop the unsaved edits left from an earlier session.
     *
     * @param {string} token
     * @param {boolean} apply
     */
    editRecover(token, apply) {
      return postForSession(`${API_BASE}${routes.editSessionRecover}`, {
        token,
        apply,
      });
    },

    /**
     * Filter a grid's EMG on the server before its first filter update; it
     * answers once the grid is filtered, which takes a while on long recordings.
     *
     * @param {string} token
     * @param {number} grid
     * @param {string} project
     */
    editPrepareGrid(token, grid, project) {
      return postJson(
        `${API_BASE}${routes.editSessionPrepareGrid}`,
        { token, grid, project },
        GRID_FILTER_TIMEOUT_MS,
      );
    },

    /**
     * Apply one edit on the server; the frame says what changed.
     *
     * @param {string} op
     * @param {JsonObject} payload `token` plus the operation's arguments
     */
    editOp(op, payload) {
      return postForSession(
        `${API_BASE}${routes.editOp(op)}`,
        payload,
        op === "update-filter" ? GRID_FILTER_TIMEOUT_MS : undefined,
      );
    },

    /**
     * Save an edit session; only the session form's fields travel.
     *
     * @param {JsonObject} payload
     */
    editSessionSave(payload) {
      return postJson(`${API_BASE}${routes.editSessionSave}`, payload);
    },

    /**
     * Save a finished run. Its pulse trains and discharge times stay on the
     * server under `run_result_token`.
     *
     * @param {JsonObject} payload
     */
    editSave(payload) {
      return postJson(`${API_BASE}${routes.editSave}`, payload);
    },

    healthUrl() {
      return `${API_BASE}${routes.health}`;
    },
  };
}
