// The run's progress stream: one JSON value per line, however it is chunked.
import { test } from "node:test";
import assert from "node:assert/strict";

const { readNdjson } = await import("../src/api/ndjson.js");

/** A stream that delivers `chunks` one by one. */
function streamOf(chunks) {
  const encoder = new TextEncoder();
  return new ReadableStream({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(encoder.encode(chunk));
      controller.close();
    },
  });
}

async function readAll(chunks) {
  const values = [];
  const malformed = [];
  for await (const value of readNdjson(streamOf(chunks), (err) =>
    malformed.push(err),
  )) {
    values.push(value);
  }
  return { values, malformed };
}

test("lines split across chunks are joined", async () => {
  const { values } = await readAll(['{"a":', '1}\n{"b"', ":2}\n"]);
  assert.deepEqual(values, [{ a: 1 }, { b: 2 }]);
});

test("a last line without a newline is read", async () => {
  const { values } = await readAll(['{"a":1}\n{"stage":"done"}']);
  assert.deepEqual(values, [{ a: 1 }, { stage: "done" }]);
});

test("a malformed line is reported and skipped; blank lines are not", async () => {
  const { values, malformed } = await readAll([
    '{"a":1}\n\nnot json\n{"b":2}\n',
  ]);
  assert.deepEqual(values, [{ a: 1 }, { b: 2 }]);
  assert.equal(malformed.length, 1);
});

test("a character split across chunks survives", async () => {
  const bytes = new TextEncoder().encode('{"s":"µV"}\n');
  const stream = new ReadableStream({
    start(controller) {
      controller.enqueue(bytes.slice(0, 7));
      controller.enqueue(bytes.slice(7));
      controller.close();
    },
  });
  const values = [];
  for await (const v of readNdjson(stream, () => {})) values.push(v);
  assert.deepEqual(values, [{ s: "µV" }]);
});
