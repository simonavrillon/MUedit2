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

export function createApiClient({ apiFetch, apiJson, API_BASE }) {
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

    runAutoQc(payload) {
      return postJson(`${API_BASE}${routes.qcAuto}`, payload, 300000);
    },

    fetchPreviewByPath(path) {
      return postJson(`${API_BASE}${routes.previewByPath}`, { path }, 120000);
    },

    decomposeStream(formData, timeoutMs) {
      return apiFetch(
        `${API_BASE}${routes.decomposeStream}`,
        { method: "POST", headers: {}, body: formData },
        timeoutMs,
      );
    },

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

    editAction(action, payload) {
      return postJson(`${API_BASE}${routes.editAction(action)}`, payload);
    },

    editMode(mode, payload) {
      return postJson(`${API_BASE}${routes.editMode(mode)}`, payload, 120000);
    },

    editRemoveOutliers(payload) {
      return postJson(`${API_BASE}${routes.editRemoveOutliers}`, payload);
    },

    editRemoveDuplicates(payload) {
      return postJson(`${API_BASE}${routes.editRemoveDuplicates}`, payload);
    },

    editFlagMu(payload) {
      return postJson(`${API_BASE}${routes.editFlagMu}`, payload);
    },

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

    editSave(payload) {
      return postJson(`${API_BASE}${routes.editSave}`, payload, 120000);
    },

    healthUrl() {
      return `${API_BASE}${routes.health}`;
    },
  };
}
