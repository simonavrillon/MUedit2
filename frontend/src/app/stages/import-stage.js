import { parseBidsEntitiesFromLabel } from "../../io/bids.js";
import {
  chooseOutputFolder,
  IS_DESKTOP,
  openFile,
  outputFolder,
} from "../platform.js";

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
    result = await openFile(() => api.openFileDialog());
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

/**
 * Show the output folder in the session panel and let the user move it;
 * the desktop app only, as the browser app writes where the server is told to.
 *
 * @param {App} app
 */
export async function setupOutputFolder(app) {
  const { els, setStatus } = app;
  if (!IS_DESKTOP || !els.outputFolderRow || !els.outputFolder) return;
  const label = els.outputFolder;
  /** @param {string} path */
  const show = (path) => {
    label.textContent = path;
    label.title = path;
  };
  show(await outputFolder());
  els.outputFolderRow.classList.remove("hidden");
  els.outputFolderBtn?.addEventListener("click", async () => {
    try {
      const path = await chooseOutputFolder();
      if (path) show(path);
    } catch (err) {
      console.error("Output folder dialog failed:", err);
      setStatus("Failed to change the output folder", "error");
    }
  });
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
