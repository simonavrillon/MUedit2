/**
 * @typedef {import('../app/deps.js').ImportSetupDeps} ImportSetupDeps
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
