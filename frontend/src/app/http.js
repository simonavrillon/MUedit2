/** @typedef {import("../app/context.js").JsonObject} JsonObject */

export const SESSION_HEADER = "X-MUedit-Session";

/** One id per tab: the backend scopes what it caches to it and frees it when the tab closes. */
export const SESSION_ID = newSessionId();

function newSessionId() {
  if (typeof globalThis.crypto?.randomUUID === "function") {
    return globalThis.crypto.randomUUID();
  }
  // randomUUID needs a secure context, which a plain-http LAN address is not.
  return Array.from({ length: 32 }, () =>
    Math.floor(Math.random() * 16).toString(16),
  ).join("");
}

/**
 * @param {Response} res
 * @returns {Promise<string>}
 */
async function parseApiError(res) {
  let message = `HTTP ${res.status}`;
  try {
    const data = await res.json();
    const err = data?.error || data;
    if (typeof err?.message === "string" && err.message.trim()) {
      message = err.message.trim();
    }

    const detail = err?.detail ?? data?.detail;
    if (typeof detail === "string" && detail.trim()) {
      message = `${message}: ${detail.trim()}`;
    } else if (Array.isArray(detail)) {
      // FastAPI/Pydantic validation errors are commonly lists with `loc` + `msg`.
      const first =
        detail.find((item) => item && typeof item === "object") || null;
      const msg = first?.msg || first?.message || "";
      const loc = Array.isArray(first?.loc) ? first.loc.join(".") : "";
      if (msg) {
        message = `${message}: ${loc ? `${loc} ` : ""}${msg}`.trim();
      }
    } else if (detail && typeof detail === "object" && !Array.isArray(detail)) {
      const reason = detail.reason || detail.message || "";
      const field = detail.field ? `${detail.field} ` : "";
      if (reason) message = `${message}: ${field}${reason}`.trim();
    }
  } catch {
    // Keep status fallback.
  }
  return message;
}

/**
 * @param {string} url
 * @param {RequestInit} [options]
 * @param {number} [timeoutMs]
 * @returns {Promise<Response>}
 */
export async function apiFetch(url, options = {}, timeoutMs = 120000) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), timeoutMs);
  const headers = new Headers(options.headers);
  headers.set(SESSION_HEADER, SESSION_ID);
  try {
    const res = await fetch(url, {
      ...options,
      headers,
      signal: controller.signal,
    });
    if (!res.ok) {
      throw new Error(await parseApiError(res));
    }
    return res;
  } catch (err) {
    if (err instanceof Error && err.name === "AbortError") {
      throw new Error("Request timed out");
    }
    throw err;
  } finally {
    clearTimeout(timeout);
  }
}

/**
 * @param {string} healthUrl
 * @param {{ intervalMs?: number, timeoutMs?: number }} [options]
 * @returns {Promise<boolean>}
 */
export async function waitForBackend(
  healthUrl,
  { intervalMs = 500, timeoutMs = 60000 } = {},
) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      const res = await fetch(healthUrl, { signal: AbortSignal.timeout(2000) });
      if (res.ok) return true;
    } catch {
      // not ready yet
    }
    await new Promise((r) => setTimeout(r, intervalMs));
  }
  return false;
}

/**
 * @param {string} url
 * @param {RequestInit} [options]
 * @param {number} [timeoutMs]
 * @returns {Promise<JsonObject>}
 */
export async function apiJson(url, options = {}, timeoutMs = 120000) {
  const res = await apiFetch(url, options, timeoutMs);
  const payload = await res.json();
  if (
    payload &&
    typeof payload === "object" &&
    "data" in payload &&
    payload.data !== undefined
  ) {
    return payload.data;
  }
  return payload;
}
