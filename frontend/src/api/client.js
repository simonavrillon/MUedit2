/**
 * API client seam. Wraps URL composition and response decoding so domain
 * modules (signal/, decomp/, editing/) never import routes or binary
 * decoders directly. Created once in the container and injected via deps.
 */
import { routes } from "./routes.js";
import {
  isQcRawF32Payload,
  decodeQcJsonPayload,
  decodeQcRawF32,
  decodeDecomposePreviewPayload,
  decodeEditLoadPayload,
  encodeFrame,
  FRAME_MEDIA_TYPE,
} from "./binary-payloads.js";
import { normalizePreviewPayload } from "./payloads.js";

/** @typedef {import("../app/context.js").JsonObject} JsonObject */

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

  return {
    /**
     * @param {JsonObject} payload
     * @param {{ preferBinary?: boolean }} [options]
     */
    async fetchQcWindow(payload, { preferBinary = true } = {}) {
      if (preferBinary) {
        const res = await apiFetch(
          `${API_BASE}${routes.qcWindow}`,
          {
            method: "POST",
            headers: {
              "Content-Type": "application/json",
              Accept: "application/octet-stream",
            },
            body: JSON.stringify(payload),
          },
          120000,
        );
        const buf = await res.arrayBuffer();
        if (isQcRawF32Payload(buf, res.headers.get("x-muedit-format"))) {
          return decodeQcRawF32(buf);
        }
        return decodeQcJsonPayload(buf);
      }
      return postJson(`${API_BASE}${routes.qcWindow}`, payload, 120000);
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
     * Frees what the backend holds for this tab. A beacon, because a normal
     * request may not get out while the page is being unloaded.
     */
    closeSession() {
      const query = `session=${encodeURIComponent(sessionId)}`;
      return navigator.sendBeacon(`${API_BASE}${routes.sessionClose}?${query}`);
    },

    openFileDialog() {
      return apiJson(`${API_BASE}${routes.dialogOpenFile}`);
    },

    /**
     * @param {string} action
     * @param {JsonObject} payload
     */
    editAction(action, payload) {
      return postJson(`${API_BASE}${routes.editAction(action)}`, payload);
    },

    /**
     * @param {string} mode
     * @param {JsonObject} payload
     */
    editMode(mode, payload) {
      return postJson(`${API_BASE}${routes.editMode(mode)}`, payload, 120000);
    },

    /**
     * @param {JsonObject} payload
     */
    editRemoveOutliers(payload) {
      return postJson(`${API_BASE}${routes.editRemoveOutliers}`, payload);
    },

    /**
     * @param {JsonObject} payload
     */
    editRemoveDuplicates(payload) {
      return postJson(`${API_BASE}${routes.editRemoveDuplicates}`, payload);
    },

    /**
     * @param {JsonObject} payload
     */
    editFlagMu(payload) {
      return postJson(`${API_BASE}${routes.editFlagMu}`, payload);
    },

    /**
     * @param {string} filepath
     */
    async editLoadByPath(filepath) {
      const res = await apiFetch(
        `${API_BASE}${routes.editLoadByPath}`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ path: filepath }),
        },
        120000,
      );
      return decodeEditLoadPayload(
        await res.arrayBuffer(),
        res.headers.get("x-muedit-format"),
      );
    },

    /**
     * Pulse trains go as a float32 MUB1 array, never as JSON: at 20 min the
     * JSON text would exceed V8's maximum string length.
     *
     * @param {JsonObject} payload
     * @param {ArrayLike<number>[]} [pulseTrains] one row of `total_samples` values per MU
     */
    editSave(payload, pulseTrains) {
      const url = `${API_BASE}${routes.editSave}`;
      const cols = Number(payload.total_samples) || 0;
      if (
        !pulseTrains?.length ||
        pulseTrains.some((row) => row?.length !== cols)
      ) {
        return postJson(url, payload, 120000);
      }
      const body = encodeFrame(payload, {
        pulse_trains: {
          dtype: "f4",
          shape: [pulseTrains.length, cols],
          rows: pulseTrains,
        },
      });
      return apiJson(
        url,
        { method: "POST", headers: { "Content-Type": FRAME_MEDIA_TYPE }, body },
        120000,
      );
    },

    healthUrl() {
      return `${API_BASE}${routes.health}`;
    },
  };
}
