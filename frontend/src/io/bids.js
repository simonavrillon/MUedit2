/**
 * BIDS naming without DOM: entity labels and tokens, suggested file names,
 * and the muscle rows of the session form.
 */
import { inferGridCount, normalizeGridNames } from "./grid.js";

/** @typedef {import("../state/state.js").State} State */

/** @typedef {ReturnType<typeof buildBidsMuscleRowsModel>} BidsMuscleRows */

/** @typedef {ReturnType<typeof parseBidsEntitiesFromLabel>} BidsEntities */

/**
 * @param {string | null | undefined} value
 * @param {string} [fallback]
 * @returns {string}
 */
export function safeBidsToken(value, fallback = "") {
  const token = String(value || "")
    .trim()
    .replace(/[^A-Za-z0-9]+/g, "");
  return token || fallback;
}

/**
 * @param {Partial<BidsEntities>} [entities]
 */
export function buildEntityLabelFromSession({
  subject,
  task,
  session,
  run,
  acquisition,
} = {}) {
  // Emit entities in canonical BIDS order: sub, ses, task, acq, run.
  // run/acq are added only when provided so sequential recordings
  // keyed by acq-<label> are not stamped with a spurious run-01.
  const parts = [`sub-${safeBidsToken(subject, "01")}`];
  const ses = safeBidsToken(session);
  if (ses) parts.push(`ses-${ses}`);
  parts.push(`task-${safeBidsToken(task, "task")}`);
  const acq = safeBidsToken(acquisition);
  if (acq) parts.push(`acq-${acq}`);
  const r = safeBidsToken(run);
  if (r) parts.push(`run-${r}`);
  return parts.join("_");
}

/**
 * @param {string} baseName
 * @param {string} suffix
 * @returns {string}
 */
export function getSuggestedNpzName(baseName, suffix) {
  let stem = baseName.replace(/\.[^.]+$/, "");
  if (stem.endsWith(suffix)) stem = stem.slice(0, -suffix.length);
  return `${stem}${suffix}.npz`;
}

/**
 * The entity label of a BIDS recording's file name: its stem without `_emg`.
 *
 * @param {string} filename
 * @returns {string}
 */
export function entityLabelFromFilename(filename) {
  return filename.replace(/\.[^.]+$/, "").replace(/_emg$/i, "");
}

/**
 * @param {string | null | undefined} label
 */
export function parseBidsEntitiesFromLabel(label) {
  const text = String(label || "");
  const get = (/** @type {string} */ key) => {
    const m = text.match(new RegExp(`(?:^|_)${key}-([^_\\.]+)`));
    return m && m[1] ? m[1] : "";
  };
  return {
    subject: get("sub"),
    session: get("ses"),
    task: get("task"),
    acquisition: get("acq"),
    run: get("run"),
  };
}

/**
 * @param {State} state
 */
export function buildBidsMuscleRowsModel(state) {
  const inEditStage = state.currentStage === "edit";
  // An open edit session's grids are `state.gridNames`; the edit stage has none without one.
  const gridNames = normalizeGridNames(
    inEditStage && !state.edit.token ? [] : state.gridNames,
  );
  const muscleNames = state.muscleNames || [];
  const rowCount = inferGridCount({
    gridNames,
    muGridIndex: inEditStage ? state.edit.muGridIndex : [],
    muscleNames,
  });
  return Array.from({ length: rowCount }, (_, i) => ({
    id: `bidsMuscle_${i}`,
    label: `Muscle Grid ${i + 1}`,
    value: muscleNames[i] || "",
  }));
}

/**
 * @param {string | null | undefined} v
 * @returns {string}
 */
export function naToEmpty(v) {
  return !v || v === "n/a" ? "" : v;
}
