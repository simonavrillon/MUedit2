import { parseBidsEntitiesFromLabel } from "../../io/bids.js";

/** @typedef {import("../context.js").App} App */
/** @typedef {import("../context.js").Els} Els */
/** @typedef {import("../context.js").StageKey} StageKey */
/** @typedef {import("../context.js").FileRef} FileRef */

/** @param {Els} els */
function clearUploadFormatError(els) {
  if (!els.uploadFormatError) return;
  els.uploadFormatError.textContent = "";
  els.uploadFormatError.classList.add("hidden");
}

/** @param {Els} els */
function showUnsupportedUploadFormatError(els) {
  if (!els.uploadFormatError) return;
  els.uploadFormatError.textContent =
    "Accepted: raw (.mat, .otb+, .otb4, .bdf, .edf, .rhd) or decomposition (.npz, .mat)";
  els.uploadFormatError.classList.remove("hidden");
}

/**
 * What a picked file is, from its name: a `.mat` can be either.
 *
 * @param {FileRef} file
 * @returns {"raw" | "decomposition" | "ambiguous_mat" | "unsupported"}
 */
function detectLandingFileType(file) {
  const name = (file?.name || "").toLowerCase();
  if (name.endsWith(".otb+") || name.endsWith(".otb4")) return "raw";
  if (name.endsWith(".bdf") || name.endsWith(".edf")) return "raw";
  if (name.endsWith(".rhd")) return "raw";
  if (name.endsWith(".npz")) return "decomposition";
  if (name.endsWith(".mat")) return "ambiguous_mat";
  return "unsupported";
}

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

/** @param {App} app */
async function handleNativeDialogOpen(app) {
  const {
    api,
    els,
    setStatus,
    setUploadLoading,
    handleRawFilePath,
    loadDecompositionForEditByPath,
    setBidsEntitiesInput,
  } = app;

  clearUploadFormatError(els);
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
  const name = displayNameForPath(path, result.name ?? "");
  const kind = detectLandingFileType({ name });

  if (kind === "unsupported") {
    showUnsupportedUploadFormatError(els);
    return;
  }
  if (kind === "raw") {
    await handleRawFilePath(path, name);
    const lname = name.toLowerCase();
    if (lname.endsWith(".bdf") || lname.endsWith(".edf")) {
      const entityLabel = name
        .replace(/_emg\.[^.]+$/i, "")
        .replace(/\.[^.]+$/, "");
      setBidsEntitiesInput(parseBidsEntitiesFromLabel(entityLabel));
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
