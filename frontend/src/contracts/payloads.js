/**
 * Frontend payload contracts and normalization.
 */

function toFiniteNumber(value, fallback = 0) {
  const n = Number(value);
  return Number.isFinite(n) ? n : fallback;
}

export function normalizeEditLoadPayload(payload) {
  const source = payload && typeof payload === "object" ? payload : {};
  const distRaw = source.distime_all || source.distime || [];
  return {
    ...source,
    pulse_trains: Array.isArray(source.pulse_trains) ? source.pulse_trains : [],
    pulse_trains_full: Array.isArray(source.pulse_trains_full)
      ? source.pulse_trains_full
      : [],
    distime_all: Array.isArray(distRaw)
      ? distRaw.map((row) =>
          Array.isArray(row)
            ? row.map((v) => toFiniteNumber(v)).filter(Number.isFinite)
            : [],
        )
      : [],
    grid_names: Array.isArray(source.grid_names) ? source.grid_names : [],
    mu_grid_index: Array.isArray(source.mu_grid_index)
      ? source.mu_grid_index
      : [],
    parameters:
      source.parameters && typeof source.parameters === "object"
        ? source.parameters
        : {},
    total_samples: toFiniteNumber(source.total_samples, 0),
    fsamp: source.fsamp ?? null,
    file_label: String(source.file_label || ""),
    edit_signal_token: String(source.edit_signal_token || ""),
  };
}

export function normalizePreviewPayload(payload) {
  const source = payload && typeof payload === "object" ? payload : {};
  return {
    ...source,
    mean_abs: Array.isArray(source.mean_abs) ? source.mean_abs : [],
    grid_mean_abs: Array.isArray(source.grid_mean_abs)
      ? source.grid_mean_abs
      : [],
    grid_names: Array.isArray(source.grid_names) ? source.grid_names : [],
    rois: Array.isArray(source.rois) ? source.rois : [],
    channel_means: Array.isArray(source.channel_means)
      ? source.channel_means
      : [],
    coordinates: Array.isArray(source.coordinates) ? source.coordinates : [],
    metadata:
      source.metadata && typeof source.metadata === "object"
        ? source.metadata
        : {},
    muscle: Array.isArray(source.muscle) ? source.muscle : [],
    pulse_trains_full: Array.isArray(source.pulse_trains_full)
      ? source.pulse_trains_full
      : [],
    pulse_trains_all: Array.isArray(source.pulse_trains_all)
      ? source.pulse_trains_all
      : [],
    distime_all: Array.isArray(source.distime_all) ? source.distime_all : [],
    mu_grid_index: Array.isArray(source.mu_grid_index)
      ? source.mu_grid_index
      : [],
    auxiliary: Array.isArray(source.auxiliary) ? source.auxiliary : [],
    auxiliary_names: Array.isArray(source.auxiliary_names)
      ? source.auxiliary_names
      : [],
    total_samples: toFiniteNumber(source.total_samples, 0),
  };
}
