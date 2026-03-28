export function safeBidsToken(value, fallback = "") {
  const token = String(value || "")
    .trim()
    .replace(/[^A-Za-z0-9]+/g, "");
  return token || fallback;
}

export function buildEntityLabelFromSession(subject, task, session, run) {
  const sub = safeBidsToken(subject, "01");
  const t = safeBidsToken(task, "task");
  const ses = safeBidsToken(session, "");
  const r = safeBidsToken(run, "01");
  const sesPart = ses ? `_ses-${ses}` : "";
  return `sub-${sub}${sesPart}_task-${t}_run-${r}`;
}

export function getSuggestedNpzName(baseName, suffix = "_edited") {
  const stem = String(baseName || "decomposition").replace(/\.[^.]+$/, "");
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
    run: get("run"),
  };
}

export function listifyMuscles(value) {
  if (Array.isArray(value))
    return value.map((v) => String(v || "").trim()).filter(Boolean);
  if (typeof value === "string" && value.trim()) return [value.trim()];
  return [];
}
