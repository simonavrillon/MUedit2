export async function parseApiError(res) {
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

export async function apiFetch(url, options = {}, timeoutMs = 120000) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const res = await fetch(url, { ...options, signal: controller.signal });
    if (!res.ok) {
      throw new Error(await parseApiError(res));
    }
    return res;
  } catch (err) {
    if (err?.name === "AbortError") {
      throw new Error("Request timed out");
    }
    throw err;
  } finally {
    clearTimeout(timeout);
  }
}

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
