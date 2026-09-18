import { parseBidsEntitiesFromLabel } from "../../io/bids.js";

/** @typedef {import("../context.js").App} App */
/** @typedef {import("../context.js").StageKey} StageKey */

/**
 * @param {string} fullPath
 * @param {string} name
 * @returns {string}
 */
function displayNameForPath(fullPath, name) {
  if (String(name || "").toLowerCase() !== "info.rhd") return name;
  const parts = String(fullPath || "")
    .replace(/\\/g, "/")
    .split("/")
    .filter(Boolean);
  const folder = parts[parts.length - 2];
  return folder ? `${folder}.rhd` : name;
}

/**
 * @param {string} fullPath
 * @returns {string}
 */
function inferProjectFromPath(fullPath) {
  const parts = fullPath.replace(/\\/g, "/").split("/");
  const dataIdx = parts.lastIndexOf("data");
  if (dataIdx < 0 || dataIdx >= parts.length - 2) return "";
  const candidate = parts[dataIdx + 1];
  if (!candidate || candidate.startsWith("sub-")) return "";
  return candidate;
}

/** @param {App} app */
async function handleNativeDialogOpen(app) {
  const {
    api,
    setStatus,
    clearUploadFormatError,
    setUploadLoading,
    showUnsupportedUploadFormatError,
    detectLandingFileType,
    handleRawFilePath,
    loadDecompositionForEditByPath,
    setBidsEntitiesInput,
  } = app;

  clearUploadFormatError();
  setUploadLoading(false);

  let result;
  try {
    result = await api.openFileDialog();
  } catch (err) {
    console.error("File dialog failed:", err);
    setStatus("Failed to open file dialog", "error");
    return;
  }

  if (!result.path) return;

  const { path } = result;
  const name = displayNameForPath(path, result.name);
  const kind = detectLandingFileType({ name });

  if (kind === "unsupported") {
    showUnsupportedUploadFormatError();
    return;
  }
  if (kind === "raw") {
    await handleRawFilePath(path, name);
    const lname = name.toLowerCase();
    if (lname.endsWith(".bdf") || lname.endsWith(".edf")) {
      const entityLabel = name
        .replace(/_emg\.[^.]+$/i, "")
        .replace(/\.[^.]+$/, "");
      setBidsEntitiesInput({
        ...parseBidsEntitiesFromLabel(entityLabel),
        project: inferProjectFromPath(path),
      });
    }
  } else if (kind === "decomposition") {
    await loadDecompositionForEditByPath(path);
  } else {
    const ok = await handleRawFilePath(path, name, {
      silentPreviewFailure: true,
    });
    if (!ok) await loadDecompositionForEditByPath(path);
  }
}

/** @param {App} app */
export function setupImportEvents(app) {
  const {
    els,
    state,
    setStatus,
    showWorkspace,
    switchStage,
    updateWorkflowStepper,
  } = app;

  if (els.browseSignalBtn) {
    els.browseSignalBtn.addEventListener("click", () => {
      void handleNativeDialogOpen(app);
    });
  }

  const openStageFromStepper = (/** @type {StageKey} */ target) => {
    if (!state.file && target !== "edit") {
      setStatus("Import a file first", "muted");
      if (els.landing) els.landing.classList.remove("hidden");
      if (els.workspace) els.workspace.classList.add("hidden");
      updateWorkflowStepper("import");
      return;
    }
    showWorkspace();
    switchStage(target);
  };

  els.stepQc?.addEventListener("click", () => {
    openStageFromStepper("qc");
  });
  els.stepRun?.addEventListener("click", () => {
    openStageFromStepper("run");
  });
  els.stepEdit?.addEventListener("click", () => {
    openStageFromStepper("edit");
  });
  els.stepImport?.addEventListener("click", () => {
    if (els.landing) els.landing.classList.remove("hidden");
    if (els.workspace) els.workspace.classList.add("hidden");
    updateWorkflowStepper("import");
  });
}
