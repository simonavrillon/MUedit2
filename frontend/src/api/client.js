/**
 * API client seam. Wraps URL composition and response decoding so domain
 * modules (signal/, decomp/, editing/) never import routes or binary
 * decoders directly. Created once in the container and injected via deps.
 */
import { routes } from "./routes.js";
import {
  decodeSeriesFrame,
  decodeDecomposePreviewPayload,
  decodeEditSessionFrame,
  decodePulseFrame,
} from "./binary-payloads.js";
import { normalizePreviewPayload } from "./payloads.js";

/** @typedef {import("../app/context.js").JsonObject} JsonObject */
/** @typedef {"emg" | "overview" | "aux"} SeriesKind */

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
  async function postForSession(url, body, timeoutMs = 120000) {
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
    async fetchSeries(kind, params) {
      const res = await apiFetch(
        `${API_BASE}${SERIES_ROUTES[kind]}?${queryOf(params)}`,
        { method: "GET", headers: { Accept: "application/octet-stream" } },
        120000,
      );
      return decodeSeriesFrame(await res.arrayBuffer());
    },

    /**
     * @param {JsonObject} payload
     */
    runAutoQc(payload) {
      return postJson(`${API_BASE}${routes.qcAuto}`, payload, 300000);
    },

    /**
     * @param {string} path
     */
    fetchPreviewByPath(path) {
      return postJson(`${API_BASE}${routes.previewByPath}`, { path }, 120000);
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
     * @param {string} token
     */
    async fetchDecomposePreview(token) {
      const res = await apiFetch(
        `${API_BASE}${routes.decomposePreview(token)}`,
        { method: "GET", headers: { Accept: "application/octet-stream" } },
        120000,
      );
      const buf = await res.arrayBuffer();
      return normalizePreviewPayload(
        decodeDecomposePreviewPayload(buf, res.headers.get("x-muedit-format")),
      );
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
    async fetchPulse(params) {
      const res = await apiFetch(
        `${API_BASE}${routes.seriesPulse}?${queryOf(params)}`,
        { method: "GET", headers: { Accept: "application/octet-stream" } },
        120000,
      );
      return decodePulseFrame(await res.arrayBuffer());
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
     * The whole state of an edit session this page opened before a reload.
     *
     * @param {string} token
     */
    async editSessionState(token) {
      const query = queryOf({ token });
      const res = await apiFetch(
        `${API_BASE}${routes.editSession}?${query}`,
        { method: "GET", headers: { Accept: "application/octet-stream" } },
        120000,
      );
      return decodeEditSessionFrame(await res.arrayBuffer());
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
        600000,
      );
    },

    /**
     * Apply one edit on the server; the frame says what changed.
     *
     * @param {string} op
     * @param {JsonObject} payload `token` plus the operation's arguments
     */
    editOp(op, payload) {
      return postForSession(`${API_BASE}${routes.editOp(op)}`, payload);
    },

    /**
     * Save an edit session; only the session form's fields travel.
     *
     * @param {JsonObject} payload
     */
    editSessionSave(payload) {
      return postJson(`${API_BASE}${routes.editSessionSave}`, payload, 120000);
    },

    /**
     * Save a finished run. Its pulse trains and discharge times stay on the
     * server under `run_result_token`.
     *
     * @param {JsonObject} payload
     */
    editSave(payload) {
      return postJson(`${API_BASE}${routes.editSave}`, payload, 120000);
    },

    healthUrl() {
      return `${API_BASE}${routes.health}`;
    },
  };
}
