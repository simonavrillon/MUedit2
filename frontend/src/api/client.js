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
} from "./binary-payloads.js";
import { normalizePreviewPayload } from "./payloads.js";

/** @typedef {import("../app/context.js").JsonObject} JsonObject */

/**
 * @param {{ apiFetch: typeof import("../app/http.js").apiFetch, apiJson: typeof import("../app/http.js").apiJson, API_BASE: string }} deps
 */
export function createApiClient({ apiFetch, apiJson, API_BASE }) {
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
