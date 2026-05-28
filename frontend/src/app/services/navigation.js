import {
  setCurrentStage,
  setEditCurrentMu,
  setEditView,
  setRunCurrentMu,
  setRunView,
} from "../../state/actions.js";

export function setStatus(els, text, tone = "muted") {
  if (!els.status) return;
  els.status.textContent = text;
  els.status.dataset.tone = tone;
}

export function updateWorkflowStepper(deps, targetStage) {
  const { els, state } = deps;
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

export function showWorkspace(deps, options = {}) {
  const { els, state, setSettingsOpen, switchStage, populateGridTabs } = deps;
  const { keepLandingVisible = false } = options;
  if (els.landing && !keepLandingVisible) els.landing.classList.add("hidden");
  if (els.workspace) els.workspace.classList.remove("hidden");
  setSettingsOpen(false);
  switchStage(state.currentStage || "qc");
  populateGridTabs();
}

export function updateStepAvailability(deps) {
  const { els, state } = deps;
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

export function switchStage(deps, target) {
  const {
    state,
    els,
    setSettingsOpen,
    setStatus,
    updateStepAvailability,
    updateWorkflowStepper,
    scheduleLayoutRerender,
  } = deps;
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

export function populateGridTabs(deps) {
  const { els, state, setSelectedGrid } = deps;
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

export function getViewForStage(state, stage) {
  if (stage === "edit") {
    const pulse = state.edit.pulseTrains?.[state.edit.currentMu] || [];
    if (!state.edit.view && pulse.length) {
      setEditView(state, { start: 0, end: pulse.length });
    }
    return { view: state.edit.view, total: pulse.length };
  }
  if (stage === "run") {
    const pulse = state.muPulseTrains?.[state.currentMu] || [];
    if (!state.runView && pulse.length) {
      setRunView(state, { start: 0, end: pulse.length });
    }
    return { view: state.runView, total: pulse.length };
  }
  return { view: null, total: 0 };
}

export function setViewForStage(deps, stage, view) {
  const { state, renderEditExplorer, renderMuExplorer } = deps;
  if (stage === "edit") {
    setEditView(state, view);
    renderEditExplorer();
    return;
  }
  if (stage === "run") {
    setRunView(state, view);
    renderMuExplorer();
  }
}

export function adjustView(view, total, action) {
  if (!view || total <= 0) return view;
  const span = Math.max(1, view.end - view.start);
  const center = view.start + span / 2;
  let nextSpan = span;
  let nextStart = view.start;
  let nextEnd = view.end;

  if (action === "zoom_in") {
    nextSpan = Math.max(10, Math.round(span * 0.8));
  } else if (action === "zoom_out") {
    nextSpan = Math.min(total, Math.round(span * 1.5));
  } else if (action === "scroll_left") {
    const step = Math.max(1, Math.round(span * 0.05));
    nextStart = view.start - step;
    nextEnd = view.end - step;
  } else if (action === "scroll_right") {
    const step = Math.max(1, Math.round(span * 0.05));
    nextStart = view.start + step;
    nextEnd = view.end + step;
  }

  if (action === "zoom_in" || action === "zoom_out") {
    nextStart = Math.round(center - nextSpan / 2);
    nextEnd = Math.round(center + nextSpan / 2);
  }

  if (nextStart < 0) {
    nextEnd -= nextStart;
    nextStart = 0;
  }
  if (nextEnd > total) {
    const overflow = nextEnd - total;
    nextStart = Math.max(0, nextStart - overflow);
    nextEnd = total;
  }
  if (nextEnd <= nextStart) {
    nextEnd = Math.min(total, nextStart + 1);
  }
  return { start: nextStart, end: nextEnd };
}

export function goToMu(deps, direction, stage) {
  const { state } = deps;
  if (stage === "edit") {
    const { getEditMuIndicesForGrid, renderEditExplorer } = deps;
    const gridIdx = state.edit.currentMuGrid || 0;
    const mus = getEditMuIndicesForGrid(gridIdx);
    if (!mus.length) return;
    const current = state.edit.currentMu ?? mus[0];
    const idx = mus.indexOf(current);
    const offset = direction === "prev" ? -1 : 1;
    const next = mus[(idx + offset + mus.length) % mus.length];
    setEditCurrentMu(state, next, { resetView: true });
    renderEditExplorer();
  } else if (stage === "run") {
    const { getMuIndicesForGrid, renderMuExplorer } = deps;
    const gridIdx = state.currentMuGrid || 0;
    const mus = getMuIndicesForGrid(gridIdx);
    if (!mus.length) return;
    const current = state.currentMu ?? mus[0];
    const idx = mus.indexOf(current);
    const next = mus[(idx + (direction === "prev" ? -1 : 1) + mus.length) % mus.length];
    setRunCurrentMu(state, next, { resetView: true });
    renderMuExplorer();
  }
}

export function handleKeyboardNavigation(deps, e) {
  const {
    state,
    els,
    setEditMode,
    runEditAction,
    removeOutliers,
    updateMuFilter,
    goToMuFn,
    getViewForStageFn,
    adjustViewFn,
    setViewForStageFn,
  } = deps;

  const active = document.activeElement;
  if (active && ["INPUT", "TEXTAREA", "SELECT"].includes(active.tagName))
    return;
  const stage = state.currentStage;
  if (stage !== "run" && stage !== "edit") return;

  let action = null;
  if (stage === "edit") {
    const key = e.key.toLowerCase();
    if (key === "a") {
      setEditMode("add", "Drag a box on pulse train to add spikes");
      e.preventDefault();
      return;
    } else if (key === "r") {
      void runEditAction(els.editOutliersBtn, () => removeOutliers());
      e.preventDefault();
      return;
    } else if (key === " ") {
      void runEditAction(els.editUpdateBtn, updateMuFilter);
      e.preventDefault();
      return;
    } else if (key === "d") {
      setEditMode(
        "delete_spikes",
        "Drag a box on pulse train to delete spikes",
      );
      e.preventDefault();
      return;
    } else if (key === "x") {
      setEditMode("add_artifact", "Drag a box on pulse train to mark an artifact");
      e.preventDefault();
      return;
    } else if (e.key === "<") {
      goToMuFn("prev", "edit");
      e.preventDefault();
      return;
    } else if (e.key === ">") {
      goToMuFn("next", "edit");
      e.preventDefault();
      return;
    } else if (key === "p") {
      if (els.editPeelOffToggle) {
        const isOn = els.editPeelOffToggle.dataset.state === "on";
        els.editPeelOffToggle.dataset.state = isOn ? "off" : "on";
        els.editPeelOffToggle.setAttribute("aria-pressed", isOn ? "false" : "true");
        els.editPeelOffToggle.classList.toggle("on", !isOn);
        const label = isOn ? "Off" : "On";
        const shortEl = els.editPeelOffToggle.querySelector(".peeloff-short");
        const fullEl = els.editPeelOffToggle.querySelector(".peeloff-full");
        if (shortEl) shortEl.textContent = label;
        if (fullEl) fullEl.textContent = `Peel-off: ${label}`;
      }
      e.preventDefault();
      return;
    } else if (key === "l") {
      if (els.editLockSpikesToggle) {
        const isOn = els.editLockSpikesToggle.dataset.state === "on";
        els.editLockSpikesToggle.dataset.state = isOn ? "off" : "on";
        els.editLockSpikesToggle.setAttribute("aria-pressed", isOn ? "false" : "true");
        els.editLockSpikesToggle.classList.toggle("on", !isOn);
        const label = isOn ? "Off" : "On";
        const shortEl = els.editLockSpikesToggle.querySelector(".lockspikes-short");
        const fullEl = els.editLockSpikesToggle.querySelector(".lockspikes-full");
        if (shortEl) shortEl.textContent = label;
        if (fullEl) fullEl.textContent = `Lock: ${label}`;
      }
      e.preventDefault();
      return;
    }
  }

  if (e.key === "ArrowUp") action = "zoom_in";
  if (e.key === "ArrowDown") action = "zoom_out";
  if (e.key === "ArrowLeft") action = "scroll_left";
  if (e.key === "ArrowRight") action = "scroll_right";
  if (!action) return;

  const { view, total } = getViewForStageFn(stage);
  if (!view || !total) return;
  const next = adjustViewFn(view, total, action);
  setViewForStageFn(stage, next);
  e.preventDefault();
}
