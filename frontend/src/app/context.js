/**
 * The application context: one object every service and feature receives.
 *
 * The container creates it with the three singletons (state, els, api) and
 * merges each service's methods into it, so a feature reaches any
 * collaborator as `app.x()` without a hand-wired dependency list. Services
 * read other services' methods at call time, never while being constructed,
 * which is what lets the QC, run and edit stages refer to each other.
 *
 * Pure helpers (plot drawing, BIDS naming, state actions and selectors) are
 * not in the context; modules import them directly.
 */

/** @typedef {import("./state.js").State} State */
/** @typedef {import("./dom.js").Els} Els */
/** @typedef {ReturnType<typeof import("../api/client.js").createApiClient>} Api */
/** @typedef {"qc" | "run" | "edit"} StageKey */
/** @typedef {"import" | StageKey} WorkflowStep A step of the header stepper. */
/** @typedef {import("./state.js").FileRef} FileRef */
/** @typedef {import("./state.js").Selection} Selection */
/** @typedef {import("./state.js").EditMode} EditMode */
/** @typedef {import("./state.js").EditHistoryEntry} EditHistoryEntry */
/** @typedef {import("../io/bids.js").BidsEntities} BidsEntities */
/** @typedef {import("../editing/operations.js").PulseViewMeta} PulseViewMeta */
/** @typedef {import("../decomp/params.js").DecomposeParams} DecomposeParams */
/** @typedef {"emgCanvas" | "auxCanvas"} RoiCanvasId Canvases that take ROI and artifact drags. */
/** @typedef {"add-spikes" | "add-artifact" | "delete-spikes" | "delete-dr"} RoiAction */

/**
 * A box drawn on an edit canvas, in samples and pulse-train units.
 *
 * @typedef {object} RoiEditRequest
 * @property {number} muIdx
 * @property {number[]} pulse
 * @property {number} xStart
 * @property {number} xEnd
 * @property {number} yMin
 * @property {number} [yMax]
 * @property {number} [fs]
 * @property {number[]} [artifact_times]
 */
/** @typedef {"muted" | "success" | "error"} Tone */
/** @typedef {{ start: number, end: number }} Span */
/** @typedef {Record<string, any>} JsonObject A decoded backend JSON object whose fields are not typed. */

/**
 * @typedef {object} Core
 * @property {State} state
 * @property {Els} els
 * @property {Api} api
 */

/**
 * @typedef {object} UiService
 * @property {(text: string, tone?: Tone) => void} setStatus
 * @property {(text: string, tone?: Tone) => void} setEditStatus
 * @property {(pct?: number, message?: string, stage?: string) => void} updateProgress
 * @property {(target: WorkflowStep) => void} updateWorkflowStepper
 * @property {() => void} updateStepAvailability
 * @property {(open: boolean) => void} setSettingsOpen
 * @property {() => void} toggleSettingsOpen
 * @property {() => void} ensureSettingsToggleIcon
 * @property {() => void} initLayoutResizePolicy
 * @property {(delay?: number) => void} scheduleLayoutRerender
 * @property {(target: StageKey) => void} switchStage
 * @property {(options?: { keepLandingVisible?: boolean }) => void} showWorkspace
 * @property {() => void} populateGridTabs
 * @property {(btn: HTMLElement, onChange?: (on: boolean) => void) => void} setupToggle
 * @property {(btn: HTMLElement, onChange?: (on: boolean) => void) => void} setupLockedOnToggle
 * @property {(id: string, show: boolean) => void} toggleConditional
 * @property {(btn: HTMLElement) => boolean} isToggleOn
 * @property {(btn: HTMLElement, on: boolean, labels: { shortSel: string, fullSel: string, prefix: string }) => void} applyLabeledToggle
 * @property {<T>(button: HTMLElement, fn: () => T | Promise<T>) => Promise<T | undefined>} runEditAction
 * @property {(button: HTMLElement, busy: boolean) => void} setEditActionBusy
 */

/**
 * @typedef {object} FileSessionService
 * @property {() => string} getBidsProject
 * @property {() => string[]} getBidsMuscleNames
 * @property {() => { subject?: string, task?: string, session?: string, run?: string, acquisition?: string }} getBidsEntityInputs
 * @property {() => JsonObject} getBidsSaveFields
 * @property {() => JsonObject} collectBidsEntities
 * @property {(entities: Partial<BidsEntities> & { project?: string }) => void} setBidsEntitiesInput
 * @property {(data: JsonObject) => void} applyPreviewMetadata
 * @property {(file: FileRef | null, data?: JsonObject) => void} applySessionInfoFromDecomposition
 * @property {() => void} renderBidsAutoInfo
 * @property {() => void} renderBidsMuscleFields
 * @property {(payload: JsonObject, fallbackName?: string) => Promise<{ mode: string, path: string }>} persistNpzBySaveTarget
 * @property {() => void} clearUploadFormatError
 * @property {() => void} showUnsupportedUploadFormatError
 * @property {(file: FileRef) => "raw" | "decomposition" | "ambiguous_mat" | "unsupported"} detectLandingFileType
 * @property {(active: boolean) => void} setUploadLoading
 */

/**
 * @typedef {object} QcStage
 * @property {() => void} populateAuxSelector
 * @property {() => void} renderAuxiliaryChannels
 * @property {(gridIdx: number, start?: number, end?: number | null) => Promise<void>} requestQcGridWindow
 * @property {(options?: { silentFailure?: boolean, filepath?: string }) => Promise<boolean>} requestPreview
 * @property {(path: string, name: string, options?: { silentPreviewFailure?: boolean }) => Promise<boolean>} handleRawFilePath
 * @property {(waitForMiniPlots?: boolean) => Promise<void> | void} renderChannelQC
 * @property {(canvasId: RoiCanvasId) => void} enableRoiSelection
 * @property {() => void} refreshVisuals
 * @property {(nwin: number) => void} syncRois
 * @property {() => Promise<boolean>} runAutoQc
 * @property {() => void} toggleArtifactMode
 * @property {() => void} removeLastArtifact
 * @property {(idx: number) => void} setSelectedGrid
 */

/**
 * @typedef {object} RunStage
 * @property {(gridIdx: number) => number[]} getMuIndicesForGrid
 * @property {() => void} renderMuDropdowns
 * @property {() => void} renderMuExplorer
 * @property {() => Promise<void>} autoSaveRunDecomposition
 * @property {(msg: JsonObject) => void} handleStreamMessage
 * @property {() => Promise<void>} runDecomposition
 * @property {() => void} updateStartAvailability
 * @property {() => DecomposeParams} buildParams
 */

/**
 * @typedef {object} EditStage
 * @property {(gridIdx: number) => number[]} getEditMuIndices
 * @property {() => void} ensureEditFlagged
 * @property {(muIdx: number) => number[]} getRawPulse
 * @property {(muIdx: number) => number[]} getDisplayPulse
 * @property {() => void} backupEditMu
 * @property {() => void} recomputeEditDirty
 * @property {() => void} refreshEditTotals
 * @property {() => number} getEditTotalSamples
 * @property {() => PulseViewMeta} getPulseViewMeta
 * @property {() => number} getPulsePlotHeight
 * @property {() => number} getDrPlotHeight
 * @property {(entry: EditHistoryEntry) => void} appendEditHistory
 * @property {() => void} resetEditState
 * @property {() => void} refreshEditModeButtons
 * @property {(mode: EditMode | null, message?: string) => void} setEditMode
 * @property {() => void} renderEditDropdowns
 * @property {() => void} renderEditExplorer
 * @property {() => void} renderInstantaneousDr
 * @property {() => void} bindEditCanvas
 * @property {() => void} bindEditDrCanvas
 * @property {() => void} bindEditTimeline
 * @property {(action: RoiAction, payload: RoiEditRequest) => Promise<void>} requestRoiEdit
 * @property {(mode: string) => Promise<void>} requestFilterUpdate
 * @property {() => Promise<void>} updateMuFilter
 * @property {(sel: Selection) => void} addSpikesInSelection
 * @property {(sel: Selection) => void} addArtifactInSelection
 * @property {(sel: Selection) => void} deleteSpikesInSelection
 * @property {(sel: Selection) => void} deleteDrInSelection
 * @property {() => void} restoreEditBackup
 * @property {() => Promise<void>} removeOutliers
 * @property {() => Promise<void>} flagMuForDeletion
 * @property {() => void} resetCurrentMuEdits
 * @property {() => void} duplicateMu
 * @property {() => Promise<void>} removeDuplicateMus
 * @property {() => Promise<void>} saveEditedFile
 * @property {(file: FileRef, path: string) => Promise<void>} loadDecompositionForEdit
 * @property {(path: string) => Promise<void>} loadDecompositionForEditByPath
 */

/** @typedef {Core & UiService & FileSessionService & QcStage & RunStage & EditStage} App */

export {};
