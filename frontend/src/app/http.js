/** @typedef {import("../app/context.js").JsonObject} JsonObject */

export const SESSION_HEADER = "X-MUedit-Session";

/** How long a request may take before it is given up, unless it says otherwise. */
const REQUEST_TIMEOUT_MS = 120000;

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
 * An error response: its message, and what the backend's error envelope says
 * went wrong (`field` names the request field at fault, when one is).
 */
export class ApiError extends Error {
  /**
   * @param {string} message
   * @param {{ status: number, code?: string, field?: string }} info
   */
  constructor(message, { status, code = "", field = "" }) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.field = field;
  }
}

/** A request given up on: the server may still carry it out. */
export class RequestTimeout extends Error {
  constructor() {
    super("Request timed out");
    this.name = "RequestTimeout";
  }
}

/**
 * @param {Response} res
 * @returns {Promise<ApiError>}
 */
async function parseApiError(res) {
  let message = `HTTP ${res.status}`;
  let code = "";
  let field = "";
  try {
    const data = await res.json();
    const err = data?.error || data;
    if (typeof err?.message === "string" && err.message.trim()) {
      message = err.message.trim();
    }
    if (typeof err?.code === "string") code = err.code;

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
      if (typeof detail.field === "string") field = detail.field;
      const fieldText = field ? `${field} ` : "";
      if (reason) message = `${message}: ${fieldText}${reason}`.trim();
    }
  } catch {
    // Keep status fallback.
  }
  return new ApiError(message, { status: res.status, code, field });
}

/**
 * @param {string} url
 * @param {RequestInit} [options]
 * @param {number} [timeoutMs]
 * @returns {Promise<Response>}
 */
export async function apiFetch(
  url,
  options = {},
  timeoutMs = REQUEST_TIMEOUT_MS,
) {
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
      throw await parseApiError(res);
    }
    return res;
  } catch (err) {
    if (err instanceof Error && err.name === "AbortError") {
      throw new RequestTimeout();
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
export async function apiJson(
  url,
  options = {},
  timeoutMs = REQUEST_TIMEOUT_MS,
) {
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
