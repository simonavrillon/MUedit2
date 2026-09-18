/**
 * The workspace stages and what happens as the user moves between them.
 *
 * `switchStage` is the only way the active stage changes: it asks the target
 * whether it can be entered, runs the outgoing stage's `exit` and the
 * incoming stage's `enter`, then shows the target's panel. `render` redraws a
 * stage's plots and is what layout changes call for the visible stage.
 *
 * Loading a new file is not a stage change: the edit slice is reset by the
 * raw-preview transition in `state/transitions.js`, because leaving the edit
 * stage to look at QC must keep the user's edits.
 */
import { setArtifactMode, setCurrentStage } from "../../state/actions.js";
import { renderArtifactControls } from "../../view/qc-renderer.js";

/** @typedef {import("../context.js").App} App */
/** @typedef {import("../context.js").StageKey} StageKey */

/**
 * @typedef {object} StageDefinition
 * @property {"stageQc" | "stageRun" | "stageEdit"} panel
 * @property {(app: App) => string | null} blocked
 *   Why the stage cannot be entered, or null; an empty string refuses silently.
 * @property {(app: App) => void} render
 * @property {(app: App) => void} [enter]
 * @property {(app: App) => void} [exit]
 */

/** @type {Record<StageKey, StageDefinition>} */
export const STAGES = {
  qc: {
    panel: "stageQc",
    blocked: ({ state }) => (state.file ? null : ""),
    render(app) {
      app.renderChannelQC();
      app.refreshVisuals();
    },
    // An armed artifact drag is a QC gesture; it should not outlive the stage.
    exit({ state, els }) {
      if (!state.artifactMode) return;
      setArtifactMode(state, false);
      renderArtifactControls(els, state);
    },
  },
  run: {
    panel: "stageRun",
    blocked({ state }) {
      if (!state.file) return "";
      if (!state.previewSeries?.length) {
        return "Run step is locked until preview is loaded";
      }
      return null;
    },
    render(app) {
      app.renderMuExplorer();
    },
  },
  edit: {
    panel: "stageEdit",
    blocked: () => null,
    render(app) {
      if (app.state.edit.distimes?.length) app.renderEditExplorer();
    },
    enter(app) {
      if (!app.state.edit.distimes?.length) {
        app.setStatus("Load a decomposition file to edit", "muted");
      }
    },
    // Add/delete/artifact modes arm the next drag on the edit canvases; the
    // status line is showing that mode's "Drag a box…" hint.
    exit(app) {
      if (!app.state.edit.mode) return;
      app.setEditMode(null);
      app.setEditStatus("", "muted");
    },
  },
};

/**
 * @param {App} app
 * @param {StageKey} target
 */
export function switchStage(app, target) {
  const { state, els } = app;
  const next = STAGES[target];
  if (!next) return;
  const reason = next.blocked(app);
  if (reason === "") return;
  app.setSettingsOpen(false);
  if (reason) {
    app.setStatus(reason, "muted");
    return;
  }

  const from = state.currentStage;
  if (from !== target) STAGES[from]?.exit?.(app);
  setCurrentStage(state, target);
  for (const [key, stage] of Object.entries(STAGES)) {
    els[stage.panel]?.classList.toggle("active", key === target);
  }
  if (from !== target) next.enter?.(app);

  app.updateStepAvailability();
  app.updateWorkflowStepper(target);
  app.scheduleLayoutRerender(0);
}

/** @param {App} app */
export function renderActiveStage(app) {
  STAGES[app.state.currentStage]?.render(app);
}
