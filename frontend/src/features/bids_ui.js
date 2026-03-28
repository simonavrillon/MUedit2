import { inferGridCount, normalizeGridNames } from "./grid_names.js";

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
    Array.isArray(state.gridNames) && state.gridNames.length ? state.gridNames : [];
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

export function buildSessionInfoFromDecomposition(
  file,
  data,
  { parseBidsEntitiesFromLabel, listifyMuscles },
) {
  const fileLabel = file?.name || data?.file_label || "decomposition";
  const fs = Number(data?.fsamp);
  const entities = parseBidsEntitiesFromLabel(fileLabel);

  const fromParams = data?.parameters?.target_muscle;
  const fromPayload = data?.muscle;
  const muscles = listifyMuscles(fromPayload).length
    ? listifyMuscles(fromPayload)
    : listifyMuscles(fromParams);

  return {
    fileLabel,
    fsampText: Number.isFinite(fs) && fs > 0 ? String(Math.round(fs)) : "",
    entities,
    muscles,
  };
}
