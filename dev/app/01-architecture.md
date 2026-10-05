# 01 - Architecture

## Boot Sequence

```
index.html
  └─ loads app.js (type="module")
      └─ import { initializeApp } from container.js
          └─ initializeApp()
```

### `initializeApp()` — `container.js`

```
initializeApp():
  1. app = createApp({ state, els, api })  # one context, every service merged in
  2. setupImportEvents(app)              # browse button, stepper clicks
     setupQcEvents(app)                  # auto-QC, artifact buttons, ROI drag
     setupRunEvents(app)                 # start/cancel, run settings toggles
     setupEditEvents(app)                # edit toolbar, canvas bindings, keyboard
     setupLayoutEvents(app)              # settings icon, section collapse, overlay, ESC
  3. app.updateStepAvailability()        # disable run/edit steps if no file
  4. app.updateWorkflowStepper("import") # highlight "Import" chip
  5. els.browseSignalBtn.disabled = true # block until backend responds
  6. app.setStatus("Connecting to backend...")   # #status pill, top-right of header
  7. await waitForBackend(api.healthUrl())
     ├─ success → enable browse button, clear status, restore an open edit session
     └─ failure → "Backend unreachable — please restart the app"
```

---

## The Application Context

Feature functions receive one object, `app`, typed as `App` in `app/context.js`. It holds the three singletons and the services one module calls in another:

```
App = Core                # state, els, api — the three singletons
    & UiService           # status, stepper, settings panel, switchStage, redraws
    & FileSessionService  # file-type detection, upload indicator, BIDS form fields, save
    & QcStage             # preview, channel grid, ROI/artifact selection, auto-QC
    & RunStage            # decomposition run, stream handling, the live run page
    & EditStage           # edit load/save, canvases, ROI edits, filters, edit modes
```

### Construction — `create-app.js`

```js
const app = { state, els, api };
for (const service of [ui, fileSession, qcStage, runStage, editStage]) {
  Object.assign(app, createService(app));   // throws on a duplicate member name
}
```

Each factory receives the same `app` it is being merged into. The QC, run and edit stages call each other (run renders QC, a finished run preloads the edit stage), and that works because of one rule:

- **A factory reads only `state`, `els` and `api` while it is being constructed.** Everything else is reached as `app.x()` (or destructured from `app`) inside a function body, so it is looked up at call time, after every service has been merged.

A feature function such as `removeOutliers(app)` in `editing-service.js` destructures the members it uses straight from `app`.

### What belongs in the context

A member goes on `app` when another module calls it and goes through `app` to reach it: the QC renderer redrawing the aux channels, the run page preloading the edit stage, the import stage opening a raw file. Tests replace those members with recorders, which is what keeps each stage testable on its own. The `schedule…` methods are there too, because each holds its own once-per-frame state.

Everything else is imported where it is used:

- **A stage's own actions.** `setupEditEvents` wires the toolbar to `undoEdit(app)`, `saveEditedFile(app)` and the rest from `editing-service.js`; `setupRunEvents` wires start and cancel to `runDecomposition(app)` and `cancelDecomposition(app)`; `setupQcEvents` wires auto-QC and the artifact buttons. None of these is reached through `app`.
- **Pure helpers:** plot drawing (`view/plots.js`), series math (`signal/series.js`), toggles and busy buttons (`view/controls.js`), BIDS naming (`io/bids.js`), state actions and selectors (`state/`), parameter building (`decomp/params.js`), and `COLORS`.

### Type checking

`npm run typecheck` runs `tsc --checkJs` in `strict` mode over `src/` (config in `frontend/tsconfig.json`, CI runs it on every push). Every parameter needs a type and nullable values must be checked before use. Because every feature is annotated `@param {App} app`, a misspelt or removed context member is a type error, and each factory's `@returns` ties it to its typedef in `context.js`. DOM handles in `dom.js` are typed per element (`HTMLInputElement`, `HTMLCanvasElement`, …).

The annotations follow five rules:

- **Intended types.** A parameter is typed as what callers should pass. When the function guards against missing or malformed input, write `T | null | undefined` and keep the guard: `@param {Span[] | null | undefined} rois`.
- **`unknown` only for values that really can be anything:** caught errors (`errorMessage(err)`) and the helpers that validate raw input (`toFiniteNumber`, the payload normalisers in `api/payloads.js`).
- **Untyped backend JSON is `JsonObject`** (`Record<string, any>`, defined in `context.js`). A payload with guaranteed fields gets a named type that intersects `JsonObject` with them, like `PreviewPayload`. Wire formats are converted where they enter: the backend sends regions (ROIs, artifact windows) as `[start, end]` pairs, and `toSpans` in `api/payloads.js` turns them into `Span` objects, so state and views never see a pair.
- **Shared shapes have names**, declared next to the code that owns them: state shapes in `state/state.js` (`Selection`, `EditHistoryEntry`, `Bookmark`), cross-module contracts in `context.js` (`RoiEditRequest`, `WorkflowStep`), and view models beside their builders (`EditDropdownModel = ReturnType<typeof buildEditDropdownModel>`). Single-use parameter bags stay inline.
- **Service and stage methods take their types from `context.js`:** `/** @type {EditStage["duplicateMu"]} */`. Only private helpers inside a factory carry their own `@param`s.

To add a service method, first check it belongs in the context (see above). Then add its signature to the matching typedef in `context.js`, implement it in the factory under `/** @type {Typedef["name"]} */` and add it to the factory's return object.

### Wiring Topology

```
                    ┌─────────┐
                    │ config  │  API_BASE, COLORS
                    └────┬────┘
                         │
              ┌──────────┼──────────┐
              v          v          v
         ┌────────┐  ┌────────┐  ┌───────┐
         │ http   │  │ dom    │  │ state │
         │(fetch) │  │ (els)  │  │       │
         └───┬────┘  └───┬────┘  └───┬───┘
             v           │           │
        ┌──────────┐     │           │
        │ apiClient│     │           │
        └────┬─────┘     │           │
             v           v           v
        ┌──────────────────────────────────────────────────────┐
        │  createApp → app                                      │
        │    ui · fileSession · qcStage · runStage · editStage  │
        └──────────────────────────┬───────────────────────────┘
                                   v
        setupImportEvents · setupQcEvents · setupRunEvents · setupEditEvents · setupLayoutEvents
```

---

## State Model

### Global state (`state/state.js`)

```javascript
{
  // Import / QC / Run shared
  file, uploadToken, isRunning, seriesLength,
  rois: [], roiDraft,
  artifactRegions: [], artifactDraft, artifactMode,
  gridNames: [], gridSeries: [],
  channelMeans: [], coordinates: [],
  discardMasks: [], channelTraces: [],
  metadata: {}, muscle: [],
  currentStage: "qc", currentGrid: 0,
  fsamp, auxSeries: [], auxNames: [],
  parameters,

  // Run stage (directly on global state)
  runLive,                   // the current run, as the run page shows it
                             //   (null before one): phases, per-iteration dots, summary
  runResultToken,            // the server keeps the run's pulse trains and discharge times under it
  runDownloadInFlight, lastRunDownloadKey,

  // Edit stage (nested slice)
  edit: createEditSlice(),
}
```

### Per-stage edit slice (`state.edit`)

The edit slice mirrors the server-side edit session: the pulse trains stay on the server, and
`pulseView` holds the window of the current MU on screen. It changes only through
`setEditSession` (a whole session), `applyEditChange` (one edit) and `applyEditSave` (a save),
plus the view, selection and bookmark setters.

```javascript
{
  file, filename,
  token,                      // the server's edit session
  distimes: [],               // Int32Array per MU
  artifactTimes: [],          // Int32Array per MU
  versions: [],               // per MU; changes whenever the server edits it
  hasPulse: [],               // per MU; false when the file has only discharge times
  gridNames: [], muGridIndex: [],
  fsamp, totalSamples,
  currentMuGrid, currentMu,
  view: { start, end },
  selectionPulse, selectionDr,
  draftSelectionPulse, draftSelectionDr,
  mode,                       // "add" | "add_artifact" | "delete_spikes" | "delete_dr" | null
  dirty, canUndo,             // the server's
  parameters,
  flagged: [],                // per-MU deletion flags
  bidsRoot, project,
  pulseView,                  // the fetched window: { mu, version, start, end, bins, row, spikes, … }
  muUids: [],
  editHistory: [],
  bookmarkPosition, showBookmark,
  softwareVersions,
}
```

### State Separation

```
┌─────────────────────────────────────────────┐
│              state (global)                  │
│                                             │
│  Import/QC/Run share:                        │
│    file, rois, gridNames,                    │
│    gridSeries, channelMeans, coordinates,    │
│    discardMasks, artifactRegions, artifactMode,│
│    currentGrid, fsamp, runLive               │
│                                             │
│  ┌─────────────────────────────────────┐    │
│  │        state.edit (per-stage)       │    │
│  │                                     │    │
│  │  Edit-only:                          │    │
│  │    token, distimes, artifactTimes,   │    │
│  │    versions, pulseView, mode, dirty, │    │
│  │    canUndo, flagged, editHistory,    │    │
│  │    bookmarkPosition, muUids, view    │    │
│  └─────────────────────────────────────┘    │
└─────────────────────────────────────────────┘
```

The run stage's `runLive` sits directly on the global state; it holds no view window, so nothing the run page shows is at risk from the edit stage's view. The edit stage uses `state.edit.pulseView` / `state.edit.currentMu` / `state.edit.view` on the nested slice. This prevents the two stages from clobbering each other's state.

---

## Stage Registration and Lifecycle

The three workspace stages are declared in `app/stages/lifecycle.js`:

```js
STAGES = {
  qc:   { panel: "stageQc",   blocked, render, exit },
  run:  { panel: "stageRun",  blocked, render },
  edit: { panel: "stageEdit", blocked, render, enter, exit },
}
```

| Hook | QC | Run | Edit |
|---|---|---|---|
| `blocked` | no file (silent) | no file (silent); no preview → "Run step is locked until preview is loaded" | never |
| `enter` | — | — | no edit data → status "Load a decomposition file to edit" |
| `exit` | disarm artifact selection | — | clear the armed edit mode (add / add artifact / delete) |
| `render` | channel grid + EMG/aux plots | the run page (plan, live view or result) | edit plots, once data is loaded |

`enter` and `exit` run only when the stage actually changes; re-selecting the current stage runs neither. `render` is what layout changes call: `scheduleLayoutRerender` draws only the active stage, since hidden stages are `display: none` and their canvases have no size, and every stage switch schedules a redraw. It draws once at the next frame however often it is asked; a `ResizeObserver` on the canvases and the channel grid asks whenever one changes size.

Loading a new raw file is **not** a stage exit. The edit slice is reset by `beginRawPreviewTransition` in `state/transitions.js` because the session changed; leaving the edit stage to look at QC keeps the loaded decomposition and its edits.

### Per-stage service factories

| Factory | File | Merged into `app` as |
|---|---|---|
| `createUiService` | services/ui.js | `UiService` |
| `createFileSessionService` | services/file-session.js | `FileSessionService` |
| `createQcStageService` | stages/qc-stage.js | `QcStage` |
| `createRunStageService` | stages/run-stage.js | `RunStage` |
| `createEditStageService` | stages/edit-stage.js | `EditStage` |

The import and layout stages have no service: `setupImportEvents` and `setupLayoutEvents` only attach listeners.

### Per-stage setup functions (event wiring)

| Setup | File | Listeners |
|---|---|---|
| `setupImportEvents` | import-stage.js | browseSignalBtn click, stepper chip clicks |
| `setupQcEvents` | qc-stage.js | auto-QC and artifact buttons, ROI drag on the overview, nwindows change, aux selector |
| `setupRunEvents` | run-stage.js | start, cancel and retry buttons, run settings toggles, the plan |
| `setupEditEvents` | edit-stage.js | all edit toolbar buttons, canvas bindings, keyboard |
| `setupLayoutEvents` | layout-stage.js | settings icon, section collapse, settings toggle, ESC |

---

## Stage Navigation

### `switchStage()` — `app/stages/lifecycle.js`

```
switchStage(app, target):
  reason = STAGES[target].blocked(app)
  if reason == "" -> return                      # silent refusal
  setSettingsOpen(false)
  if reason -> setStatus(reason), return
  if stage changes: STAGES[current].exit(app)
  setCurrentStage(state, target)
  toggle .active on each stage's panel
  if stage changes: STAGES[target].enter(app)
  updateStepAvailability()
  updateWorkflowStepper(target)
  scheduleLayoutRerender()                       # -> STAGES[target].render(app)
```

### `updateStepAvailability()` — `navigation.js`

```
stepRun.disabled  = !hasFile || !hasPreview
stepEdit.disabled = !hasEditData && (!hasFile || !hasRunResults)
```

### Allowed transitions

| From | To | Guard |
|---|---|---|
| import | qc | file loaded (preview succeeds) |
| import | edit | decomposition file loaded directly |
| qc | run | gridSeries exists (the preview's overview envelopes) |
| qc | edit | edit data loaded |
| run | edit | auto-transitioned after run completes |
| any | any (stepper click) | availability checks via `updateStepAvailability` |
