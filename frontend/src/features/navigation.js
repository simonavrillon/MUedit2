import {
  setEditCurrentMu,
  setEditView,
  setRunCurrentMu,
  setRunView,
} from "../state/actions.js";

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
    if (e.key === "ArrowLeft") {
      action = "scroll_left";
    } else if (e.key === "ArrowRight") {
      action = "scroll_right";
    } else if (e.key === "ArrowUp") {
      action = "zoom_in";
    } else if (e.key === "ArrowDown") {
      action = "zoom_out";
    } else if (key === "a") {
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
    } else if (e.key === "<") {
      goToMuFn("prev", "edit");
      e.preventDefault();
      return;
    } else if (e.key === ">") {
      goToMuFn("next", "edit");
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
