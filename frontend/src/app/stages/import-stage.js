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
    "Accepted: raw (.mat, .otb+, .otb4, .bdf, .edf, .rhd, .oebin) or decomposition (.npz, .mat)";
  els.uploadFormatError.classList.remove("hidden");
}

/** @typedef {"raw" | "decomposition" | "ambiguous_mat" | "unsupported"} FileKind */

/** @type {Record<string, FileKind>} */
const KIND_BY_EXTENSION = {
  "otb+": "raw",
  otb4: "raw",
  bdf: "raw",
  edf: "raw",
  rhd: "raw",
  oebin: "raw",
  npz: "decomposition",
  // A .mat can be either; it is tried as a recording first.
  mat: "ambiguous_mat",
};

/** BIDS EMG files, whose names carry their entities. */
const BIDS_EMG_FILE = /\.(bdf|edf)$/i;

/**
 * What a picked file is, from its name.
 *
 * @param {FileRef} file
 * @returns {FileKind}
 */
function detectLandingFileType(file) {
  const extension = (file?.name || "").toLowerCase().split(".").pop() ?? "";
  return KIND_BY_EXTENSION[extension] ?? "unsupported";
}

/** Header files whose name is fixed, so the recording is named after its folder. */
const FOLDER_NAMED_FILES = { "info.rhd": ".rhd", "structure.oebin": ".oebin" };

/**
 * @param {string} fullPath
 * @param {string} name
 * @returns {string}
 */
function displayNameForPath(fullPath, name) {
  const extension =
    FOLDER_NAMED_FILES[
      /** @type {keyof typeof FOLDER_NAMED_FILES} */ (
        String(name || "").toLowerCase()
      )
    ];
  if (!extension) return name;
  const parts = String(fullPath || "")
    .replace(/\\/g, "/")
    .split("/")
    .filter(Boolean);
  const folder = parts[parts.length - 2];
  return folder ? `${folder}${extension}` : name;
}

/**
 * Whether to open `name` when the decomposition being edited has unsaved
 * edits; it is asked only then. The edits stay in the session's log, and
 * are offered back when that decomposition is opened again.
 *
 * @param {App} app
 * @param {string} name
 */
function confirmLeavingUnsavedEdits(app, name) {
  if (!app.state.edit.dirty) return true;
  const ask = globalThis.window?.confirm;
  return (
    typeof ask === "function" &&
    ask(
      `The decomposition you are editing has unsaved edits. Open ${name} anyway? ` +
        "The edits will be offered back when you open that decomposition again.",
    )
  );
}

/**
 * Ask for a file and open it. One open at a time: the backend holds one
 * upload per tab, and a second preview would drop the first while it loads.
 *
 * @param {App} app
 */
async function openPickedFile(app) {
  const button = app.els.browseSignalBtn;
  if (button) button.disabled = true;
  try {
    await handleNativeDialogOpen(app);
  } finally {
    if (button) button.disabled = false;
  }
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
  if (!confirmLeavingUnsavedEdits(app, name)) return;
  if (kind === "raw") {
    const ok = await handleRawFilePath(path, name);
    if (ok && BIDS_EMG_FILE.test(name)) {
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
  const { els, state, setStatus, showWorkspace, updateWorkflowStepper } = app;

  if (els.browseSignalBtn) {
    els.browseSignalBtn.addEventListener("click", () => {
      void openPickedFile(app);
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
    showWorkspace(target);
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
