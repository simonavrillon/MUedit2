/**
 * The JSON values of a newline-delimited JSON stream, as each line arrives.
 * A line that is not JSON is handed to `onMalformed` and skipped.
 *
 * @param {ReadableStream<Uint8Array>} body
 * @param {(err: unknown) => void} onMalformed
 * @returns {AsyncGenerator<import("../app/context.js").JsonObject>}
 */
export async function* readNdjson(body, onMalformed) {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  /** @param {string} line */
  function parse(line) {
    if (!line.trim()) return null;
    try {
      return JSON.parse(line);
    } catch (err) {
      onMalformed(err);
      return null;
    }
  }
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop() || "";
    for (const line of lines) {
      const msg = parse(line);
      if (msg) yield msg;
    }
  }
  const last = parse(buffer + decoder.decode());
  if (last) yield last;
}
