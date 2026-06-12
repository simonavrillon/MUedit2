/**
 * Decoders for the compact binary transport the API uses for large float32
 * matrices (pulse trains). Each payload is either plain JSON or a binary frame:
 *
 *   magic(4 bytes) | uint32 version | uint32 metaLen | (uint32 rows, uint32 cols)
 *   per array | JSON metadata (metaLen bytes) | float32 array data
 *
 * All integers/floats are little-endian. When the magic prefix is absent the
 * server fell back to JSON, so the bytes are parsed as text instead. Mirrors the
 * Python `_pack_json_f32_payload` packer in `api/common.py`.
 */
const textDecoder = new TextDecoder();

function hasMagic(buffer, magic) {
  if (!buffer || buffer.byteLength < magic.length) return false;
  const sig = new Uint8Array(buffer, 0, magic.length);
  for (let i = 0; i < magic.length; i++) {
    if (sig[i] !== magic.charCodeAt(i)) return false;
  }
  return true;
}

function readFloat32Values(view, offset, count) {
  const out = new Float32Array(count);
  let cursor = offset;
  for (let i = 0; i < count; i++) {
    out[i] = view.getFloat32(cursor, true);
    cursor += 4;
  }
  return out;
}

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

export function isEditLoadF32Payload(buffer, formatHeader = "") {
  return formatHeader === "edit-load-f32-v1" || hasMagic(buffer, "MELD");
}

export function decodeEditLoadPayload(buffer, formatHeader = "") {
  if (!isEditLoadF32Payload(buffer, formatHeader)) {
    const text = textDecoder.decode(new Uint8Array(buffer));
    return JSON.parse(text);
  }
  const view = new DataView(buffer);
  if (!hasMagic(buffer, "MELD"))
    throw new Error("Invalid edit-load binary payload");
  let offset = 4;
  const version = view.getUint32(offset, true);
  offset += 4;
  if (version !== 1)
    throw new Error(`Unsupported edit-load payload version: ${version}`);
  const metaLen = view.getUint32(offset, true);
  offset += 4;
  const rows = view.getUint32(offset, true);
  offset += 4;
  const cols = view.getUint32(offset, true);
  offset += 4;
  const meta = JSON.parse(
    textDecoder.decode(new Uint8Array(buffer, offset, metaLen)),
  );
  offset += metaLen;
  const pulse = to2d(readFloat32Values(view, offset, rows * cols), rows, cols);
  return { ...meta, pulse_trains_full: pulse };
}

export function isDecomposePreviewF32Payload(buffer, formatHeader = "") {
  return (
    formatHeader === "decompose-preview-f32-v1" || hasMagic(buffer, "MDPV")
  );
}

export function decodeDecomposePreviewPayload(buffer, formatHeader = "") {
  if (!isDecomposePreviewF32Payload(buffer, formatHeader)) {
    const text = textDecoder.decode(new Uint8Array(buffer));
    return JSON.parse(text);
  }
  const view = new DataView(buffer);
  if (!hasMagic(buffer, "MDPV"))
    throw new Error("Invalid decompose-preview payload");
  let offset = 4;
  const version = view.getUint32(offset, true);
  offset += 4;
  if (version !== 1)
    throw new Error(
      `Unsupported decompose-preview payload version: ${version}`,
    );
  const metaLen = view.getUint32(offset, true);
  offset += 4;
  const rowsFull = view.getUint32(offset, true);
  offset += 4;
  const colsFull = view.getUint32(offset, true);
  offset += 4;
  const rowsAll = view.getUint32(offset, true);
  offset += 4;
  const colsAll = view.getUint32(offset, true);
  offset += 4;
  const meta = JSON.parse(
    textDecoder.decode(new Uint8Array(buffer, offset, metaLen)),
  );
  offset += metaLen;
  const full = readFloat32Values(view, offset, rowsFull * colsFull);
  offset += rowsFull * colsFull * 4;
  const all = readFloat32Values(view, offset, rowsAll * colsAll);
  return {
    ...meta,
    pulse_trains_full: to2d(full, rowsFull, colsFull),
    pulse_trains_all: to2d(all, rowsAll, colsAll),
  };
}
