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
  1. wireEvents()
     ├─ layoutStage.ensureSettingsToggleIcon()
     ├─ setupImportEvents(importDeps)     # browse button, stepper clicks
     ├─ setupRunEvents(runDeps)           # start button, toggles, MU dropdowns
     ├─ setupEditEvents(editDeps)         # edit toolbar, canvas bindings, keyboard
     └─ setupLayoutEvents(layoutDeps)     # section collapse, settings overlay, ESC
  2. ui.updateStepAvailability()          # disable run/edit steps if no file
  3. ui.updateWorkflowStepper("import")  # highlight "Import" chip
  4. els.browseSignalBtn.disabled = true # block until backend responds
  5. ui.setStatus("Connecting to backend...")   # #status pill, top-right of header
  6. await waitForBackend(api.healthUrl())
     ├─ success → enable browse button, clear status
     └─ failure → "Backend unreachable — please restart the app"
```

### Construction Phase (module load, synchronous)

The `container.js` module body executes synchronously on import, constructing all service instances before `initializeApp` is called:

```
container.js module load:
  1. const api = createApiClient({ apiFetch, apiJson, API_BASE })
  2. Helper closures: nextFrame, updateStartAvailability, ensureDiscardMasks,
     getCurrentGrid, buildParams, applySessionInfoFromDecomposition,
     renderBidsAutoInfo, renderBidsMuscleFields, persistNpzBySaveTarget
  3. Late-bound forwarders: renderChannelQC, refreshVisuals, renderEditExplorer
     (qcStage/runStage/editStage not yet assigned — these use optional chaining)
  4. const ui = createUiService({ els, state, renderChannelQC, refreshVisuals,
     renderEditExplorer, setSelectedGrid })
  5. const fileSession = createFileSessionService({ els })
  6. Navigation helpers: getViewForStage, setViewForStage, adjustView, goToMu,
     handleKeyboardNavigation
  7. editStage = createEditStageService(editDeps)       # first (needs ui, fileSession)
  8. runStage  = createRunStageService(runDeps)         # second (needs qc forwarders + editStage)
  9. qcStage   = createQcStageService(qcDeps)           # third (needs runStage.renderMuExplorer)
 10. importStage = createImportStageService(importDeps) # needs qcStage + editStage
 11. layoutStage = createLayoutStageService(layoutDeps) # needs ui layout methods
```

The circular dependency between qc/run/edit stages is solved via **late-bound forwarder functions**:

```js
let qcStage, runStage, editStage;

function renderChannelQC(...args) { return qcStage?.renderChannelQC(...args); }
function refreshVisuals(...args)  { return qcStage?.refreshVisuals(...args); }
function renderEditExplorer(...args) { return editStage?.renderEditExplorer(...args); }
```

---

## Dependency Injection Pattern

Each stage follows a dual-export pattern:

```
┌─────────────────────────────────────────────────────┐
│  Factory: createXxxStageService(deps)                │
│    - Defines closures, builds one `ctx` bag          │
│    - Returns bag of methods                          │
│    - Called once at module load (container.js)       │
└─────────────────────────────────────────────────────┘
┌─────────────────────────────────────────────────────┐
│  Setup: setupXxxEvents(deps)                         │
│    - Destructures service methods + els + state      │
│    - Adds addEventListener calls to DOM elements     │
│    - Called once in wireEvents()                     │
└─────────────────────────────────────────────────────┘
```

### The shared context bag (`ctx`)

Inside `createQcStageService`, `createRunStageService`, `createEditStageService` and `createUiService`, every feature function receives the same object:

```js
const ctx = { ...deps, renderEditExplorer, requestRoiEdit, /* local helpers */ };
const removeOutliers = () => removeOutliersFeature(ctx);
```

`ctx` is built just before the factory's `return`, after every helper is defined; helpers only read it when called. Feature functions (`editing/operations.js`, `app/services/editing-service.js`, `view/*.js`, `signal/qc.js`, `decomp/run.js`, `navigation.js`, `layout.js`) destructure only the keys they use, so the superset is safe. Two rules keep it that way:

- A feature must not give a dependency a default value in its destructuring, and optional dependencies (checked with `if (dep)` / `dep?.()`) must be supplied by every factory whose features rely on them.
- A few features expect a dependency under a different name; the bag carries both: `refreshVisualsFn` (qc), `handleStreamMessageFn` and `onSaved` (run).

### `deps.js` — JSDoc typedef contracts

Defines explicit interfaces for each setup deps bundle:

| Typedef | Fields |
|---|---|
| `ImportSetupDeps` | els, state, handleNativeDialogOpen, setStatus, showWorkspace, switchStage, updateWorkflowStepper |
| `RunSetupDeps` | els, state, runDecomposition, enableRoiSelection, syncRois, refreshVisuals, setupToggle, setupLockedOnToggle, toggleConditional, updateStartAvailability, renderAuxiliaryChannels, renderMuExplorer, runAutoQc?, toggleArtifactMode?, removeLastArtifact? |
| `EditSetupDeps` | els, state, bindEditCanvas, bindEditDrCanvas, bindEditTimeline, renderEditExplorer, runEditAction, saveEditedFile, resetCurrentMuEdits, updateMuFilter, removeOutliers, flagMuForDeletion, duplicateMu, removeDuplicateMus, restoreEditBackup, setEditMode, refreshEditModeButtons, handleKeyboardNavigation, applyLabeledToggle |
| `LayoutSetupDeps` | els, toggleSettingsOpen, setSettingsOpen, initLayoutResizePolicy |
| `UiService` | setStatus, setEditStatus, setRunPhase, updateProgress, updateWorkflowStepper, updateStepAvailability, setSettingsOpen, toggleSettingsOpen, ensureSettingsToggleIcon, initLayoutResizePolicy, scheduleLayoutRerender, showWorkspace, switchStage, setupToggle, setupLockedOnToggle, toggleConditional, isToggleOn, runEditAction |
| `FileSessionService` | getBidsProject, getBidsMuscleNames, clearUploadFormatError, showUnsupportedUploadFormatError, detectLandingFileType, setUploadLoading |
| `QcStageService` | 12 methods |
| `RunStageService` | 6 methods |
| `EditStageService` | 28 methods |

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
             │           │           │
             v           │           │
        ┌──────────┐     │           │
        │ apiClient│     │           │
        └────┬─────┘     │           │
             │           │           │
    ┌────────┼───────────┼───────────┤
    │        v           v           v
    │   ┌──────────────────────────────────┐
    │   │     container.js (composition)   │
    │   │                                  │
    │   │  ┌─────┐  ┌──────────┐           │
    │   │  │ ui  │  │fileSession│          │
    │   │  └──┬──┘  └─────┬────┘          │
    │   │     │           │               │
    │   │  ┌──┴──┐  ┌─────┴────┐  ┌──────┴────┐  ┌──────────┐  ┌────────┐
    │   │  │edit │  │  run     │  │   qc      │  │ import   │  │ layout │
    │   │  │stage│  │  stage   │  │   stage   │  │ stage    │  │ stage  │
    │   │  └─────┘  └──────────┘  └───────────┘  └──────────┘  └────────┘
    │   └──────────────────────────────────┘
    │
    └──> wireEvents() -> setupXxxEvents() for each stage
```

### Thunks for Late Binding

Many deps are passed as zero-arg thunks to break circular construction:

```js
renderChannelQC: () => qcStage.renderChannelQC(...args)
requestQcGridWindow: (...args) => qcStage.requestQcGridWindow(...args)
loadDecompositionForEditByPath: (...args) => editStage.loadDecompositionForEditByPath(...args)
```

---

## State Model

### Global state (`state.js`)

```javascript
{
  // Import / QC / Run shared
  file, uploadToken, isRunning, seriesLength,
  rois: [], roiDraft, previewSeries,
  artifactRegions: [], artifactDraft, artifactMode,
  gridNames: [], gridSeries: [], gridColors,
  channelMeans: [], coordinates: [],
  discardMasks: [], channelTraces: [],
  qcWindowLoading: {},
  metadata: {}, muscle: [],
  currentStage: "qc", currentGrid: 0,
  fsamp, auxSeries: [], auxNames: [],
  parameters,

  // Run stage (directly on global state)
  muPulseTrains, muDistimes, muGridIndex,
  currentMuGrid, currentMu,
  runView,
  runDownloadInFlight, lastRunDownloadKey,

  // Edit stage (nested slice)
  edit: createEditSlice(),
}
```

### Per-stage edit slice (`state.edit`)

```javascript
{
  file, filename,
  pulseTrains: [],           // current (possibly edited) pulse trains
  originalPulseTrains: [],    // pristine copies for reset
  distimes: [],               // discharge times per MU
  originalDistimes: [],
  artifactTimes: [],
  gridNames: [], muGridIndex: [],
  fsamp, totalSamples,
  currentMuGrid, currentMu,
  view: { start, end },
  selectionPulse, selectionDr,
  draftSelectionPulse, draftSelectionDr,
  mode,                       // "add" | "add_artifact" | "delete_spikes" | "delete_dr" | null
  dirty,
  parameters,
  flagged: [],                // per-MU deletion flags
  backup,                      // single-level undo backup
  bidsRoot, project,
  editSignalToken,
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
│    file, rois, previewSeries, gridNames,     │
│    gridSeries, channelMeans, coordinates,    │
│    discardMasks, artifactRegions, artifactMode,│
│    currentGrid, fsamp,                         │
│    muPulseTrains, muDistimes, muGridIndex,   │
│    currentMuGrid, currentMu, runView         │
│                                             │
│  ┌─────────────────────────────────────┐    │
│  │        state.edit (per-stage)       │    │
│  │                                     │    │
│  │  Edit-only:                          │    │
│  │    pulseTrains, originalPulseTrains, │    │
│  │    distimes, originalDistimes,       │    │
│  │    artifactTimes, mode, dirty,        │    │
│  │    flagged, backup, editHistory,     │    │
│  │    bookmarkPosition, muUids, view    │    │
│  └─────────────────────────────────────┘    │
└─────────────────────────────────────────────┘
```

The run stage uses `state.muPulseTrains` / `state.currentMu` / `state.runView` directly on the global state. The edit stage uses `state.edit.pulseTrains` / `state.edit.currentMu` / `state.edit.view` on the nested slice. This prevents run and edit from clobbering each other's view state.

---

## Stage Registration and Lifecycle

Stages do **not** have `init`/`enter`/`exit`/`destroy` methods. Instead:

1. **Construction** — `createXxxStageService(deps)` returns a bag of closures. Called once at module load.
2. **Event wiring** — `setupXxxEvents(deps)` attaches DOM listeners. Called once in `wireEvents()`.
3. **Stage activation** — Pure CSS class toggling via `switchStage()`. No enter/exit callbacks.
4. **Re-rendering** — `scheduleLayoutRerender()` triggers plot redraws on stage switch and resize.

### Per-stage service factories

| Factory | File | Returns |
|---|---|---|
| `createImportStageService` | import-stage.js | `{ handleNativeDialogOpen }` |
| `createQcStageService` | qc-stage.js | 12 methods |
| `createRunStageService` | run-stage.js | 6 methods |
| `createEditStageService` | edit-stage.js | 28 methods |
| `createLayoutStageService` | layout-stage.js | 4 methods |
| `createUiService` | services/ui.js | 21 methods |
| `createFileSessionService` | file-session.js | 9 methods |
| `createApiClient` | api/client.js | 11 methods |

### Per-stage setup functions (event wiring)

| Setup | File | Listeners |
|---|---|---|
| `setupImportEvents` | import-stage.js | browseSignalBtn click, stepper chip clicks |
| `setupRunEvents` | run-stage.js | start click, nwindows change, toggles, dropdowns |
| `setupEditEvents` | edit-stage.js | all edit toolbar buttons, canvas bindings, keyboard |
| `setupLayoutEvents` | layout-stage.js | section collapse, settings toggle, ESC |

---

## Stage Navigation

### `switchStage()` — `navigation.js`

```
switchStage(deps, target):
  guard: if no file and target != "edit" -> return (no-op)
  guard: if target == "run" and no previewSeries -> setStatus("locked"), return
  guard: if target == "edit" and no edit.distimes -> "Load a decomposition file"
  setCurrentStage(state, target)
  toggle .active on stageQc/stageRun/stageEdit
  updateStepAvailability()
  updateWorkflowStepper(target)
  scheduleLayoutRerender(0)
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
| qc | run | previewSeries exists |
| qc | edit | edit data loaded |
| run | edit | auto-transitioned after run completes |
| any | any (stepper click) | availability checks via `updateStepAvailability` |
