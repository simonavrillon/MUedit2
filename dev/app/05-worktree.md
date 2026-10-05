# 05 - Worktree: User-Exposed vs App-Internal

This document maps every module, showing what is **exposed to the user** (UI elements, buttons, canvases, form fields) versus what is **used by the app internally** (functions, state, API calls). Use this to identify dead code or unreachable branches.

Legend:
- **[U]** = User-exposed (the user can see or interact with this)
- **[A]** = App-internal (used by the app, not directly visible to the user)
- **[?]** = Not called at runtime (documentation only)

---

## `index.html` — DOM Elements

All elements are registered in `dom.js` as `els.*` properties.

### Import / Landing

| Element ID | [U/A] | Purpose |
|---|---|---|
| `browseSignalBtn` | [U] | Button to open file dialog |
| `landing` | [U] | Landing page container |
| `workspace` | [U] | Main workspace container |
| `uploadLoader` | [U] | Upload spinner |
| `uploadFormatError` | [U] | Format error message |
| `fileName` | [U] | Display loaded filename |
| `status` | [U] | Global status pill, top right of the header (hidden when empty) |
| `stepImport` | [U] | Stepper: Import |
| `stepQc` | [U] | Stepper: QC |
| `stepRun` | [U] | Stepper: Decompose |
| `stepEdit` | [U] | Stepper: Edit |

### Settings Panel (shared across stages)

| Element ID | [U/A] | Purpose |
|---|---|---|
| `settingsToggleBtn` | [U] | Settings panel toggle (inside the panel, on the rail) |
| `settingsOverlay` | [U] | Settings overlay backdrop |
| `settingsPanel` | [U] | Settings panel container |
| `bidsProject` | [U] | BIDS project input |
| `bidsSubject` | [U] | BIDS subject input |
| `bidsSession` | [U] | BIDS session input |
| `bidsAcquisition` | [U] | BIDS acquisition input |
| `bidsRun` | [U] | BIDS run input |
| `bidsTask` | [U] | BIDS task input (with datalist) |
| `bidsParticipantAge` | [U] | Participant age input |
| `bidsParticipantSex` | [U] | Participant sex select |
| `bidsParticipantHandedness` | [U] | Participant handedness select |
| `bidsMuscleContainer` | [U] | Muscle name inputs container (dynamic) |
| `bidsPlacementScheme` | [U] | Placement scheme select |
| `bidsPlacementDescription` | [U] | Placement description input |
| `bidsPlacementDescRow` | [U] | Placement description row (hidden by default) |
| `bidsManufacturer` | [U] | Manufacturer input |
| `bidsDeviceModel` | [U] | Device model input |
| `fsamp` | [U] | Sampling frequency (readonly) |
| `bidsPowerlineFreq` | [U] | Powerline frequency select |
| `bidsAutoInfo` | [U] | Auto-detected metadata box |
| `niter` | [U] | Decomposition iterations |
| `nwindows` | [U] | Analysis window count |
| `duplicatesthresh` | [U] | Duplicate threshold |
| `peelOffToggle` | [U] | Peel-off toggle |
| `peelOffSettings` | [U] | Peel-off settings container |
| `peelOffWindow` | [U] | Peel-off window (ms) |
| `postprocessMode` | [U] | Post-processing mode select |
| `postprocessModeHint` | [U] | Post-processing hint text |
| `silToggle` | [U] | SIL filter toggle (locked ON) |
| `silSettings` | [U] | SIL settings container |
| `silValue` | [U] | SIL threshold |
| `covToggle` | [U] | CoV filter toggle |
| `covSettings` | [U] | CoV settings container |
| `covValue` | [U] | CoV threshold |

### QC Stage

| Element ID | [U/A] | Purpose |
|---|---|---|
| `stageQc` | [U] | QC stage container |
| `qcGridTabs` | [U] | Dynamic grid tab buttons |
| `startBtn` | [U] | "Decompose Signal" button |
| `qcAutoBtn` | [U] | Run automatic QC (bad channels + artifact regions) |
| `artifactAddBtn` | [U] | Toggle artifact-region drawing on the EMG overview |
| `artifactRemoveBtn` | [U] | Remove the last artifact region |
| `artifactCount` | [U] | Number of artifact regions |
| `qcSection` | [U] | Channel quality grid container |
| `emgCanvas` | [U] | EMG overview canvas + ROI selection |
| `auxSelector` | [U] | Auxiliary channel selector |
| `auxCanvas` | [U] | Auxiliary channel canvas |

### Run Stage

| Element ID | [U/A] | Purpose |
|---|---|---|
| `stageRun` | [U] | Run stage container (`data-mode` pre / live / result picks what shows) |
| `runPlan` | [U] | Pre-run plan: grids, windows, iterations, the filters |
| `runStartBtn` | [U] | "Start decomposition" button (pre mode) |
| `runPhases` | [U] | Phase track: Load → Filter → Decompose → Post-process → Save |
| `runCount` | [U] | Units kept so far, then the summary's final count |
| `runCountLabel` | [U] | "motor unit(s) found" / "kept" |
| `runCountGrids` | [U] | Per-grid counts (multi-grid runs) |
| `runElapsed` | [U] | Elapsed clock ("Took …" once finished) |
| `runEta` | [U] | Time-left estimate, projected from the search's pace |
| `runDots` | [U] | One row per grid × window, one dot per iteration |
| `runError` | [U] | Failure message (hidden unless the run failed) |
| `runResultStats` | [U] | Result stats: found vs kept, mean silhouette |
| `runSaveText` | [U] | The save's outcome (saving / saved path / failure) |
| `runRetrySaveBtn` | [U] | Retry the save after a failure |
| `runAgainBtn` | [U] | Run again with the current settings |
| `cancelRunBtn` | [U] | Cancel the running decomposition (shown while it runs) |

### Edit Stage

| Element ID | [U/A] | Purpose |
|---|---|---|
| `stageEdit` | [U] | Edit stage container |
| `editMuGridSelect` | [U] | Grid dropdown (edit) |
| `editMuSelect` | [U] | MU dropdown (edit) |
| `editFlagBtn` | [U] | Flag MU button |
| `editDeduplicateBtn` | [U] | Remove Duplicates button |
| `editDuplicateBtn` | [U] | Duplicate MU button |
| `editOutliersBtn` | [U] | Remove Outliers button |
| `editAddBtn` | [U] | Add Spike button |
| `editAddArtifactBtn` | [U] | Add Artifact button |
| `editDeleteSpikeBtn` | [U] | Delete Spike/Artifact button |
| `editUpdateBtn` | [U] | Update Filter button |
| `editPeelOffToggle` | [U] | Peel-off toggle (edit) |
| `editLockSpikesToggle` | [U] | Lock spikes toggle (edit) |
| `editUndoBtn` | [U] | Undo button |
| `editResetBtn` | [U] | Reset button |
| `editSaveBtn` | [U] | Save button |
| `editPulseCanvas` | [U] | Pulse train canvas (edit) |
| `editDrCanvas` | [U] | Discharge rate canvas (edit) |
| `editTimelineCanvas` | [U] | Navigation timeline canvas (edit) |
| `editStatus` | [U] | Edit status pill |

---

## Module: `app/container.js` (Entry)

### User-exposed: none (internal wiring)

| Function | [A/?] | Called By |
|---|---|---|
| `initializeApp` | [A] | app.js (entry) |

---

## Module: `app/create-app.js`

| Function | [A/?] | Called By |
|---|---|---|
| `createApp` | [A] | initializeApp; tests/lifecycle.test.js |

---

## Module: `app/context.js`

| Export | [A/?] | Used By |
|---|---|---|
| `App`, `Core`, `UiService`, `FileSessionService`, `QcStage`, `RunStage`, `EditStage`, `StageKey` typedefs | [?] | Every `@param {App} app`; enforced by `npm run typecheck`, not at runtime |

---

## Module: `state/state.js`

### User-exposed: none

### App-internal

| Export | [A/?] | Used By |
|---|---|---|
| `createEditSlice` | [A] | state initialization, transitions, resetEditSlice |
| `state` | [A] | container.js (the one app state) |

---

## Module: `app/dom.js`

### User-exposed: none (but registers all DOM elements)

### App-internal

| Export | [A] | Used By |
|---|---|---|
| `els` (default export) | [A] | nearly all modules |

---

## Module: `app/http.js`

| Export | [A/?] | Used By |
|---|---|---|
| `apiFetch` | [A] | api/client.js; sends `X-MUedit-Session` |
| `apiJson` | [A] | api/client.js |
| `SESSION_ID`, `SESSION_HEADER` | [A] | createApiClient (`closeSession`), apiFetch |
| `waitForBackend` | [A] | initializeApp |
| `parseApiError` | [A] | http.js (internal) |

---

## Module: `app/services/navigation.js`

| Function | [A/?] | Called By |
|---|---|---|
| `setStatus` | [A] | ui.js |
| `updateWorkflowStepper` | [A] | ui.js |
| `positionStepIndicator` | [A] | updateWorkflowStepper, layout.js (resize/orientation) |
| `showWorkspace` | [A] | ui.js |
| `updateStepAvailability` | [A] | ui.js |
| `populateGridTabs` | [A] | ui.js |
| `adjustView` (private) | [A] | handleKeyboardNavigation (arrows) |
| `goToMu` (private) | [A] | handleKeyboardNavigation (`<` / `>`) |
| `handleKeyboardNavigation` | [A] | setupEditEvents (window keydown, keys no command takes) |

---

## Module: `app/stages/lifecycle.js`

| Export | [A/?] | Called By |
|---|---|---|
| `STAGES` | [A] | switchStage, renderActiveStage |
| `switchStage` | [A] | ui.js (`app.switchStage`: stepper, showWorkspace, requestPreview, runDecomposition, showEditSession) |
| `renderActiveStage` | [A] | scheduleLayoutRerender |

---

## Module: `app/services/ui.js`

| Function | [A/?] | Called By |
|---|---|---|
| `setStatus` | [A] | all stages |
| `setEditStatus` | [A] | editing-service, edit-stage |
| `updateWorkflowStepper` | [A] | switchStage, import-stage |
| `updateStepAvailability` | [A] | switchStage, initializeApp |
| `setSettingsOpen` | [A] | switchStage, showWorkspace, setupLayoutEvents |
| `scheduleLayoutRerender` | [A] | switchStage, setSettingsOpen, canvas resize (ResizeObserver) |
| `showWorkspace` | [A] | qc.js, run.js, editing-service, import-stage stepper |
| `switchStage` | [A] | see `lifecycle.js` |
| `populateGridTabs` | [A] | showWorkspace, requestPreview |

---

## Module: `app/services/layout.js`

| Function | [A/?] | Called By |
|---|---|---|
| `setSettingsOpen` | [A] | ui.js |
| `toggleSettingsOpen` | [A] | setupLayoutEvents (settingsToggleBtn) |
| `ensureSettingsToggleIcon` | [A] | setupLayoutEvents |
| `initLayoutResizePolicy` | [A] | setupLayoutEvents |

---

## Module: `app/services/file-session.js`

| Function | [A/?] | Called By |
|---|---|---|
| `getBidsProject` | [A] | runDecomposition (FormData), withBidsSaveFields |
| `getBidsMuscleNames` | [A] | autoSaveRunDecomposition, saveEditedFile, readSessionForm |
| `setUploadLoading` | [A] | import-stage, qc.js requestPreview, loadDecompositionForEdit |
| `readSessionForm` (private) | [A] | collectBidsEntities, withBidsSaveFields (one reading of the form, labels cleaned as in BIDS, so a run's export and its save agree) |
| `withBidsSaveFields` | [A] | persistNpzBySaveTarget, saveEditedFile |
| `collectBidsEntities` | [A] | runDecomposition (FormData) |
| `setBidsEntitiesInput` | [A] | import-stage (BDF/EDF entity label) |
| `applyPreviewMetadata` | [A] | qc.js requestPreview |
| `applySessionInfoFromDecomposition` | [A] | loadDecompositionForEdit |
| `renderBidsAutoInfo` | [A] | qc.js requestPreview, run.js applyPreviewData |
| `renderBidsMuscleFields` | [A] | qc.js requestPreview, run.js applyPreviewData, loadDecompositionForEdit |
| `persistNpzBySaveTarget` | [A] | autoSaveRunDecomposition (the run save, `POST /edit/save`) |

---

## Module: `app/services/editing-service.js`

| Function | [A/?] | Called By |
|---|---|---|
| `requestEditOp` | [A] | every action below: `POST /edit/ops/{op}` + `applyEditChange` |
| `editAction` (private) | [A] | every action below: status, redraw, outcome, failure |
| `requestRoiEdit` | [A] | operations.js (addSpikes, addArtifact, deleteSpikes, deleteDr) |
| `requestFilterUpdate` | [A] | EDIT_COMMANDS (update button, Space) |
| `removeOutliers` | [A] | EDIT_COMMANDS (outliers button, R) |
| `removeDuplicateMus` | [A] | EDIT_COMMANDS |
| `flagMuForDeletion` | [A] | EDIT_COMMANDS |
| `undoEdit` | [A] | EDIT_COMMANDS (undo button) |
| `resetCurrentMuEdits` | [A] | EDIT_COMMANDS (reset button) |
| `duplicateMu` | [A] | EDIT_COMMANDS (duplicate button) |
| `saveEditedFile` | [A] | EDIT_COMMANDS (save button) |
| `loadDecompositionForEdit` | [A] | edit-stage loadDecompositionForEditByPath |
| `restoreEditSession` | [A] | initializeApp, after the backend answers |
| `showEditSession` (private) | [A] | loadDecompositionForEdit, restoreEditSession |
| `resumePosition` (private) | [A] | showEditSession |
| `confirmRecovery` (private) | [A] | loadDecompositionForEdit |
| `setEditBookmarkAndHide` (private) | [A] | requestRoiEdit, requestFilterUpdate, removeOutliers |
| `rememberSessionToken`, `rememberedSessionToken`, `forgetSessionToken` (private) | [A] | the session token in `sessionStorage` |

---

## Module: `app/services/view-fetcher.js`

| Function | [A/?] | Called By |
|---|---|---|
| `createViewFetcher` | [A] | edit-stage (`/series/pulse` for the edit canvas): one request in flight, only the latest window waits |

---

## Module: `app/services/error-service.js`

| Function | [A/?] | Called By |
|---|---|---|
| `handleError` | [A] | editing-service, signal/qc.js, decomp/run.js |

---

## Module: `app/stages/import-stage.js`

| Function | [A/?] | Called By |
|---|---|---|
| `handleNativeDialogOpen` (private) | [A] | setupImportEvents (browseSignalBtn click) |
| `displayNameForPath` (private) | [A] | handleNativeDialogOpen |
| `detectLandingFileType` (private) | [A] | handleNativeDialogOpen |
| `clearUploadFormatError`, `showUnsupportedUploadFormatError` (private) | [A] | handleNativeDialogOpen |
| `setupImportEvents` | [A] | initializeApp |

---

## Module: `app/stages/qc-stage.js`

| Function | [A/?] | Called By |
|---|---|---|
| `createQcStageService` | [A] | createApp |
| `populateAuxSelector` | [A] | requestPreview, applyPreviewData |
| `renderAuxiliaryChannels` | [A] | refreshVisuals, setupQcEvents (aux selector) |
| `ensureQcTraces` | [A] | renderChannelQC, enableRoiSelection (ROI commit) |
| `handleRawFilePath` | [A] | import-stage (handleNativeDialogOpen) |
| `renderChannelQC` | [A] | QC stage render, setSelectedGrid, requestAutoQc, ensureQcTraces (traces arrived, QC on screen) |
| `enableRoiSelection` | [A] | requestPreview, setupQcEvents |
| `refreshVisuals` | [A] | QC stage render, ROI drags, auto-QC |
| `scheduleRefreshVisuals` | [A] | ROI and artifact drags (once per frame) |
| `setSelectedGrid` | [A] | populateGridTabs (grid tab click) |
| `toggleArtifactMode`, `removeLastArtifact` (private) | [A] | setupQcEvents (artifact + / − buttons) |
| `setupQcEvents` | [A] | initializeApp: auto-QC and artifact buttons, ROI drag, nwindows change, aux selector |

---

## Module: `app/stages/run-stage.js`

| Function | [A/?] | Called By |
|---|---|---|
| `createRunStageService` | [A] | createApp |
| `renderRunStage` | [A] | switchStage/render (lifecycle), runDecomposition, handleStreamMessage, autoSaveRunDecomposition, setupRunEvents (settings change) |
| `scheduleRunRender` | [A] | handleStreamMessage (progress events, once per frame) |
| `autoSaveRunDecomposition` | [A] | handleStreamMessage (on done), setupRunEvents (runRetrySaveBtn) |
| `handleStreamMessage` | [A] | runDecomposition (stream loop) |
| `updateStartAvailability` | [A] | handleRawFilePath, runDecomposition, setupRunEvents |
| `buildParams` | [A] | runDecomposition, renderRunStage (the plan) |
| `setupRunEvents` | [A] | initializeApp: start, cancel and retry buttons, run settings toggles |

---

## Module: `app/stages/edit-stage.js`

| Function | [A/?] | Called By |
|---|---|---|
| `createEditStageService` | [A] | createApp |
| `refreshEditModeButtons` | [A] | setEditMode, requestEditOp, saveEditedFile, resetEditState |
| `setEditMode` | [A] | EDIT_COMMANDS (mode buttons, a/d/x), Edit stage exit |
| `getPulsePlotHeight`, `getDrPlotHeight` | [A] | operations.js (drawn box → values) |
| `ensureEditPulseView` | [A] | edit-canvas.renderEditExplorer: fetch the window on screen when it changed |
| `resetEditState` | [A] | loadDecompositionForEdit (on error) |
| `renderEditDropdowns` | [A] | renderEditExplorer (rebuilt only when what they show changes) |
| `renderEditExplorer` | [A] | Edit stage render, every edit action |
| `scheduleEditRender` | [A] | pulse, rate and timeline drags (once per frame) |
| `requestRoiEdit` | [A] | operations.js |
| `addSpikesInSelection`, `addArtifactInSelection`, `deleteSpikesInSelection` | [A] | bindEditCanvas (pointerup in the armed mode) |
| `deleteDrInSelection` | [A] | bindEditDrCanvas (pointerup) |
| `loadDecompositionForEditByPath` | [A] | import-stage, autoSaveRunDecomposition (preloads the saved run with `{ open: false }`) |
| `EDIT_COMMANDS` | [A] | setupEditEvents: each toolbar button and its key run the same command |
| `setupEditEvents` | [A] | initializeApp: canvas bindings, EDIT_COMMANDS on clicks and keys, then view keys |

---

## Module: `app/stages/layout-stage.js`

| Function | [A/?] | Called By |
|---|---|---|
| `setupLayoutEvents` | [A] | initializeApp |

---

## Module: `api/client.js`

| Function | [A/?] | Called By |
|---|---|---|
| `createApiClient` | [A] | initializeApp |
| `postJson` (internal) | [A] | runAutoQc, fetchPreviewByPath, cancelDecomposition, editSessionSave, editSave |
| `postForSession` (internal) | [A] | editOpen, editRecover, editOp (decodes the MUB1 session frame) |
| `fetchSeries` | [A] | qc.createQcTraces (`emg`), qc-stage.requestPreview (`overview`, `aux`) |
| `fetchPreviewByPath` | [A] | qc-stage.requestPreview, decomp/run.js (token re-mint on expiry) |
| `runAutoQc` | [A] | signal/qc.requestAutoQc |
| `decomposeStream` | [A] | run-stage.runDecomposition |
| `cancelDecomposition` | [A] | run-stage.cancelDecomposition (cancelRunBtn) |
| `fetchPulse` | [A] | edit-stage.ensureEditPulseView (through createViewFetcher) |
| `openFileDialog` | [A] | import-stage.handleNativeDialogOpen (browser only, through platform.openFile) |
| `closeSession` | [A] | initializeApp (pagehide) |
| `editOpen` | [A] | editing-service.loadDecompositionForEdit |
| `editRecover` | [A] | editing-service.loadDecompositionForEdit |
| `editSessionState` | [A] | editing-service.restoreEditSession |
| `editOp` | [A] | editing-service.requestEditOp |
| `editSessionSave` | [A] | editing-service.saveEditedFile |
| `editSave` | [A] | file-session.persistNpzBySaveTarget (the run save) |
| `healthUrl` | [A] | initializeApp (waitForBackend) |

---

## Module: `api/routes.js`

| Export | [A] | Used By |
|---|---|---|
| `routes` | [A] | api/client.js |

---

## Module: `api/ndjson.js`

| Export | [A] | Used By |
|---|---|---|
| `readNdjson` | [A] | runDecomposition (the run's progress stream, line by line; malformed lines skipped and counted) |

---

## Module: `api/payloads.js`

| Function | [A/?] | Called By |
|---|---|---|
| `toFiniteNumber` | [A] | normalizePreviewPayload |
| `toSpikeArray` | [A] | normalizePreviewPayload (JSON discharge times → `Int32Array`) |
| `toSpans` | [A] | normalizePreviewPayload, signal/qc.js (artifact regions) |
| `normalizePreviewPayload` | [A] | decomp/run.js handleStreamMessage |

---

## Module: `api/binary-payloads.js`

| Function | [A/?] | Called By |
|---|---|---|
| `hasMagic` | [A] | decodeFrame |
| `frameAligned`, `frameCount` | [A] | decodeFrame, encodeFrame |
| `decodeFrame` | [A] | every decoder below |
| `encodeFrame` | [?] | tests only (builds the decoders' input) |
| `csrRows` | [A] | decodeEditSessionFrame |
| `decodeEditSessionFrame` | [A] | editOpen, editRecover, editOp, editSessionState |
| `frameRowViews` (private) | [A] | decodeSeriesFrame, decodePulseFrame |
| `decodeSeriesFrame` | [A] | fetchSeries |
| `decodePulseFrame` | [A] | fetchPulse |

---

## Module: `decomp/live.js`

| Function | [A/?] | Called By |
|---|---|---|
| `createRunLive` | [A] | decomp/run.js runDecomposition |
| `applyRunEvent` | [A] | handleStreamMessage (folds one stream event into `runLive`) |
| `keptTotal` | [A] | view/run-live.js (the counter) |
| `searchSecondsLeft` | [A] | view/run-live.js (the ETA) |
| `formatClock`, `formatRemaining` | [A] | view/run-live.js (the clock) |
| `buildRunSummary` | [A] | handleStreamMessage (the done event) |
| `buildRunPlan` | [A] | run-stage.renderRunStage (the pre-run plan) |
| `RUN_PHASES`, `DOT` | [A] | view/run-live.js (the phase track, the dots' codes) |

---

## Module: `decomp/params.js`

| Export | [A/?] | Called By |
|---|---|---|
| `postprocessFlags` | [A] | buildDecomposeParams |
| `buildDecomposeParams` | [A] | run-stage buildParams |
| `POSTPROCESS_MODES` | [A] | run-stage setup |
| `DEFAULT_POSTPROCESS_MODE` | [A] | run-stage setup |

---

## Module: `decomp/run.js`

| Function | [A/?] | Called By |
|---|---|---|
| `autoSaveRunDecomposition` | [A] | run-stage.autoSaveRunDecomposition |
| `runDecomposition` | [A] | setupRunEvents (startBtn, runStartBtn, runAgainBtn) |
| `cancelDecomposition` | [A] | setupRunEvents (cancelRunBtn) |
| `applyPreviewData` (private) | [A] | handleStreamMessage |
| `failRun` (private) | [A] | runDecomposition, handleStreamMessage |
| `handleStreamMessage` | [A] | run-stage.handleStreamMessage |

---

## Module: `editing/operations.js`

| Export | [A/?] | Called By |
|---|---|---|
| `getPulseViewMeta` | [A] | edit-stage.getPulseViewMeta |
| `buildEditDropdownModel` | [A] | edit-stage.renderEditDropdowns |
| `resetEditState` | [A] | edit-stage.resetEditState |
| `dischargeRates` | [A] | edit-canvas.renderInstantaneousDr, deleteDrInSelection |
| `fastestRateInView` | [A] | edit-canvas.renderInstantaneousDr, deleteDrInSelection |
| `pulseBox` (private) | [A] | addSpikesInSelection, addArtifactInSelection, deleteSpikesInSelection |
| `addSpikesInSelection` | [A] | edit-stage.addSpikesInSelection |
| `addArtifactInSelection` | [A] | edit-stage.addArtifactInSelection |
| `deleteSpikesInSelection` | [A] | edit-stage.deleteSpikesInSelection |
| `deleteDrInSelection` | [A] | edit-stage.deleteDrInSelection |
| `clampView` | [A] | edit-canvas timeline drag and click, navigation.adjustView |

---

## Module: `io/bids.js`

| Function | [A/?] | Called By |
|---|---|---|
| `safeBidsToken` | [A] | buildEntityLabelFromSession |
| `buildEntityLabelFromSession` | [A] | persistNpzBySaveTarget |
| `getSuggestedNpzName` | [A] | autoSaveRunDecomposition, saveEditedFile |
| `parseBidsEntitiesFromLabel` | [A] | buildSessionInfoFromDecomposition |
| `listifyMuscles` | [A] | buildBidsMuscleRowsModel |
| `buildBidsAutoInfoModel` | [A] | renderBidsAutoInfo |
| `buildBidsMuscleRowsModel` | [A] | renderBidsMuscleFields |
| `naToEmpty` | [A] | applySessionInfoToDom |
| `buildSessionInfoFromDecomposition` | [A] | applySessionInfoFromDecomposition |

---

## Module: `io/grid.js`

| Function | [A/?] | Called By |
|---|---|---|
| `safeNonNegativeInt` | [A] | inferGridCount, normalizeGridNames |
| `inferGridCount` | [A] | showEditSession |
| `normalizeGridNames` | [A] | showEditSession |
| `gridDimensionsFor` | [A] | renderChannelQC |

---

## Module: `signal/qc.js`

| Function | [A/?] | Called By |
|---|---|---|
| `syncRois` | [A] | setupQcEvents (nwindows change), qc-renderer (ROI drag) |
| `pickRoiSlot` | [A] | qc-renderer (an ROI drag picks the window it replaces) |
| `requestAutoQc` | [A] | setupQcEvents (qcAutoBtn click) |
| `createQcTraces` | [A] | qc-stage (`ensureQcTraces`: latest window wins, per grid) |
| `requestPreview` | [A] | qc-stage.handleRawFilePath |

---

## Module: `signal/series.js`

| Function | [A/?] | Called By |
|---|---|---|
| `isValues`, `isEnvelope`, `seriesPoints`, `seriesRange` | [A] | plots.js, qc-renderer.renderAuxiliaryChannels (envelopes and samples) |
| `traceRange` | [A] | plots.drawTrace, operations.getPulseViewMeta |

---

## Module: `state/actions.js`

All functions are state mutators (`set*` functions). Each is called by at least one stage service or feature module. There are ~40+ exported functions. Key categories:

- `setFile`, `setUploadToken`, `setSeriesLength`, `setRois`, `setRoiForIndex`, `setRoiDraft`
- `setGridSeries`, `setGridNames`, `setChannelMeans`, `setCoordinates`, `setChannelTraces`, `setChannelTraceForGrid`
- `setMetadata`, `setMuscle`, `setFsamp`, `setAuxData`
- `setDiscardMaskChannel`, `setDiscardMasks`, `ensureDiscardMasks`
- `setArtifactMode`, `setArtifactDraft`, `setArtifactRegions`, `addArtifactRegion`, `removeLastArtifactRegion`
- `setParameters`, `setIsRunning`
- `setRunLive`, `setRunResultToken`, `setRunDownloadInFlight`, `setLastRunDownloadKey`
- `setCurrentStage`, `setCurrentGrid`
- `clearPreviewState`
- Edit slice: `setEditMode`, `setEditCurrentMuGrid`, `setEditCurrentMu`, `setEditProject`, `setEditView`, `setEditFile`, `setEditFilename`, `setEditGridNames`, `setEditBookmark`, `setShowBookmark`, `setEditPulseSelection`, `setEditPulseDraftSelection`, `setEditDrSelection`, `setEditDrDraftSelection`, `clearEditPulseSelections`, `clearEditDrSelections`, `clearAllEditSelections`, `setEditPulseView`, `setEditSoftwareVersions`, `resetEditSlice`
- Edit session mirror: `setEditSession` (a state frame: the whole session), `applyEditChange` (a change frame), `applyEditSave` (a session save), `keepEditMus` (keep MUs by index across every per-MU array)
---

## Module: `state/selectors.js`

| Function | [A/?] | Called By |
|---|---|---|
| `getCurrentGrid` | [A] | qc.js, run.js, qc-renderer |
| `roiStart` | [A] | qc-renderer, plots, qc.js |
| `roiEnd` | [A] | qc-renderer, plots, qc.js |
| `muIndicesForGrid` (private) | [A] | getEditMuIndicesForGrid |
| `getEditMuIndicesForGrid` | [A] | edit-stage.getEditMuIndices |

---

## Module: `state/transitions.js`

| Function | [A/?] | Called By |
|---|---|---|
| `beginRawPreviewTransition` | [A] | qc-stage.handleRawFilePath |
| `rollbackRawPreviewTransition` | [A] | qc-stage (on preview failure) |

---

## Module: `view/bids-renderer.js`

| Function | [A/?] | Called By |
|---|---|---|
| `makeInfoItem` | [A] | renderBidsAutoInfo |
| `resetBidsEntityDefaults` | [A] | qc-stage.handleRawFilePath |
| `applyParticipantFields` | [A] | applySessionInfoToDom |
| `renderBidsAutoInfo` | [A] | file-session renderBidsAutoInfo |
| `renderBidsMuscleFields` | [A] | file-session renderBidsMuscleFields |
| `applySessionInfoToDom` | [A] | applySessionInfoFromDecomposition |

---

## Module: `view/edit-canvas.js`

| Function | [A/?] | Called By |
|---|---|---|
| `pxToViewSample` (private) | [A] | the canvas bindings |
| `renderBookmark` (private) | [A] | renderEditExplorer |
| `clampY` (private) | [A] | bindEditCanvas, bindEditDrCanvas |
| `createDragState` (private) | [A] | bindEditCanvas, bindEditDrCanvas |
| `displayedPulse` (private) | [A] | renderEditExplorer (a flagged MU as a flat line) |
| `fillTicks` (private) | [A] | renderEditTimeline |
| `renderEditDropdownsView` | [A] | edit-stage.renderEditDropdowns |
| `renderEditExplorer` | [A] | edit-stage.renderEditExplorer |
| `renderInstantaneousDr` | [A] | edit-stage.renderInstantaneousDr |
| `bindEditCanvas` | [A] | edit-stage.bindEditCanvas |
| `renderEditTimeline` | [A] | edit-stage.renderEditExplorer |
| `bindEditTimeline` | [A] | edit-stage.bindEditTimeline |
| `bindEditDrCanvas` | [A] | edit-stage.bindEditDrCanvas |

---

## Module: `view/run-live.js` (the run page)

| Function | [A/?] | Called By |
|---|---|---|
| `renderRunStage` | [A] | run-stage.renderRunStage (the plan, live view or result) |
| `renderRunTime` | [A] | run-stage.renderRunClock (the 1s tick) |
| `updateRunDots` | [A] | run-stage.updateRunDots (one event's dots) |

---

## Module: `view/plots.js`

| Function | [A/?] | Called By |
|---|---|---|
| `nextFrame` | [A] | qc.js requestPreview, qc-renderer renderChannelQC |
| `oncePerFrame` | [A] | the `schedule…` methods of ui, qc-stage, run-stage and edit-stage |
| `prepareCanvas` | [A] | the draw functions, qc-renderer, edit-canvas (device-pixel sizing) |
| `getAxisPadding` (private) | [A] | getCanvasPlotMetrics |
| `getCanvasPlotMetrics` | [A] | drawTrace, edit-canvas, edit-stage |
| `drawSelectionRect` (private) | [A] | drawTrace |
| `drawAxes`, `drawTimeAxis` (private) | [A] | drawTrace |
| `drawRoiRects` | [A] | drawGridOverlay, renderAuxiliaryChannels |
| `drawTrace` | [A] | edit-canvas (a pulse window with its markers) |
| `drawGridOverlay` | [A] | qc-renderer.refreshVisuals |
| `drawMiniSeries` | [A] | qc-renderer.renderChannelQC |
| `strokeSeries` | [A] | drawGridOverlay, drawMiniSeries, renderAuxiliaryChannels |

---

## Module: `view/controls.js`

| Function | [A/?] | Called By |
|---|---|---|
| `setupToggle`, `setupLockedOnToggle`, `toggleConditional`, `isToggleOn` | [A] | setupRunEvents, buildParams |
| `applyLabeledToggle` | [A] | setupEditEvents (peel-off, lock), keyboard nav (P, L) |
| `runEditAction` | [A] | setupEditEvents (every mutating button), keyboard nav |
| `setEditActionBusy` | [A] | runEditAction, refreshEditModeButtons |

---

## Module: `view/qc-renderer.js`

| Function | [A/?] | Called By |
|---|---|---|
| `refreshVisuals` | [A] | qc-stage.refreshVisuals |
| `enableRoiSelection` | [A] | qc-stage.enableRoiSelection |
| `buildChannelGrid` (private) | [A] | renderChannelQC, once per grid's data |
| `renderChannelQC` | [A] | qc-stage.renderChannelQC |
| `populateAuxSelector` | [A] | qc-stage.populateAuxSelector |
| `renderAuxiliaryChannels` | [A] | qc-stage.renderAuxiliaryChannels |

---

## Module: `view/select-renderers.js`

| Function | [A/?] | Called By |
|---|---|---|
| `renderSelectPair` | [A] | edit-canvas.renderEditDropdownsView |

---

## Module: `config.js`

| Export | [A] | Used By |
|---|---|---|
| `API_BASE` | [A] | container.js, api/client.js |
| `COLORS` (read from css/tokens.css) | [A] | plots.js, edit-canvas.js, qc-renderer.js |
| `traceColors` (read from css/tokens.css) | [A] | qc-renderer.js, decomp/run.js |

---

## Summary: User-Exposed Element Count

| Stage | Buttons | Canvases | Form Fields | Dropdowns | Other |
|---|---|---|---|---|---|
| Import | 1 (browse) | 0 | 0 | 0 | 4 (loader, error, filename, global status) |
| QC | 4 (start, auto QC, add/remove artifact) + N (grid tabs) + N (channel cells) | 2 (emg, aux) | 18 (BIDS) + 8 (decomp) + 3 (filters) | 1 (aux) | 2 (auto-info, artifact count) |
| Run | 4 (start, run again, retry save, cancel) | 0 | 0 | 0 | 11 (plan, phases, counter ×3, clock ×2, dots, error, result stats, save text) |
| Edit | 13 (toolbar) | 3 (pulse, DR, timeline) | 1 (project) | 2 (grid, MU) | 1 (edit status) |
| **Total** | **~22 + N** | **5** | **~30** | **3** | **~18** |
