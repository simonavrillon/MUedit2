// MUB1 frames built the way the backend's `pack_frame` (api/binary.py) builds
// them, for tests to decode. Not a test file itself: `npm test` only runs
// tests/*.test.js.

const ALIGN = 8;
const TYPES = {
  f4: Float32Array,
  i4: Int32Array,
  i8: BigInt64Array,
  u1: Uint8Array,
  i2: Int16Array,
};

const aligned = (n) => n + ((ALIGN - (n % ALIGN)) % ALIGN);
const count = (shape) => shape.reduce((n, d) => n * d, 1);

/**
 * Encode `meta` and arrays into one MUB1 buffer. An array is given as flat
 * `data` or as `rows` of `shape[1]` values each; an `i8` array's values are
 * converted to BigInt.
 */
export function encodeFrame(meta, arrays) {
  const entries = Object.entries(arrays);
  const specs = [];
  let dataLen = 0;
  for (const [name, arr] of entries) {
    specs.push({ name, dtype: arr.dtype, shape: arr.shape, offset: dataLen });
    dataLen = aligned(
      dataLen + count(arr.shape) * TYPES[arr.dtype].BYTES_PER_ELEMENT,
    );
  }
  const header = new TextEncoder().encode(
    JSON.stringify({ meta, arrays: specs }),
  );
  const dataStart = aligned(8 + header.byteLength);
  const buffer = new ArrayBuffer(dataStart + dataLen);
  const bytes = new Uint8Array(buffer);
  bytes.set(new TextEncoder().encode("MUB1"), 0);
  new DataView(buffer).setUint32(4, header.byteLength, true);
  bytes.set(header, 8);
  specs.forEach((spec, i) => {
    const arr = entries[i][1];
    const view = new TYPES[arr.dtype](
      buffer,
      dataStart + spec.offset,
      count(arr.shape),
    );
    if (view instanceof BigInt64Array) {
      const flat = arr.rows
        ? arr.rows.flatMap((row) => Array.from(row))
        : arr.data;
      view.set(Array.from(flat ?? [], (v) => BigInt(v)));
    } else if (arr.rows) {
      const cols = arr.shape[1] ?? 0;
      arr.rows.forEach((row, r) => view.set(row, r * cols));
    } else if (arr.data) {
      view.set(arr.data);
    }
  });
  return buffer;
}
