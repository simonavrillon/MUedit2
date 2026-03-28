import { setCurrentStage } from "../state/actions.js";

export function setStatus(els, text, tone = "muted") {
  if (!els.status) return;
  els.status.textContent = text;
  els.status.dataset.tone = tone;
}

export function updateWorkflowStepper({ els, state }, targetStage) {
  const steps = [
    { key: "import", el: els.stepImport, complete: !!state.file },
    { key: "qc", el: els.stepQc, complete: !!state.previewSeries?.length },
    { key: "run", el: els.stepRun, complete: !!state.muDistimes?.length },
    { key: "edit", el: els.stepEdit, complete: !!state.edit.distimes?.length },
  ];
  const activeKeyByStage = {
    import: "import",
    qc: "qc",
    run: "run",
    edit: "edit",
  };
  const activeKey = activeKeyByStage[targetStage] || "qc";
  steps.forEach((step) => {
    if (!step.el) return;
    step.el.classList.remove("active", "complete", "pending");
    if (step.key === activeKey) {
      step.el.classList.add("active");
    } else if (step.complete) {
      step.el.classList.add("complete");
    } else {
      step.el.classList.add("pending");
    }
  });
}

export function showWorkspace(
  { els, state, setSettingsOpen, switchStage, populateGridTabs },
  options = {},
) {
  const { keepLandingVisible = false } = options;
  if (els.landing && !keepLandingVisible) els.landing.classList.add("hidden");
  if (els.workspace) els.workspace.classList.remove("hidden");
  setSettingsOpen(false);
  switchStage(state.currentStage || "qc");
  populateGridTabs();
}

export function updateStepAvailability({ els, state }) {
  const hasFile = !!state.file;
  const hasPreview = !!state.previewSeries?.length;
  const hasRunResults = !!state.muDistimes?.length;
  const hasEditData = !!state.edit.distimes?.length;

  if (els.stepRun) {
    // Run becomes available once a preview exists, independent of active stage.
    els.stepRun.disabled = !hasFile || !hasPreview;
  }
  if (els.stepEdit) {
    // Edit is available after a run result exists, or when edit data is loaded.
    els.stepEdit.disabled = !hasFile || (!hasRunResults && !hasEditData);
  }
}

export function switchStage(
  {
    state,
    els,
    setSettingsOpen,
    setStatus,
    updateStepAvailability,
    updateWorkflowStepper,
    scheduleLayoutRerender,
  },
  target,
) {
  if (!state.file && target !== "edit") {
    return;
  }
  setSettingsOpen(false);
  if (target === "run" && !state.previewSeries?.length) {
    setStatus("Run step is locked until preview is loaded", "muted");
    return;
  }
  if (target === "edit" && !state.edit.distimes?.length) {
    setStatus("Load a decomposition file to edit", "muted");
  }
  setCurrentStage(state, target);
  const qcStage = els.stageQc;
  const runStage = els.stageRun;
  const editStage = els.stageEdit;
  if (qcStage) qcStage.classList.toggle("active", target === "qc");
  if (runStage) runStage.classList.toggle("active", target === "run");
  if (editStage) editStage.classList.toggle("active", target === "edit");
  updateStepAvailability();
  updateWorkflowStepper(target);
  scheduleLayoutRerender(0);
}

export function populateGridTabs({ els, state, setSelectedGrid }) {
  if (!els.qcGridTabs) return;
  els.qcGridTabs.innerHTML = "";
  (state.gridNames || []).forEach((name, idx) => {
    const btn = document.createElement("button");
    btn.className = `tab-btn ${idx === state.currentGrid ? "active" : ""}`;
    btn.textContent = `Grid ${idx + 1}${name ? ` • ${name}` : ""}`;
    btn.onclick = () => setSelectedGrid(idx);
    els.qcGridTabs.appendChild(btn);
  });
}
