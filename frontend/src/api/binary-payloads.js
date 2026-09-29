/**
 * Codecs for the binary transport the API uses for arrays (viewport series, discharge times).
 *
 * MUB1, mirroring `pack_frame` / `unpack_frame` in `api/binary.py`:
 *
 *   "MUB1" | uint32 headerLen | header JSON {meta, arrays} | pad to 8 |
 *   array data, each array at an 8-byte-aligned offset from the data start
 *
 * Little-endian. The alignment lets every array be read as a typed-array view
 * without copying. A payload without the magic is the server's JSON fallback.
 */
/** @typedef {import("../app/context.js").JsonObject} JsonObject */

const textDecoder = new TextDecoder();

/**
 * @param {ArrayBuffer} buffer
 * @param {string} magic
 */
function hasMagic(buffer, magic) {
  if (!buffer || buffer.byteLength < magic.length) return false;
  const sig = new Uint8Array(buffer, 0, magic.length);
  for (let i = 0; i < magic.length; i++) {
    if (sig[i] !== magic.charCodeAt(i)) return false;
  }
  return true;
}

export const FRAME_MEDIA_TYPE = "application/x-muedit-frame";
const FRAME_MAGIC = "MUB1";
const FRAME_FORMAT = "mub1";
const FRAME_ALIGN = 8;

const FRAME_TYPES = {
  f4: Float32Array,
  i4: Int32Array,
  i8: BigInt64Array,
  u1: Uint8Array,
  i2: Int16Array,
};

/**
 * @typedef {keyof typeof FRAME_TYPES} FrameDtype
 * @typedef {Float32Array | Int32Array | BigInt64Array | Uint8Array | Int16Array} FrameData
 * @typedef {{ dtype: FrameDtype, shape: number[], data: FrameData }} FrameArray
 * @typedef {{ dtype: FrameDtype, shape: number[], rows?: ArrayLike<number>[], data?: ArrayLike<number> }} FrameArrayInput An `i8` array's values are converted to BigInt.
 */

/** @param {number} n */
function frameAligned(n) {
  return n + ((FRAME_ALIGN - (n % FRAME_ALIGN)) % FRAME_ALIGN);
}

/** @param {number[]} shape */
function frameCount(shape) {
  return shape.reduce((n, d) => n * d, 1);
}

/**
 * @param {ArrayBuffer} buffer
 * @param {string | null} [formatHeader]
 */
function isFrame(buffer, formatHeader = "") {
  return formatHeader === FRAME_FORMAT || hasMagic(buffer, FRAME_MAGIC);
}

/**
 * Decode a MUB1 frame; arrays are views into `buffer`, not copies.
 *
 * @param {ArrayBuffer} buffer
 * @returns {{ meta: JsonObject, arrays: Record<string, FrameArray> }}
 */
export function decodeFrame(buffer) {
  if (!hasMagic(buffer, FRAME_MAGIC) || buffer.byteLength < 8) {
    throw new Error("Invalid MUB1 frame");
  }
  const headerLen = new DataView(buffer).getUint32(4, true);
  if (8 + headerLen > buffer.byteLength) {
    throw new Error("Invalid MUB1 frame: header runs past the end");
  }
  const header = JSON.parse(
    textDecoder.decode(new Uint8Array(buffer, 8, headerLen)),
  );
  const dataStart = frameAligned(8 + headerLen);
  /** @type {Record<string, FrameArray>} */
  const arrays = {};
  for (const spec of header.arrays || []) {
    const Typed = FRAME_TYPES[/** @type {FrameDtype} */ (spec.dtype)];
    if (!Typed) throw new Error(`Unsupported MUB1 dtype: ${spec.dtype}`);
    const count = frameCount(spec.shape);
    const offset = dataStart + spec.offset;
    if (offset + count * Typed.BYTES_PER_ELEMENT > buffer.byteLength) {
      throw new Error(`MUB1 array ${spec.name} runs past the end`);
    }
    arrays[spec.name] = {
      dtype: spec.dtype,
      shape: spec.shape,
      data: new Typed(buffer, offset, count),
    };
  }
  return { meta: header.meta || {}, arrays };
}

/**
 * Encode `meta` and arrays into one MUB1 buffer, writing each array in place.
 * An array is given as flat `data` or as `rows` of `shape[1]` values each.
 *
 * @param {JsonObject} meta
 * @param {Record<string, FrameArrayInput>} arrays
 * @returns {ArrayBuffer}
 */
export function encodeFrame(meta, arrays) {
  const entries = Object.entries(arrays);
  const specs = [];
  let dataLen = 0;
  for (const [name, arr] of entries) {
    specs.push({ name, dtype: arr.dtype, shape: arr.shape, offset: dataLen });
    dataLen = frameAligned(
      dataLen +
        frameCount(arr.shape) * FRAME_TYPES[arr.dtype].BYTES_PER_ELEMENT,
    );
  }
  const header = new TextEncoder().encode(
    JSON.stringify({ meta, arrays: specs }),
  );
  const dataStart = frameAligned(8 + header.byteLength);
  const buffer = new ArrayBuffer(dataStart + dataLen);
  const bytes = new Uint8Array(buffer);
  for (let i = 0; i < 4; i++) bytes[i] = FRAME_MAGIC.charCodeAt(i);
  new DataView(buffer).setUint32(4, header.byteLength, true);
  bytes.set(header, 8);
  specs.forEach((spec, i) => {
    const arr = entries[i][1];
    const Typed = FRAME_TYPES[arr.dtype];
    const view = new Typed(
      buffer,
      dataStart + spec.offset,
      frameCount(arr.shape),
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

/**
 * Rows of a CSR pair as views into the frame: row `i` is
 * `values[offsets[i]:offsets[i + 1]]`. Each MU's discharge times arrive this way.
 *
 * @param {FrameArray | undefined} values
 * @param {FrameArray | undefined} offsets
 * @returns {Int32Array[]}
 */
export function csrRows(values, offsets) {
  if (!values || !offsets) return [];
  const data = /** @type {Int32Array} */ (values.data);
  const ends = /** @type {BigInt64Array} */ (offsets.data);
  const rows = [];
  for (let i = 0; i + 1 < ends.length; i++) {
    rows.push(data.subarray(Number(ends[i]), Number(ends[i + 1])));
  }
  return rows;
}

/**
 * A run's preview: its JSON fields, with `distime_all` rebuilt from the CSR
 * discharge times. The pulse trains stay on the server (`/series/pulse`).
 *
 * @param {ArrayBuffer} buffer
 * @param {string | null} [formatHeader]
 * @returns {JsonObject}
 */
export function decodeDecomposePreviewPayload(buffer, formatHeader = "") {
  if (!isFrame(buffer, formatHeader)) {
    return JSON.parse(textDecoder.decode(new Uint8Array(buffer)));
  }
  const { meta, arrays } = decodeFrame(buffer);
  return {
    ...meta,
    distime_all: csrRows(arrays.spikes, arrays.spike_offsets),
  };
}

/**
 * @typedef {object} EditSessionFrame What the edit session sends: its fields, and per-MU times.
 * @property {JsonObject} meta
 * @property {Int32Array[]} spikes Discharge times of every MU (open) or of the changed ones (an edit).
 * @property {Int32Array[]} artifacts Artifact times, likewise.
 */

/**
 * @param {ArrayBuffer} buffer
 * @returns {EditSessionFrame}
 */
export function decodeEditSessionFrame(buffer) {
  const { meta, arrays } = decodeFrame(buffer);
  return {
    meta,
    spikes: csrRows(arrays.spikes, arrays.spike_offsets),
    artifacts: csrRows(arrays.artifacts, arrays.artifact_offsets),
  };
}

/**
 * @typedef {{ min: Float32Array, max: Float32Array }} Envelope
 * @typedef {Envelope | Float32Array} SeriesRow One row of a viewport: min/max per bin, or the samples.
 * @typedef {{ meta: JsonObject, rows: SeriesRow[] }} SeriesView
 */

/**
 * @param {FrameArray | undefined} arr
 * @returns {Float32Array[]}
 */
function frameRowViews(arr) {
  if (!arr || arr.shape.length !== 2) return [];
  const [n, cols] = arr.shape;
  const data = /** @type {Float32Array} */ (arr.data);
  return Array.from({ length: n }, (_, r) =>
    data.subarray(r * cols, (r + 1) * cols),
  );
}

/**
 * Decode a `/series/*` frame into one row per channel, as views into `buffer`.
 *
 * @param {ArrayBuffer} buffer
 * @returns {SeriesView}
 */
export function decodeSeriesFrame(buffer) {
  const { meta, arrays } = decodeFrame(buffer);
  if (meta.kind === "samples") {
    return { meta, rows: frameRowViews(arrays.samples) };
  }
  const maxs = frameRowViews(arrays.max);
  return {
    meta,
    rows: frameRowViews(arrays.min).map((min, r) => ({ min, max: maxs[r] })),
  };
}

/**
 * One MU's pulse train over `[start, end)` as drawn, with the discharges and
 * artifacts in that window and the train's value at each.
 *
 * @typedef {object} PulseView
 * @property {number} mu
 * @property {number} start
 * @property {number} end
 * @property {number} bins
 * @property {number} version Changes whenever the MU is edited.
 * @property {boolean} flagged
 * @property {SeriesRow} row
 * @property {Int32Array} spikes
 * @property {Float32Array} spikeValues
 * @property {Int32Array} artifacts
 * @property {Float32Array} artifactValues
 */

/**
 * Decode a `/series/pulse` frame; every array is a view into `buffer`.
 *
 * @param {ArrayBuffer} buffer
 * @returns {PulseView}
 */
export function decodePulseFrame(buffer) {
  const { meta, arrays } = decodeFrame(buffer);
  const row =
    meta.kind === "samples"
      ? frameRowViews(arrays.samples)[0]
      : {
          min: frameRowViews(arrays.min)[0],
          max: frameRowViews(arrays.max)[0],
        };
  const ints = (/** @type {string} */ name) =>
    /** @type {Int32Array} */ (arrays[name]?.data ?? new Int32Array(0));
  const floats = (/** @type {string} */ name) =>
    /** @type {Float32Array} */ (arrays[name]?.data ?? new Float32Array(0));
  return {
    mu: Number(meta.mu),
    start: Number(meta.start),
    end: Number(meta.end),
    bins: Number(meta.bins),
    version: Number(meta.version),
    flagged: !!meta.flagged,
    row,
    spikes: ints("spikes"),
    spikeValues: floats("spike_values"),
    artifacts: ints("artifacts"),
    artifactValues: floats("artifact_values"),
  };
}
