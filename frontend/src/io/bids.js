import { inferGridCount, normalizeGridNames } from "./grid.js";

export function safeBidsToken(value, fallback = "") {
  const token = String(value || "")
    .trim()
    .replace(/[^A-Za-z0-9]+/g, "");
  return token || fallback;
}

export function buildEntityLabelFromSession({
  subject,
  task,
  session,
  run,
  acq,
  recording,
} = {}) {
  // Emit entities in canonical BIDS order: sub, ses, task, acq, run, recording.
  // run/acq/recording are added only when provided so sequential recordings
  // keyed by acq-<label> are not stamped with a spurious run-01.
  const parts = [`sub-${safeBidsToken(subject, "01")}`];
  const ses = safeBidsToken(session, "");
  if (ses) parts.push(`ses-${ses}`);
  parts.push(`task-${safeBidsToken(task, "task")}`);
  const a = safeBidsToken(acq, "");
  if (a) parts.push(`acq-${a}`);
  const r = safeBidsToken(run, "");
  if (r) parts.push(`run-${r}`);
  const rec = safeBidsToken(recording, "");
  if (rec) parts.push(`recording-${rec}`);
  return parts.join("_");
}

export function getSuggestedNpzName(baseName, suffix = "_edited") {
  let stem = String(baseName || "decomposition").replace(/\.[^.]+$/, "");
  if (suffix && stem.endsWith(suffix)) stem = stem.slice(0, -suffix.length);
  return `${stem}${suffix}.npz`;
}

export function parseBidsEntitiesFromLabel(label) {
  const text = String(label || "");
  const get = (key) => {
    const m = text.match(new RegExp(`(?:^|_)${key}-([^_\\.]+)`));
    return m && m[1] ? m[1] : "";
  };
  return {
    subject: get("sub"),
    session: get("ses"),
    task: get("task"),
    acq: get("acq"),
    run: get("run"),
    recording: get("recording"),
  };
}

export function listifyMuscles(value) {
  if (Array.isArray(value))
    return value.map((v) => String(v || "").trim()).filter(Boolean);
  if (typeof value === "string" && value.trim()) return [value.trim()];
  return [];
}

export function buildBidsAutoInfoModel(state) {
  const meta = state.metadata || {};
  const muscles = Array.isArray(state.muscle) ? state.muscle : [];
  const hasMeta =
    !!meta.hardware_filters ||
    !!meta.gains ||
    muscles.length > 0 ||
    !!meta.device_name;
  if (!hasMeta) {
    return { hidden: true };
  }

  const uniqueMuscles = [
    ...new Set(muscles.filter((m) => m && typeof m === "string" && m.trim())),
  ];
  const hpf = Array.isArray(meta.emg_hpf) ? meta.emg_hpf[0] : meta.emg_hpf;
  const lpf = Array.isArray(meta.emg_lpf) ? meta.emg_lpf[0] : meta.emg_lpf;
  const gain = Array.isArray(meta.gains) ? meta.gains[0] : meta.gains;

  return {
    hidden: false,
    manufacturer: meta.manufacturer || "",
    deviceName: meta.device_name || "",
    musclesText: uniqueMuscles.length ? uniqueMuscles.join(", ") : "",
    filtersText: hpf || lpf ? `${hpf || "n/a"} - ${lpf || "n/a"} Hz` : "",
    gainText: gain ? String(gain) : "",
  };
}

export function buildBidsMuscleRowsModel(state) {
  const inEditStage = state.currentStage === "edit";
  const editGridNames =
    Array.isArray(state.edit?.gridNames) && state.edit.gridNames.length
      ? state.edit.gridNames
      : [];
  const runGridNames =
    Array.isArray(state.gridNames) && state.gridNames.length
      ? state.gridNames
      : [];
  const gridNames = inEditStage
    ? editGridNames.length
      ? editGridNames
      : ["Grid 1"]
    : editGridNames.length
      ? editGridNames
      : runGridNames.length
        ? runGridNames
        : ["Grid 1"];
  const muscles = Array.isArray(state.muscle) ? state.muscle : [];
  const rowCount = inferGridCount({
    gridNames,
    muGridIndex: inEditStage ? state.edit?.muGridIndex : [],
    muscles,
  });
  const normalizedGridNames = normalizeGridNames(gridNames, {
    minimumCount: rowCount,
  });
  return Array.from({ length: rowCount }, (_, i) => ({
    id: `bidsMuscle_${i}`,
    label: `Muscle Grid ${i + 1}${normalizedGridNames[i] ? ` (${normalizedGridNames[i]})` : ""}`,
    value: muscles[i] || "",
  }));
}

export function buildSessionInfoFromDecomposition(file, data, deps) {
  const { parseBidsEntitiesFromLabel, listifyMuscles } = deps;
  const fileLabel = file?.name || data?.file_label || "decomposition";
  const fs = Number(data?.fsamp);
  const entities = parseBidsEntitiesFromLabel(fileLabel);

  const fromParams = data?.parameters?.target_muscle;
  const fromPayload = data?.muscle;
  const muscles = listifyMuscles(fromPayload).length
    ? listifyMuscles(fromPayload)
    : listifyMuscles(fromParams);

  const participant = data?.participant_meta || {};
  const naToEmpty = (v) => (!v || v === "n/a" ? "" : v);

  return {
    fileLabel,
    fsampText: Number.isFinite(fs) && fs > 0 ? String(Math.round(fs)) : "",
    entities,
    muscles,
    participant: {
      age: naToEmpty(participant.age),
      sex: naToEmpty(participant.sex),
      handedness: naToEmpty(participant.handedness),
    },
    hardware: {
      manufacturer: data?.manufacturer || "",
      deviceModel: data?.manufacturers_model_name || "",
    },
    bids: {
      powerlineFreq: data?.powerline_freq ?? "",
      placementScheme: data?.placement_scheme ?? "",
      placementDescription: data?.placement_scheme_description ?? "",
      softwareVersions: data?.software_versions ?? null,
    },
  };
}
