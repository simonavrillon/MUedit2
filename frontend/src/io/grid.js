function safeNonNegativeInt(value) {
  const n = Number(value);
  return Number.isFinite(n) && n >= 0 ? Math.floor(n) : null;
}

export function inferGridCount({
  gridNames = [],
  muGridIndex = [],
  muscles = [],
  minimum = 1,
} = {}) {
  let count = Math.max(1, Number(minimum) || 1);
  if (Array.isArray(gridNames)) count = Math.max(count, gridNames.length);
  if (Array.isArray(muscles)) count = Math.max(count, muscles.length);
  if (Array.isArray(muGridIndex) && muGridIndex.length) {
    const maxIdx = Math.max(
      ...muGridIndex.map(safeNonNegativeInt).filter((v) => v !== null),
      0,
    );
    count = Math.max(count, maxIdx + 1);
  }
  return count;
}

export function normalizeGridNames(gridNames, { minimumCount = 1 } = {}) {
  const source = Array.isArray(gridNames) ? gridNames : [];
  const count = Math.max(1, Number(minimumCount) || 1, source.length);
  return Array.from({ length: count }, (_, i) => {
    const raw = String(source[i] || "").trim();
    return raw || `Grid ${i + 1}`;
  });
}
