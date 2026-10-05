# 02 - User Workflow Stages

The app has four stages, presented as a linear workflow with a clickable stepper at the top of the workspace. Import is the landing screen; QC, Run and Edit are workspace sections whose entry guards, enter/exit hooks and redraws are declared in `app/stages/lifecycle.js` (see [01-architecture.md](01-architecture.md#stage-registration-and-lifecycle)).

```
  Import ───> QC / ROI ───> Decompose ───> Edit
  (landing)   (stageQc)     (stageRun)     (stageEdit)
     │            │             │              │
     │            │             │              │
  browse      review        run           edit
  file        channels      decomposition  spikes
              + fill        + view MUs     + save
              BIDS form
```

---

## Stage 1: File Loading (Import)

### Purpose

The user selects a signal file (raw EMG or saved decomposition). The app detects the file type and routes to the appropriate next stage.

### Supported File Formats

| Format | Extension | Routing |
|---|---|---|
| MATLAB | `.mat` | Ambiguous — tries raw preview first, falls back to decomposition load |
| OTB+ | `.otb+` | Raw → QC stage |
| OTB4 | `.otb4` | Raw → QC stage |
| BIDS EMG | `.bdf`, `.edf` | Raw → QC stage (parses BIDS entities from filename) |
| Intan RHD | `.rhd` | Raw → QC stage (folder-named if header is "info.rhd") |
| Decomposition | `.npz` | Decomposition → Edit stage (skips QC and Decompose) |

### User-Exposed UI

| Element ID | Type | Action |
|---|---|---|
| `browseSignalBtn` | Button | Opens native OS file dialog |
| `uploadLoader` | Spinner | Loading indicator during upload |
| `uploadFormatError` | Alert | Shows "Accepted: raw (.mat, .otb+, .otb4, .bdf, .edf, .rhd) or decomposition (.npz, .mat)" on unsupported file |

### Flow

```
User clicks #browseSignalBtn
  -> importStage.handleNativeDialogOpen()
     -> api.openFileDialog()   GET /dialog/open-file
     -> detectLandingFileType(name)
        ├─ "raw"           -> qcStage.handleRawFilePath(path, name)
        │                      -> requestPreview({ filepath: path })  POST /preview-by-path
        │                      -> showWorkspace() -> switchStage("qc")
        ├─ "decomposition"  -> editStage.loadDecompositionForEditByPath(path)
        │                      -> api.editOpen(path)   POST /edit/session/open
        │                      -> showWorkspace() -> switchStage("edit")
        └─ "ambiguous_mat"  -> try raw first (silent),
                               fall back to decomposition on failure
```

For `.bdf`/`.edf` files, BIDS entities are parsed from the filename. The project comes from the preview (`project`, the file's folder under the output folder), which `applyPreviewMetadata` puts in the Project field.

### State Written

```
beginRawPreviewTransition(state, file):
  - resetEditSlice(state)         [preserves bidsRoot]
  - setFile(state, file)
  - setUploadToken(state, null)
  - setChannelTraces(state, [])
  - state.discardMasks = []

[after preview API succeeds]:
  - setUploadToken(state, data.upload_token)
  - fetchSeries("overview"), fetchSeries("aux")   # whole-recording envelopes
  - setGridSeries, setGridNames, setSeriesLength
  - setChannelMeans, setCoordinates, setChannelTraces([])
  - setMetadata, setMuscle, setAuxData, setFsamp
  - setRois
  - switchStage("qc")   # runs the Edit exit hook if the user was editing
```

---

## Stage 2: Quality Check & Experiment Info Form (QC)

### Purpose

The user reviews channel quality, discards bad channels, selects regions of interest (ROIs), marks artifact windows, fills in BIDS experiment metadata, and configures decomposition parameters. There is no explicit pass/fail gate — the user advances by clicking "Decompose Signal".

### User-Exposed UI

#### Grid Tabs (dynamic)

| Element ID | Type | Action |
|---|---|---|
| `qcGridTabs` | Dynamic buttons | Click to switch active grid (one button per grid) |

Each tab click calls `setCurrentGrid(state, idx)` then `renderChannelQC()`, which asks `ensureQcTraces()` for that grid's traces over the first window. Traces that arrive after the window moved on (a new ROI drag, another file) are dropped, so the grid never shows an older window's traces.

#### Channel Quality Grid

| Element | Type | Action |
|---|---|---|
| `qc-cell` (dynamic) | Clickable button per channel | Toggle discard mask on/off for that channel |
| `qc-mini` (dynamic) | Mini canvas per channel | Shows per-channel trace |

Discarded channels render in warning color and are excluded downstream. Initial masks are seeded from `metadata.bad_channels_per_grid` when the loader provides it (BIDS `channels.tsv` status); otherwise all channels start kept.

#### Automatic QC Button

| Element ID | Type | Action |
|---|---|---|
| `qcAutoBtn` | Button | "Automatic QC" — triggers `qcStage.runAutoQc()` -> `POST /qc/auto` |

Runs the server-side QC pipeline (`run_auto_qc`) and loads the result into the interactive controls: detected bad channels replace the discard masks (click a cell to correct), and detected artifact regions populate the artifact windows list. Nothing is sent to decomposition here — the corrected masks travel with the run request.

#### EMG Overview Canvas

| Element ID | Type | Action |
|---|---|---|
| `emgCanvas` | Canvas | Drag to select ROI (region of interest); shows per-grid mean EMG overlay + artifact windows |

ROI drag selection updates the nearest ROI window and triggers a QC grid window fetch. When artifact mode is armed, dragging marks a new artifact window instead.

#### Artifact Window Controls

| Element ID | Type | Action |
|---|---|---|
| `artifactAddBtn` | Button (+) | Arm/cancel artifact selection mode; next drag on `emgCanvas` marks a window |
| `artifactRemoveBtn` | Button (-) | Remove the last artifact window |
| `artifactCount` | span | Current artifact window count |

Artifact windows are visually shaded on the EMG overview. They are OR'd into the decomposition artifact mask at run time, excluding those samples from filter updates and state propagation.

#### Auxiliary Channels

| Element ID | Type | Action |
|---|---|---|
| `auxSelector` | Select dropdown | Choose auxiliary channel to display (or "All channels") |
| `auxCanvas` | Canvas | Shows auxiliary channel traces; drag to select ROI |

#### Decompose Button

| Element ID | Type | Action |
|---|---|---|
| `startBtn` | Button | "Decompose Signal" — triggers `runDecomposition()` (guarded: disabled if no file or already running) |

#### Session Info Form (Settings Panel)

| Field ID | Type | Default | Purpose |
|---|---|---|---|
| `fileName` | span (display) | "None" | Loaded filename |
| `bidsProject` | text input | — | BIDS project name |
| `bidsSubject` | text input | "1" | BIDS subject entity |
| `bidsSession` | text input | "1" | BIDS session entity |
| `bidsAcquisition` | text input | — | BIDS acquisition entity |
| `bidsRun` | number input | — | BIDS run entity |
| `bidsTask` | text input + datalist | "trapezoid" | BIDS task label |
| `bidsParticipantAge` | number input | — | Participant age |
| `bidsParticipantSex` | select (n/a, M, F, O) | "" | Participant sex |
| `bidsParticipantHandedness` | select (n/a, right, left, ambidextrous) | "" | Participant handedness |
| `bidsMuscleContainer` | dynamic div | — | Muscle name inputs (one per grid) |
| `bidsPlacementScheme` | select (ChannelSpecific, Measured, Other) | "ChannelSpecific" | Electrode placement scheme |
| `bidsPlacementDescription` | text input | — | Placement description (hidden unless scheme=Other) |
| `bidsManufacturer` | text input | — | Hardware manufacturer |
| `bidsDeviceModel` | text input | — | Device model |
| `fsamp` | number input (readonly) | 2048 | Sampling frequency (auto-filled) |
| `bidsPowerlineFreq` | select (50, 60) | "50" | Powerline frequency |
| `bidsAutoInfo` | div (auto-detected) | hidden | Auto-detected metadata (muscles, filters, gain) |

#### Decomposition Settings (Settings Panel)

| Field ID | Type | Default | Purpose |
|---|---|---|---|
| `niter` | number input | 150 | Decomposition iterations |
| `nwindows` | number input | 1 | Analysis window count (changes ROI count) |
| `duplicatesthresh` | number input | 0.3 | Duplicate spike threshold |
| `peelOffToggle` | pill toggle (off) | off | Peel-off on/off |
| `peelOffWindow` | number input | 25 | Peel-off window (ms) — hidden unless peel-off is on |
| `postprocessMode` | select (windowed, full-trace, adaptive) | "windowed" | Post-processing mode |
| `postprocessModeHint` | text | — | Hint text for selected mode |
| `silToggle` | pill toggle (locked ON) | on | SIL filter (always on, cannot be turned off) |
| `silValue` | number input | 0.9 | SIL threshold |
| `covToggle` | pill toggle (off) | off | CoV filter on/off |
| `covValue` | number input | 0.5 | CoV threshold — hidden unless CoV is on |

#### Post-processing Modes

| Mode | Label | Hint |
|---|---|---|
| `windowed` | Windowed | "Filters applied inside each analysis window only. Pulse trains are zero outside the ROI." |
| `full-trace` | Full trace | "Filters dewhitened and applied across the whole recording, so units extend beyond the ROI." |
| `adaptive` | Adaptive | "Filters and whitening are adapted batch by batch across the whole recording." (beta) |

### Flow

```
[Preview loaded from Stage 1]
  -> renderBidsAutoInfo()        shows auto-detected metadata
  -> renderBidsMuscleFields()    creates muscle name inputs
  -> switchStage("qc")           draws the page once, at the next frame:
       renderChannelQC()         channel grid with mini-plots (asks ensureQcTraces)
       refreshVisuals()          EMG overview and aux traces, with the windows
  -> the landing page hides after that frame; the grid's traces fill in as they arrive

User reviews channels:
  - clicks "Automatic QC" (#qcAutoBtn) to auto-detect bad channels + artifacts
  - clicks qc-cell to toggle discard (corrects auto-detected masks)
  - arms artifact mode (+) and drags on emgCanvas to mark artifact windows
  - drags on emgCanvas to set an ROI (pickRoiSlot: over a drawn window → adjust
    it; else the first undrawn window; else the window starting nearest)
  - changes nwindows to split ROI count
  - fills BIDS form fields
  - configures decomposition parameters

User clicks "Decompose Signal" (#startBtn)
  -> runDecomposition()  [transitions to Stage 3]
```

### API Calls During QC

| Endpoint | When | Purpose |
|---|---|---|
| `POST /preview-by-path` | On file load (native dialog) | Fetch preview metadata |
| `GET /series/emg` | On initial render, grid tab switch, ROI change | Fetch QC channel envelopes |
| `GET /series/overview`, `/series/aux` | After `/preview-by-path` | Fetch the whole-recording overview and aux envelopes |
| `POST /qc/auto` | On "Automatic QC" button click | Run auto QC: detect bad channels + artifact regions |

---

## Stage 3: Decomposition (Run)

### Purpose

The decomposition pipeline runs server-side, streaming progress events to the frontend. The run page shows the plan the settings imply before a run, follows the search live while it runs, and reports the result when it finishes. The model behind the page lives in `decomp/live.js` (`RunLive`), its rendering in `view/run-live.js`.

### User-Exposed UI

The `#stageRun` container carries `data-mode`: `pre` (no run yet), `live` (a run is going) or `result` — the mode picks what shows.

| Element ID | Type | Action |
|---|---|---|
| `runPlan` | Definition list | The pre-run plan: grids (and kept channels), windows, iterations, the filters |
| `runStartBtn` | Button | Start the decomposition |
| `runPhases` | Phase track | Load → Filter → Decompose → Post-process → Save; the active dot bounces, a failed phase turns red |
| `runCount`, `runCountLabel`, `runCountGrids` | Counter | Units kept so far (per grid), then the summary's final count |
| `runElapsed`, `runEta` | Clock | Elapsed time, and the time left projected from the search's pace |
| `runDots` | Dot grid | One row per grid × window, one dot per iteration: kept / rejected / too few spikes / skipped |
| `runError` | Alert | The failure message, when a run fails |
| `runResultStats` | Definition list | Found vs kept after post-processing, mean silhouette |
| `runSaveText` | Text | The save's outcome ("Saving the decomposition…", "Saved to …", or the failure) |
| `runRetrySaveBtn` | Button | Retry the save after a failure |
| `runAgainBtn` | Button | Run again with the current settings |
| `cancelRunBtn` | Button | Cancel the running decomposition (shown while it runs) |

The QC stage's `#startBtn` ("Decompose Signal") starts the run too; the three start buttons share `updateStartAvailability`.

### Flow

```
User clicks "Decompose Signal" / "Start decomposition"
  -> runDecomposition()
     1. Guards: isRunning? file? -> early return
     2. setIsRunning(state, true); setParameters(state, buildParams())
     3. setRunLive(state, createRunLive(now)); setRunResultToken(state, "")
     4. switchStage("run"); renderRunStage(); a 1s clock renders runElapsed/runEta
     5. Build FormData:
        - upload_token (required)
        - params (JSON)
        - persist_output = "false"
        - discard_channels (JSON)
        - rois (JSON)
        - artifact_regions (JSON)
        - project, bids_entities, bids_export = "true"
        - full_preview = "true"
     6. api.decomposeStream(formData, 15min timeout)
        - on an upload_token error: POST /preview-by-path with state.file.path
          to mint a fresh token, then retry once (masks/ROIs are kept)
     7. Stream NDJSON reader loop:
        - each line -> handleStreamMessage(msg), which folds it into state.runLive
          - msg.stage=="error"      -> failRun(): live.status="failed", live.error
          - msg.stage=="cancelled"  -> setRunLive(state, null): the page returns to the plan
          - otherwise applyRunEvent(): the event's phase moves the track; decompose
            events (grid, window, iter, outcomes) fill the row's dots and bump
            keptByGrid, then updateRunDots() restyles just the changed dots
          - msg.stage=="done"       -> setRunResultToken; applyPreviewData() (state only:
                                        the QC plots are drawn when their page is
                                        shown; their traces are fetched now);
                                        buildRunSummary(msg.summary); live.phase="save",
                                        live.status="done"; autoSaveRunDecomposition()
        - a stream that ends without done/error/cancelled fails the run
          ("The decomposition stopped without a result")
     8. finally: clear the clock; setIsRunning(state, false); renderRunStage()
```

### Progress Events

The worker's progress callbacks carry where the run is, so the page never parses message text:

| Event fields | Meaning |
|---|---|
| `phase` | `load` \| `preprocess` \| `decompose` \| `postprocess` \| `export` (mapped to the `save` step) |
| `grid`, `ngrid`, `window`, `nwindows`, `niter` | Where the search is; the first decompose event lays out one dot row per grid × window |
| `iter`, `outcomes` | Iterations accounted for, and one code per iteration since the last event: `k` kept, `r` rejected, `f` too few spikes |
| `window_done` | The window's basis ran out early; the row's remaining dots are marked skipped |

### API Calls During Run

| Endpoint | When | Purpose |
|---|---|---|
| `POST /decompose_stream` | On a start button click | Main decomposition call (streaming NDJSON) |
| `POST /decompose/cancel` | On "Cancel" click | Stop the run; the stream ends with `cancelled` |
| `POST /edit/save` | On auto-save after completion | Save the decomposition as .npz (the trains and discharge times stay on the server under `run_result_token`) |
| `POST /edit/session/open` | After the save | Preload the saved file into the edit session without leaving the run page |

---

## Stage 4: Editing

The most complex stage. The user manually refines motor unit decomposition results: spike times, pulse trains, artifact markers, and per-MU flags. See [03-edit-stage-controls.md](03-edit-stage-controls.md) for the complete control reference.

### High-Level Flow

The decomposition being edited lives on the server, in an edit session. The page holds the
discharge times and per-MU fields and fetches the current MU's pulse train for the window on
screen.

```
Open .npz/.mat decomposition -> POST /edit/session/open -> setEditSession(state.edit)
  |   (unsaved edits left from an earlier session? ask, then POST /edit/session/recover)
  v
Render explorer: grid dropdown + MU dropdown + pulse canvas + DR canvas + timeline
  (GET /series/pulse for the window on screen, refetched when the MU's version changes)
  |
  v
User edits per-MU spike trains via:
  - Drag-box ROI on pulse canvas  -> add-spikes / delete-spikes / add-artifact
  - Drag-box ROI on DR canvas     -> delete-dr (discharge-rate outliers)
  - Button actions                -> update-filter, remove-outliers, flag,
                                     duplicate, remove-duplicates, reset, undo
  - Timeline drag                -> pan/zoom the view window
  - Keyboard                      -> shortcuts
  |
  v
Each edit: POST /edit/ops/{op} -> applyEditChange (changed MUs' times, per-MU fields,
           history) -> renderEditExplorer
  |
  v
Save -> POST /edit/session/save -> applyEditSave (mirror the saved file; dirty cleared)
```

### Can Also Be Reached Directly

Loading a `.npz` file from the Import stage skips QC and Decompose, going straight to Edit.

```
Import (.npz file) -> editStage.loadDecompositionForEditByPath(path)
  -> api.editOpen(filepath)   POST /edit/session/open
  -> setEditSession(state.edit)
  -> showWorkspace() -> switchStage("edit")
  -> renderEditExplorer()
```
