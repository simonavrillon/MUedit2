# 02 - User Workflow Stages

The app has four stages, presented as a linear workflow with a clickable stepper at the top of the workspace. Each stage is a CSS-toggled section; there is no formal enter/exit lifecycle.

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
     -> api.openFileDialog()        GET /dialog/open-file
     -> detectLandingFileType(name)
        ├─ "raw"           -> qcStage.handleRawFilePath(path, name)
        │                      -> requestPreview({ filepath: path })  POST /preview-by-path
        │                      -> showWorkspace() -> switchStage("qc")
        ├─ "decomposition"  -> editStage.loadDecompositionForEditByPath(path)
        │                      -> api.editLoadByPath(path)   POST /edit/load-by-path
        │                      -> showWorkspace() -> switchStage("edit")
        └─ "ambiguous_mat"  -> try raw first (silent),
                               fall back to decomposition on failure
```

For `.bdf`/`.edf` files, BIDS entities are parsed from the filename and the project is inferred from the directory path.

### State Written

```
beginRawPreviewTransition(state, file):
  - resetEditSlice(state)         [preserves bidsRoot]
  - setFile(state, file)
  - setUploadToken(state, null)
  - setChannelTraces(state, [])
  - setQcWindowLoading(state, {})
  - state.discardMasks = []

[after preview API succeeds]:
  - setUploadToken(state, data.upload_token)
  - setGridSeries, setGridNames, setSeriesLength
  - setChannelMeans, setCoordinates, setChannelTraces([])
  - setMetadata, setMuscle, setAuxData, setFsamp
  - setPreviewSeries, setRois
  - setCurrentStage(state, "qc")
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

Each tab click calls `setCurrentGrid(state, idx)` then `renderChannelQC()` and `requestQcGridWindow()`.

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
| `adaptive` | Adaptive | "Separation vectors and whitening track the signal batch by batch across the whole recording." (beta) |

### Flow

```
[Preview loaded from Stage 1]
  -> renderChannelQC()           builds channel grid with mini-plots
  -> drawGridOverlay()           renders EMG overview on emgCanvas
  -> renderAuxiliaryChannels()   renders aux traces
  -> renderBidsAutoInfo()        shows auto-detected metadata
  -> renderBidsMuscleFields()    creates muscle name inputs

User reviews channels:
  - clicks "Automatic QC" (#qcAutoBtn) to auto-detect bad channels + artifacts
  - clicks qc-cell to toggle discard (corrects auto-detected masks)
  - arms artifact mode (+) and drags on emgCanvas to mark artifact windows
  - drags on emgCanvas to select ROI
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
| `POST /qc/window` | On initial render, grid tab switch, ROI change | Fetch QC channel traces |
| `POST /qc/auto` | On "Automatic QC" button click | Run auto QC: detect bad channels + artifact regions |

---

## Stage 3: Decomposition (Run)

### Purpose

The decomposition pipeline runs server-side, streaming progress events to the frontend. The user watches progress and can preview motor unit pulse trains as they become available.

### User-Exposed UI

| Element ID | Type | Action |
|---|---|---|
| `runPhase` | KPI card | Shows current pipeline phase (Idle/Preprocessing/Decomposing/Finalizing/Complete/Failed) |
| `progressText` | Text | Progress message |
| `progressBar` | Progress bar | Width fills from 0-100% |
| `muGridSelect` | Select dropdown | Choose grid to view in MU explorer |
| `muSelect` | Select dropdown | Choose motor unit within grid |
| `muMeta` | Text | Shows discharge time count for selected MU |
| `muPulseCanvas` | Canvas | Pulse train plot for selected MU |

Keyboard: `↑`/`↓` zoom in/out, `←`/`→` scroll left/right (shared with edit stage).

### Flow

```
User clicks "Decompose Signal"
  -> runDecomposition()
     1. Guards: isRunning? file? -> early return
     2. setIsRunning(state, true); updateStartAvailability()
     3. switchStage("run")
     4. setParameters(state, buildParams())
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
        - each line -> handleStreamMessage(msg)
          - msg.pct?       -> updateProgress(pct, message, stage)
          - msg.message?   -> updateProgress(undefined, message, stage)
          - msg.preview?   -> applyPreviewData() [hydrates MU pulse trains]
          - msg.summary?   -> setProgressText("Grid 1: N MU | Grid 2: M MU | Total: X MU")
          - msg.stage=="error" -> setStatus(error)
          - msg.stage=="done"  -> autoSaveRunDecomposition()
     8. finally: setIsRunning(state, false)

[On done]:
  -> autoSaveRunDecomposition()
     -> persistNpzBySaveTarget()     POST /edit/save
     -> loadDecompositionForEditByPath(saved.path)  POST /edit/load-by-path
     -> switchStage("edit")
```

### Progress Phases

| Phase | Trigger |
|---|---|
| Idle | Initial / no progress |
| Loading | Message contains "loading" |
| Preprocessing | Message contains "preprocess" |
| Decomposing | Message contains "grid" |
| Finalizing | Message contains "finalizing" |
| Complete | Stage = "done" |
| Failed | Stage = "error" or message contains "error" |

### API Calls During Run

| Endpoint | When | Purpose |
|---|---|---|
| `POST /decompose_stream` | On "Decompose Signal" click | Main decomposition call (streaming NDJSON) |
| `GET /decompose_preview/{token}` | On preview event (binary fast-path) | Fetch heavy MU arrays in binary format |
| `POST /edit/save` | On auto-save after completion | Save decomposition result as .npz |

---

## Stage 4: Editing

The most complex stage. The user manually refines motor unit decomposition results: spike times, pulse trains, artifact markers, and per-MU flags. See [03-edit-stage-controls.md](03-edit-stage-controls.md) for the complete control reference.

### High-Level Flow

```
Load .npz decomposition -> populate state.edit.*
  |
  v
Render explorer: grid dropdown + MU dropdown + pulse canvas + DR canvas + timeline
  |
  v
User edits per-MU spike trains via:
  - Drag-box ROI on pulse canvas  -> add-spikes / delete-spikes / add-artifact
  - Drag-box ROI on DR canvas     -> delete-dr (discharge-rate outliers)
  - Button actions                -> update-filter, remove-outliers, flag-mu,
                                     duplicate-mu, remove-duplicate-mus, reset, undo
  - Timeline drag                -> pan/zoom the view window
  - Keyboard                      -> shortcuts
  |
  v
Each edit: backupEditMu() -> API call -> setEditDistimesForMu -> recomputeEditDirty ->
           appendEditHistory -> renderEditExplorer
  |
  v
Save -> POST /edit/save -> setEditOriginalDistimes (dirty cleared)
```

### Can Also Be Reached Directly

Loading a `.npz` file from the Import stage skips QC and Decompose, going straight to Edit.

```
Import (.npz file) -> editStage.loadDecompositionForEditByPath(path)
  -> api.editLoadByPath(filepath)   POST /edit/load-by-path
  -> populate state.edit.*
  -> showWorkspace() -> switchStage("edit")
  -> renderEditExplorer()
```
