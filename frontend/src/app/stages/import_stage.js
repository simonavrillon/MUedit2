import { parseBidsEntitiesFromLabel } from "../../io/bids.js";

function inferProjectFromPath(fullPath) {
  const parts = fullPath.replace(/\\/g, "/").split("/");
  const dataIdx = parts.lastIndexOf("data");
  if (dataIdx < 0 || dataIdx >= parts.length - 2) return "";
  const candidate = parts[dataIdx + 1];
  if (!candidate || candidate.startsWith("sub-")) return "";
  return candidate;
}

export function createImportStageService(deps) {
  const {
    apiJson,
    API_BASE,
    setStatus,
    clearUploadFormatError,
    setUploadLoading,
    showUnsupportedUploadFormatError,
    detectLandingFileType,
    handleRawFilePath,
    loadDecompositionForEditByPath,
    setBidsEntitiesInput,
  } = deps;

  async function handleNativeDialogOpen() {
    clearUploadFormatError();
    setUploadLoading(false);

    let result;
    try {
      result = await apiJson(`${API_BASE}/dialog/open-file`);
    } catch (err) {
      console.error("File dialog failed:", err);
      setStatus("Failed to open file dialog", "error");
      return;
    }

    if (!result.path) return;

    const { path, name } = result;
    const kind = detectLandingFileType({ name });

    if (kind === "unsupported") {
      showUnsupportedUploadFormatError();
      return;
    }
    if (kind === "raw") {
      await handleRawFilePath(path, name);
      const lname = name.toLowerCase();
      if (lname.endsWith(".bdf") || lname.endsWith(".edf")) {
        const entityLabel = name.replace(/_emg\.[^.]+$/i, "").replace(/\.[^.]+$/, "");
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

  return { handleNativeDialogOpen };
}

/**
 * @typedef {import('../deps.js').ImportSetupDeps} ImportSetupDeps
 */

/**
 * @param {ImportSetupDeps} deps
 */
export function setupImportEvents(deps) {
  const {
    els,
    state,
    handleNativeDialogOpen,
    setStatus,
    showWorkspace,
    switchStage,
    updateWorkflowStepper,
  } = deps;

  if (els.browseSignalBtn) {
    els.browseSignalBtn.addEventListener("click", () => {
      void handleNativeDialogOpen();
    });
  }

  const openStageFromStepper = (target) => {
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
