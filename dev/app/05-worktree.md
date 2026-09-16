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
| `progressText` | [U] | Progress message |
| `progressBar` | [U] | Progress bar |
| `stepImport` | [U] | Stepper: Import |
| `stepQc` | [U] | Stepper: QC |
| `stepRun` | [U] | Stepper: Decompose |
| `stepEdit` | [U] | Stepper: Edit |

### Settings Panel (shared across stages)

| Element ID | [U/A] | Purpose |
|---|---|---|
| `settingsToggleBtn` | [U] | Settings gear toggle |
| `settingsOverlay` | [U] | Settings overlay backdrop |
| `settingsPanel` | [U] | Settings panel container |
| `recordingSection` | [U] | Session Info section (collapsible) |
| `decompSettingsSection` | [U] | Decomposition Settings section (collapsible) |
| `filterSettingsSection` | [U] | Quality Filters section (collapsible) |
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
| `qcSection` | [U] | Channel quality grid container |
| `emgCanvas` | [U] | EMG overview canvas + ROI selection |
| `auxSelector` | [U] | Auxiliary channel selector |
| `auxCanvas` | [U] | Auxiliary channel canvas |

### Run Stage

| Element ID | [U/A] | Purpose |
|---|---|---|
| `stageRun` | [U] | Run stage container |
| `runPhase` | [U] | Pipeline phase KPI card |
| `muGridSelect` | [U] | Grid dropdown (run explorer) |
| `muSelect` | [U] | MU dropdown (run explorer) |
| `muMeta` | [U] | Discharge time count text |
| `muPulseCanvas` | [U] | Pulse train canvas (run explorer) |

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

## Module: `app/container.js` (Composition Root)

### User-exposed: none (internal wiring)

### App-internal functions

| Function | [A/?] | Called By |
|---|---|---|
| `initializeApp` | [A] | app.js (entry) |
| `wireEvents` | [A] | initializeApp |
| `updateStartAvailability` | [A] | wireEvents, switchStage, handleRawFile |
| `ensureDiscardMasks` | [A] | applyPreviewData, renderChannelQC |
| `getCurrentGrid` | [A] | drawGridOverlay, renderChannelQC |
| `buildParams` | [A] | runDecomposition |
| `applySessionInfoFromDecomposition` | [A] | loadDecompositionForEdit |
| `renderBidsAutoInfo` | [A] | requestPreview, loadDecompositionForEdit |
| `renderBidsMuscleFields` | [A] | requestPreview, loadDecompositionForEdit |
| `persistNpzBySaveTarget` | [A] | autoSaveRunDecomposition, saveEditedFile |
| `setEditModeWithStatus` | [A] | keyboard nav (a/d/x keys) |
| `refreshEditModeButtons` | [A] | setEditModeWithStatus, backupEditMu, loadDecompositionForEdit |
| `handleKeyboardNavigation` | [A] | window keydown listener |
| `nextFrame` | [A] | layout rerender |
| `renderChannelQC` (forwarder) | [A] | ui.rerenderPlotsForLayout, scheduleLayoutRerender |
| `refreshVisuals` (forwarder) | [A] | ui.rerenderPlotsForLayout, scheduleLayoutRerender |
| `renderEditExplorer` (forwarder) | [A] | ui.rerenderPlotsForLayout, switchStage |
| `setSelectedGrid` | [A] | populateGridTabs |

---

## Module: `app/state.js`

### User-exposed: none

### App-internal

| Export | [A/?] | Used By |
|---|---|---|
| `createEditSlice` | [A] | state initialization, transitions, resetEditSlice |
| `state` (default export) | [A] | all modules |

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
| `apiFetch` | [A] | api/client.js |
| `apiJson` | [A] | api/client.js |
| `waitForBackend` | [A] | initializeApp |
| `parseApiError` | [A] | http.js (internal) |

---

## Module: `app/services/navigation.js`

| Function | [A/?] | Called By |
|---|---|---|
| `setStatus` | [A] | ui.js, multiple stages |
| `updateWorkflowStepper` | [A] | ui.js, switchStage |
| `showWorkspace` | [A] | import-stage, qc-stage, editing-service |
| `updateStepAvailability` | [A] | ui.js, switchStage |
| `switchStage` | [A] | import-stage, run-stage, editing-service, import-stage stepper |
| `populateGridTabs` | [A] | showWorkspace |
| `getViewForStage` | [A] | navigation key handler |
| `setViewForStage` | [A] | navigation key handler |
| `adjustView` | [A] | keyboard nav |
| `goToMu` | [A] | keyboard nav |
| `handleKeyboardNavigation` | [A] | container.js |

---

## Module: `app/services/ui.js`

| Function | [A/?] | Called By |
|---|---|---|
| `setStatus` | [A] | all stages (via wrapper) |
| `setEditStatus` | [A] | editing-service, edit-stage |
| `setRunPhase` | [A] | updateProgress |
| `updateProgress` | [A] | run-stage handleStreamMessage |
| `updateWorkflowStepper` | [A] | switchStage, import-stage |
| `updateStepAvailability` | [A] | switchStage, wireEvents |
| `setSettingsOpen` | [A] | layout-stage, settingsOverlay click |
| `toggleSettingsOpen` | [A] | layout-stage settingsToggleBtn |
| `ensureSettingsToggleIcon` | [A] | wireEvents |
| `initLayoutResizePolicy` | [A] | layout-stage |
| `scheduleLayoutRerender` | [A] | switchStage, setSettingsOpen, resize |
| `showWorkspace` | [A] | import/qc/edit stages |
| `switchStage` | [A] | import/run/edit stages |
| `populateGridTabs` | [A] | showWorkspace |
| `setupToggle` | [A] | run-stage setup |
| `setupLockedOnToggle` | [A] | run-stage setup (SIL) |
| `toggleConditional` | [A] | run-stage setup |
| `isToggleOn` | [A] | run-stage, edit-stage |
| `runEditAction` | [A] | edit-stage setup (all mutating buttons) |
| `applyLabeledToggle` | [A] | edit-stage setup (peeloff, lock) |
| `setEditActionBusy` | [A] | runEditAction |
| `rerenderPlotsForLayout` | [A] | scheduleLayoutRerender |

---

## Module: `app/services/layout.js`

| Function | [A/?] | Called By |
|---|---|---|
| `setSettingsOpen` | [A] | ui.js |
| `toggleSettingsOpen` | [A] | ui.js |
| `ensureSettingsToggleIcon` | [A] | ui.js |
| `rerenderPlotsForLayout` | [A] | ui.js |
| `scheduleLayoutRerender` | [A] | ui.js |
| `initLayoutResizePolicy` | [A] | ui.js |

---

## Module: `app/services/file-session.js`

| Function | [A/?] | Called By |
|---|---|---|
| `getBidsProject` | [A] | runDecomposition (FormData), persistNpzBySaveTarget |
| `getBidsMuscleNames` | [A] | autoSaveRunDecomposition, persistNpzBySaveTarget |
| `clearUploadFormatError` | [A] | import-stage |
| `showUnsupportedUploadFormatError` | [A] | import-stage |
| `detectLandingFileType` | [A] | import-stage |
| `setUploadLoading` | [A] | qc-stage (requestPreview) |
| `getBidsEntityInputs` | [A] | collectBidsEntities |
| `getBidsSaveFields` | [A] | persistNpzBySaveTarget |
| `collectBidsEntities` | [A] | runDecomposition (FormData) |

---

## Module: `app/services/editing-service.js`

| Function | [A/?] | Called By |
|---|---|---|
| `spikesDiff` (private) | [A] | requestRoiEdit, requestFilterUpdate |
| `generateMuUids` (private) | [A] | loadDecompositionForEdit |
| `setEditBookmarkAndHide` (private) | [A] | requestRoiEdit |
| `requestRoiEdit` | [A] | operations.js (addSpikes, addArtifact, deleteSpikes, deleteDr) |
| `requestFilterUpdate` | [A] | edit-stage (updateMuFilter) |
| `removeOutliers` | [A] | edit-stage |
| `removeDuplicateMus` | [A] | edit-stage |
| `flagMuForDeletion` | [A] | edit-stage |
| `saveEditedFile` | [A] | edit-stage |
| `loadDecompositionForEdit` | [A] | edit-stage |

---

## Module: `app/services/error-service.js`

| Function | [A/?] | Called By |
|---|---|---|
| `handleError` | [A] | editing-service (loadDecompositionForEdit) |

---

## Module: `app/stages/import-stage.js`

| Function | [A/?] | Called By |
|---|---|---|
| `createImportStageService` | [A] | container.js |
| `handleNativeDialogOpen` | [A] | setupImportEvents (browseSignalBtn click) |
| `displayNameForPath` | [A] | handleNativeDialogOpen |
| `inferProjectFromPath` | [A] | handleNativeDialogOpen |
| `setupImportEvents` | [A] | wireEvents |

---

## Module: `app/stages/qc-stage.js`

| Function | [A/?] | Called By |
|---|---|---|
| `createQcStageService` | [A] | container.js |
| `populateAuxSelector` | [A] | requestPreview, applyPreviewData |
| `renderAuxiliaryChannels` | [A] | refreshVisuals, setupRunEvents, applyPreviewData |
| `requestQcGridWindow` | [A] | renderChannelQC, enableRoiSelection, handleStreamMessage |
| `requestPreview` | [A] | handleRawFilePath |
| `handleRawFilePath` | [A] | import-stage (handleNativeDialogOpen) |
| `renderChannelQC` | [A] | ui.rerenderPlotsForLayout, requestPreview, handleStreamMessage |
| `enableRoiSelection` | [A] | requestPreview, applyPreviewData, setupRunEvents |
| `refreshVisuals` | [A] | ui.rerenderPlotsForLayout, scheduleLayoutRerender |
| `syncRois` | [A] | setupRunEvents (nwindows change) |
| `runAutoQc` | [A] | setupRunEvents (qcAutoBtn click) |
| `toggleArtifactMode` | [A] | setupRunEvents (artifactAddBtn click) |
| `removeLastArtifact` | [A] | setupRunEvents (artifactRemoveBtn click) |

---

## Module: `app/stages/run-stage.js`

| Function | [A/?] | Called By |
|---|---|---|
| `createRunStageService` | [A] | container.js |
| `getMuIndicesForGrid` | [A] | renderMuDropdowns |
| `renderMuDropdowns` | [A] | renderMuExplorer |
| `renderMuExplorer` | [A] | refreshVisuals, handleStreamMessage, setupRunEvents |
| `autoSaveRunDecomposition` | [A] | handleStreamMessage (on done) |
| `handleStreamMessage` | [A] | runDecomposition (stream loop) |
| `runDecomposition` | [A] | setupRunEvents (startBtn click) |
| `setupRunEvents` | [A] | wireEvents |

---

## Module: `app/stages/edit-stage.js`

| Function | [A/?] | Called By |
|---|---|---|
| `createEditStageService` | [A] | container.js |
| `ensureEditFlagged` | [A] | requestRoiEdit, loadDecompositionForEdit |
| `getRawPulse` | [A] | renderEditExplorer |
| `getDisplayPulse` | [A] | renderEditExplorer |
| `backupEditMu` | [A] | operations.js (all mutating ops) |
| `recomputeEditDirty` | [A] | all mutating ops |
| `appendEditHistory` | [A] | all mutating ops |
| `getEditTotalSamples` | [A] | refreshEditTotals |
| `getPulseViewMeta` | [A] | edit-canvas.js |
| `refreshEditTotals` | [A] | loadDecompositionForEdit |
| `resetEditState` | [A] | loadDecompositionForEdit (on error) |
| `getEditMuIndices` | [A] | renderEditDropdowns |
| `renderEditDropdowns` | [A] | renderEditExplorer |
| `renderInstantaneousDr` | [A] | renderEditExplorer |
| `renderEditExplorer` | [A] | ui.rerenderPlotsForLayout, switchStage, all mutating ops |
| `restoreEditBackup` | [A] | setupEditEvents (undo button) |
| `requestRoiEdit` | [A] | operations.js |
| `requestFilterUpdate` | [A] | updateMuFilter |
| `updateMuFilter` | [A] | setupEditEvents (update button, Space key) |
| `addSpikesInSelection` | [A] | bindEditCanvas (mouseup in add mode) |
| `addArtifactInSelection` | [A] | bindEditCanvas (mouseup in add_artifact mode) |
| `deleteSpikesInSelection` | [A] | bindEditCanvas (mouseup in delete_spikes mode) |
| `deleteDrInSelection` | [A] | bindEditDrCanvas (mouseup) |
| `removeOutliers` | [A] | setupEditEvents (outliers button, R key) |
| `flagMuForDeletion` | [A] | setupEditEvents (flag button) |
| `resetCurrentMuEdits` | [A] | setupEditEvents (reset button) |
| `removeDuplicateMus` | [A] | setupEditEvents (deduplicate button) |
| `duplicateMu` | [A] | setupEditEvents (duplicate button) |
| `bindEditCanvas` | [A] | setupEditEvents |
| `bindEditDrCanvas` | [A] | setupEditEvents |
| `bindEditTimeline` | [A] | setupEditEvents |
| `saveEditedFile` | [A] | setupEditEvents (save button) |
| `loadDecompositionForEdit` | [A] | import-stage, autoSaveRunDecomposition |
| `loadDecompositionForEditByPath` | [A] | import-stage |
| `setupEditEvents` | [A] | wireEvents |

---

## Module: `app/stages/layout-stage.js`

| Function | [A/?] | Called By |
|---|---|---|
| `createLayoutStageService` | [A] | container.js |
| `ensureSettingsToggleIcon` | [A] | wireEvents |
| `toggleSettingsOpen` | [A] | setupLayoutEvents |
| `setSettingsOpen` | [A] | setupLayoutEvents |
| `initLayoutResizePolicy` | [A] | setupLayoutEvents |
| `setupLayoutEvents` | [A] | wireEvents |

---

## Module: `api/client.js`

| Function | [A/?] | Called By |
|---|---|---|
| `createApiClient` | [A] | container.js |
| `postJson` (internal) | [A] | editAction, editMode, etc. |
| `fetchQcWindow` | [A] | qc-stage.requestQcGridWindow |
| `fetchPreviewByPath` | [A] | qc-stage.requestPreview, decomp/run.js (token re-mint on expiry) |
| `runAutoQc` | [A] | signal/qc.requestAutoQc |
| `decomposeStream` | [A] | run-stage.runDecomposition |
| `fetchDecomposePreview` | [A] | run-stage.handleStreamMessage |
| `openFileDialog` | [A] | import-stage.handleNativeDialogOpen |
| `editAction` | [A] | editing-service.requestRoiEdit |
| `editMode` | [A] | editing-service.requestFilterUpdate |
| `editRemoveOutliers` | [A] | editing-service.removeOutliers |
| `editRemoveDuplicates` | [A] | editing-service.removeDuplicateMus |
| `editFlagMu` | [A] | editing-service.flagMuForDeletion |
| `editLoadByPath` | [A] | editing-service.loadDecompositionForEdit |
| `editSave` | [A] | editing-service.saveEditedFile, autoSaveRunDecomposition |
| `healthUrl` | [A] | initializeApp (waitForBackend) |

---

## Module: `api/routes.js`

| Export | [A] | Used By |
|---|---|---|
| `routes` | [A] | api/client.js |

---

## Module: `api/payloads.js`

| Function | [A/?] | Called By |
|---|---|---|
| `toFiniteNumber` | [A] | normalizeEditLoadPayload, normalizePreviewPayload |
| `normalizeEditLoadPayload` | [A] | editing-service.loadDecompositionForEdit |
| `normalizePreviewPayload` | [A] | qc-stage.requestPreview |

---

## Module: `api/binary-payloads.js`

| Function | [A/?] | Called By |
|---|---|---|
| `hasMagic` | [A] | isQcRawF32Payload, isEditLoadF32Payload, isDecomposePreviewF32Payload |
| `readFloat32Values` | [A] | decodeQcRawF32 |
| `to2d` | [A] | decodeQcRawF32 |
| `isQcRawF32Payload` | [A] | fetchQcWindow |
| `decodeQcJsonPayload` | [A] | fetchQcWindow (JSON fallback) |
| `decodeQcRawF32` | [A] | fetchQcWindow (binary) |
| `isEditLoadF32Payload` | [A] | editLoadByPath |
| `decodeEditLoadPayload` | [A] | editLoadByPath |
| `isDecomposePreviewF32Payload` | [A] | fetchDecomposePreview |
| `decodeDecomposePreviewPayload` | [A] | fetchDecomposePreview |

---

## Module: `decomp/explorer.js`

| Function | [A/?] | Called By |
|---|---|---|
| `buildRunMuDropdownModel` | [A] | run-stage.renderMuDropdowns |
| `buildRunMuExplorerModel` | [A] | run-stage.renderMuExplorer |

---

## Module: `decomp/params.js`

| Export | [A/?] | Called By |
|---|---|---|
| `postprocessFlags` | [A] | buildDecomposeParams |
| `buildDecomposeParams` | [A] | container.js.buildParams |
| `POSTPROCESS_MODES` | [A] | run-stage setup |
| `DEFAULT_POSTPROCESS_MODE` | [A] | run-stage setup |

---

## Module: `decomp/run.js`

| Function | [A/?] | Called By |
|---|---|---|
| `autoSaveRunDecomposition` | [A] | run-stage.autoSaveRunDecomposition |
| `runDecomposition` | [A] | run-stage.runDecomposition |
| `applyPreviewData` (private) | [A] | handleStreamMessage |
| `hydrateBinaryDecomposePreview` (private) | [A] | handleStreamMessage |
| `handleStreamMessage` | [A] | run-stage.handleStreamMessage |

---

## Module: `editing/operations.js`

| Export | [A/?] | Called By |
|---|---|---|
| `ensureEditFlagged` | [A] | edit-stage, requestRoiEdit, loadDecompositionForEdit |
| `getRawPulse` | [A] | edit-stage.renderEditExplorer |
| `getDisplayPulse` | [A] | edit-stage.renderEditExplorer |
| `backupEditMu` | [A] | edit-stage (all mutating ops) |
| `restoreEditBackup` | [A] | edit-stage.restoreEditBackup |
| `recomputeEditDirty` | [A] | edit-stage (all mutating ops) |
| `getEditTotalSamples` | [A] | edit-stage.refreshEditTotals |
| `getPulseViewMeta` | [A] | edit-canvas.js |
| `refreshEditTotals` | [A] | edit-stage.refreshEditTotals |
| `buildEditDropdownModel` | [A] | edit-stage.renderEditDropdowns |
| `resetEditState` | [A] | edit-stage.resetEditState |
| `addSpikesInSelection` | [A] | edit-stage.addSpikesInSelection |
| `addArtifactInSelection` | [A] | edit-stage.addArtifactInSelection |
| `deleteSpikesInSelection` | [A] | edit-stage.deleteSpikesInSelection |
| `deleteDrInSelection` | [A] | edit-stage.deleteDrInSelection |
| `computeInstantaneousDr` | [A] | edit-canvas.renderInstantaneousDr |
| `duplicateMu` | [A] | edit-stage.duplicateMu |
| `resetCurrentMuEdits` | [A] | edit-stage.resetCurrentMuEdits |

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
| `inferGridCount` | [A] | loadDecompositionForEdit, buildEditDropdownModel |
| `normalizeGridNames` | [A] | loadDecompositionForEdit |
| `gridDimensionsFor` | [A] | renderChannelQC |

---

## Module: `signal/qc.js`

| Function | [A/?] | Called By |
|---|---|---|
| `channelsToEnv` | [A] | requestQcGridWindow |
| `syncRois` | [A] | qc-stage.syncRois |
| `requestAutoQc` | [A] | qc-stage.runAutoQc |
| `requestQcGridWindow` | [A] | qc-stage.requestQcGridWindow |
| `requestPreview` | [A] | qc-stage.requestPreview |

---

## Module: `state/actions.js`

All functions are state mutators (`set*` functions). Each is called by at least one stage service or feature module. There are ~40+ exported functions. Key categories:

- `setFile`, `setUploadToken`, `setSeriesLength`, `setRois`, `setRoiForIndex`, `setRoiDraft`
- `setGridSeries`, `setGridNames`, `setChannelMeans`, `setCoordinates`, `setChannelTraces`, `setChannelTraceForGrid`
- `setQcWindowLoading`, `setQcWindowLoadingForGrid`
- `setMetadata`, `setMuscle`, `setFsamp`, `setPreviewSeries`, `setAuxData`
- `setDiscardMaskChannel`, `ensureDiscardMasks`
- `setParameters`, `setIsRunning`
- `setMuPreviewData`, `setRunCurrentMuGrid`, `setRunCurrentMu`, `setRunView`
- `setRunDownloadInFlight`, `setLastRunDownloadKey`
- `setCurrentStage`, `setCurrentGrid`
- `clearPreviewState`
- Edit slice: `setEditMode`, `setEditCurrentMuGrid`, `setEditCurrentMu`, `setEditProject`, `setEditSignalToken`, `setEditView`, `setEditDistimesForMu`, `setEditArtifactTimesForMu`, `setEditPulseTrainForMu`, `setEditFlagForMu`, `clearEditPulseSelections`, `clearEditDrSelections`, `clearAllEditSelections`, `setEditFile`, `setEditFilename`, `setEditPulseTrains`, `setEditOriginalPulseTrains`, `setEditDistimes`, `setEditOriginalDistimes`, `setEditGridNames`, `setEditMuGridIndex`, `setEditFsamp`, `setEditParameters`, `setEditTotalSamples`, `setEditFlaggedArray`, `setEditMuUids`, `setEditHistory`, `setEditArtifactTimes`, `appendEditHistoryEntry`, `popLastEditHistoryEntryForMu`, `clearEditHistoryForMu`, `setEditBackup`, `setEditBookmark`, `setShowBookmark`, `setEditDirty`, `setEditPulseSelection`, `setEditPulseDraftSelection`, `setEditDrSelection`, `setEditDrDraftSelection`, `resetEditSlice`, `appendEditMu`, `setEditSoftwareVersions`

---

## Module: `state/selectors.js`

| Function | [A/?] | Called By |
|---|---|---|
| `getCurrentGrid` | [A] | container.js, qc-renderer |
| `roiStart` | [A] | qc-renderer, plots, qc.js |
| `roiEnd` | [A] | qc-renderer, plots, qc.js |
| `muUidFor` | [A] | editing-service, operations.js |
| `muIndicesForGrid` (private) | [A] | getRunMuIndicesForGrid, getEditMuIndicesForGrid |
| `getRunMuIndicesForGrid` | [A] | run-stage.getMuIndicesForGrid |
| `getEditMuIndicesForGrid` | [A] | edit-stage.getEditMuIndices |

---

## Module: `state/transitions.js`

| Function | [A/?] | Called By |
|---|---|---|
| `beginRawPreviewTransition` | [A] | qc-stage.handleRawFile, handleRawFilePath |
| `rollbackRawPreviewTransition` | [A] | qc-stage (on preview failure) |

---

## Module: `view/bids-renderer.js`

| Function | [A/?] | Called By |
|---|---|---|
| `makeInfoItem` | [A] | renderBidsAutoInfo |
| `resetBidsEntityDefaults` | [A] | qc-stage.handleRawFile, handleRawFilePath |
| `applyParticipantFields` | [A] | applySessionInfoToDom |
| `renderBidsAutoInfo` | [A] | container.js.renderBidsAutoInfo |
| `renderBidsMuscleFields` | [A] | container.js.renderBidsMuscleFields |
| `applySessionInfoToDom` | [A] | applySessionInfoFromDecomposition |

---

## Module: `view/edit-canvas.js`

| Function | [A/?] | Called By |
|---|---|---|
| `renderBookmark` (private) | [A] | renderEditExplorer |
| `clampY` (private) | [A] | bindEditCanvas, bindEditDrCanvas |
| `createDragState` (private) | [A] | bindEditCanvas, bindEditDrCanvas |
| `renderEditDropdownsView` | [A] | edit-stage.renderEditDropdowns |
| `renderEditExplorer` | [A] | edit-stage.renderEditExplorer |
| `renderInstantaneousDr` | [A] | edit-stage.renderInstantaneousDr |
| `bindEditCanvas` | [A] | edit-stage.bindEditCanvas |
| `renderEditTimeline` | [A] | edit-stage.renderEditExplorer |
| `bindEditTimeline` | [A] | edit-stage.bindEditTimeline |
| `bindEditDrCanvas` | [A] | edit-stage.bindEditDrCanvas |

---

## Module: `view/explorer.js` (run-stage explorer)

| Function | [A/?] | Called By |
|---|---|---|
| `renderMuDropdowns` | [A] | run-stage.renderMuDropdowns |
| `renderMuExplorer` | [A] | run-stage.renderMuExplorer |

---

## Module: `view/plots.js`

| Function | [A/?] | Called By |
|---|---|---|
| `getAxisPadding` (private) | [A] | getCanvasPlotMetrics |
| `getCanvasPlotMetrics` | [A] | drawSeries, drawGridOverlay, edit-canvas, qc-renderer |
| `drawSelectionRect` (private) | [A] | drawSeries |
| `drawRoiRects` | [A] | drawGridOverlay, renderAuxiliaryChannels |
| `drawSeries` | [A] | edit-canvas, explorer, qc-renderer |
| `drawGridOverlay` | [A] | qc-renderer.refreshVisuals |
| `drawMiniSeries` | [A] | qc-renderer.renderChannelQC |

---

## Module: `view/qc-renderer.js`

| Function | [A/?] | Called By |
|---|---|---|
| `refreshVisuals` | [A] | qc-stage.refreshVisuals |
| `enableRoiSelection` | [A] | qc-stage.enableRoiSelection |
| `renderChannelQC` | [A] | qc-stage.renderChannelQC |
| `populateAuxSelector` | [A] | qc-stage.populateAuxSelector |
| `renderAuxiliaryChannels` | [A] | qc-stage.renderAuxiliaryChannels |

---

## Module: `view/select-renderers.js`

| Function | [A/?] | Called By |
|---|---|---|
| `renderSelectPair` | [A] | edit-canvas.renderEditDropdownsView, explorer.renderMuDropdowns |

---

## Module: `config.js`

| Export | [A] | Used By |
|---|---|---|
| `API_BASE` | [A] | container.js, api/client.js |
| `COLORS` | [A] | plots.js, edit-canvas.js, qc-renderer.js |
| `GRID_COLORS` | [A] | state.js (gridColors), plots.js |
| `UNIFORM_PULSE_COLOR` | [A] | edit-canvas.js |

---

## Module: `app/deps.js`

| Export | [A/?] | Used By |
|---|---|---|
| JSDoc typedefs | [?] | Documentation only — not enforced at runtime |

---

## Summary: User-Exposed Element Count

| Stage | Buttons | Canvases | Form Fields | Dropdowns | Other |
|---|---|---|---|---|---|
| Import | 1 | 0 | 0 | 0 | 4 (loader, error, filename, global status) |
| QC | 1 (start) + N (grid tabs) + N (channel cells) | 2 (emg, aux) | 18 (BIDS) + 8 (decomp) + 3 (filters) | 1 (aux) | 3 (auto-info, phase, progress) |
| Run | 0 | 1 (pulse) | 0 | 2 (grid, MU) | 3 (phase, progress, meta) |
| Edit | 13 (toolbar) | 3 (pulse, DR, timeline) | 1 (project) | 2 (grid, MU) | 1 (edit status) |
| **Total** | **~16 + N** | **6** | **~30** | **6** | **~12** |
