/**
 * Codecs for the binary transport the API uses for arrays (pulse trains).
 *
 * MUB1, mirroring `pack_frame` / `unpack_frame` in `api/binary.py`:
 *
 *   "MUB1" | uint32 headerLen | header JSON {meta, arrays} | pad to 8 |
 *   array data, each array at an 8-byte-aligned offset from the data start
 *
 * Little-endian. The alignment lets every array be read as a typed-array view
 * without copying. A payload without the magic is the server's JSON fallback.
 * The QC window still uses its own MQCR layout.
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

/**
 * @param {Float32Array} raw
 * @param {number} rows
 * @param {number} cols
 * @returns {number[][]}
 */
function to2d(raw, rows, cols) {
  const out = [];
  for (let r = 0; r < rows; r++) {
    const row = new Array(cols);
    const base = r * cols;
    for (let c = 0; c < cols; c++) row[c] = raw[base + c];
    out.push(row);
  }
  return out;
}

/**
 * @param {ArrayBuffer} buffer
 * @param {string | null} [formatHeader]
 */
export function isQcRawF32Payload(buffer, formatHeader = "") {
  return formatHeader === "qc-raw-f32-v1" || hasMagic(buffer, "MQCR");
}

/**
 * @param {ArrayBuffer} buffer
 * @returns {JsonObject}
 */
export function decodeQcJsonPayload(buffer) {
  return JSON.parse(textDecoder.decode(new Uint8Array(buffer)));
}

/**
 * @param {ArrayBuffer} buffer
 */
export function decodeQcRawF32(buffer) {
  // Wire format:
  // 4 bytes magic "MQCR" + uint32 version + fixed metadata fields + repeated channel blocks.
  // Each channel block is: int32 channel_index, uint32 n, float32[n] samples.
  const view = new DataView(buffer);
  if (!hasMagic(buffer, "MQCR")) {
    throw new Error("Invalid QC raw payload");
  }
  let offset = 4;
  const version = view.getUint32(offset, true);
  offset += 4;
  if (version !== 1) {
    throw new Error(`Unsupported QC raw payload version: ${version}`);
  }
  const grid_index = view.getInt32(offset, true);
  offset += 4;
  const channel_index = view.getInt32(offset, true);
  offset += 4;
  const start = view.getInt32(offset, true);
  offset += 4;
  const end = view.getInt32(offset, true);
  offset += 4;
  const total_samples = view.getInt32(offset, true);
  offset += 4;
  const fsamp = view.getFloat32(offset, true);
  offset += 4;
  const nChannels = view.getUint32(offset, true);
  offset += 4;

  const channels = [];
  for (let i = 0; i < nChannels; i++) {
    const chIdx = view.getInt32(offset, true);
    offset += 4;
    const n = view.getUint32(offset, true);
    offset += 4;
    const series = new Float32Array(buffer, offset, n);
    offset += n * 4;
    channels.push({ channel_index: chIdx, series: Array.from(series) });
  }
  return {
    grid_index,
    channel_index,
    start,
    end,
    total_samples,
    fsamp,
    channels,
  };
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
 * @typedef {{ dtype: "f4" | "i4" | "u1" | "i2", shape: number[], rows?: ArrayLike<number>[], data?: ArrayLike<number> }} FrameArrayInput
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
    if (arr.rows) {
      const cols = arr.shape[1] ?? 0;
      arr.rows.forEach((row, r) => view.set(row, r * cols));
    } else if (arr.data) {
      view.set(arr.data);
    }
  });
  return buffer;
}

/**
 * @param {FrameArray | undefined} arr
 * @returns {number[][]}
 */
function frameRows(arr) {
  if (!arr || arr.shape.length !== 2) return [];
  return to2d(
    /** @type {Float32Array} */ (arr.data),
    arr.shape[0],
    arr.shape[1],
  );
}

/**
 * @param {ArrayBuffer} buffer
 * @param {string | null} [formatHeader]
 * @returns {JsonObject}
 */
export function decodeEditLoadPayload(buffer, formatHeader = "") {
  if (!isFrame(buffer, formatHeader)) {
    return JSON.parse(textDecoder.decode(new Uint8Array(buffer)));
  }
  const { meta, arrays } = decodeFrame(buffer);
  return { ...meta, pulse_trains_full: frameRows(arrays.pulse_trains_full) };
}

/**
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
    pulse_trains_full: frameRows(arrays.pulse_trains_full),
    pulse_trains_all: frameRows(arrays.pulse_trains_all),
  };
}
