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

/** @typedef {import("../state/state.js").State} State */
/** @typedef {import("./dom.js").Els} Els */
/** @typedef {ReturnType<typeof import("../api/client.js").createApiClient>} Api */
/** @typedef {"qc" | "run" | "edit"} StageKey */
/** @typedef {"import" | StageKey} WorkflowStep A step of the header stepper. */
/** @typedef {import("../state/state.js").FileRef} FileRef */
/** @typedef {import("../state/state.js").Selection} Selection */
/** @typedef {import("../state/state.js").EditMode} EditMode */
/** @typedef {import("../state/state.js").EditHistoryEntry} EditHistoryEntry */
/** @typedef {import("../io/bids.js").BidsEntities} BidsEntities */
/** @typedef {import("../editing/operations.js").PulseViewMeta} PulseViewMeta */
/** @typedef {import("../decomp/params.js").DecomposeParams} DecomposeParams */
/** @typedef {"emgCanvas" | "auxCanvas"} RoiCanvasId Canvases that take ROI and artifact drags. */
/** @typedef {"add-spikes" | "add-artifact" | "delete-spikes"} RoiAction */

/**
 * A box drawn on an edit canvas, in samples and pulse-train units (Hz on the
 * discharge-rate plot).
 *
 * @typedef {object} RoiEditRequest
 * @property {number} muIdx
 * @property {number} xStart
 * @property {number} xEnd
 * @property {number} yMin
 * @property {number} [yMax]
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
 * @property {(target: WorkflowStep) => void} updateWorkflowStepper
 * @property {() => void} updateStepAvailability
 * @property {(open: boolean) => void} setSettingsOpen
 * @property {() => void} scheduleLayoutRerender Redraw the visible stage at the next frame, once however often asked.
 * @property {(target: StageKey) => void} switchStage
 * @property {(target: StageKey) => void} showWorkspace Leave the landing page for `target`'s page.
 * @property {() => void} populateGridTabs
 */

/**
 * @typedef {object} FileSessionService
 * @property {() => string} getBidsProject
 * @property {(project: string) => void} setBidsProject The Project field, and the edit session's project with it.
 * @property {(fileName: string) => void} resetSessionForm The entity fields back to their defaults for a new raw file.
 * @property {() => string[]} getBidsMuscleNames
 * @property {() => JsonObject} collectBidsEntities
 * @property {() => boolean} checkSessionForm Whether every required session field is filled; if not, mark the empty ones and open the panel on them.
 * @property {(entities: Partial<BidsEntities> & { project?: string }) => void} setBidsEntitiesInput
 * @property {(data: JsonObject) => void} applyPreviewMetadata
 * @property {(file: FileRef | null, data?: JsonObject) => void} applySessionInfoFromDecomposition
 * @property {() => void} prefillHardwareFields Fill the empty manufacturer, device and power-line fields from the file's metadata.
 * @property {() => void} renderBidsMuscleFields
 * @property {(payload: JsonObject, fallbackName?: string) => Promise<{ path: string }>} persistNpzBySaveTarget
 * @property {(payload: JsonObject) => JsonObject} withBidsSaveFields
 * @property {(active: boolean) => void} setUploadLoading
 */

/**
 * @typedef {object} QcStage
 * @property {() => void} populateAuxSelector
 * @property {() => void} renderAuxiliaryChannels
 * @property {() => void} ensureQcTraces Fetch the current grid's channel traces over the first window, unless they are shown or on their way.
 * @property {(path: string, name: string, options?: { silentPreviewFailure?: boolean }) => Promise<boolean>} handleRawFilePath
 * @property {() => void} renderChannelQC Draw the current grid's channels (the first grid when the data has no other), and ask for its traces.
 * @property {() => void} refreshVisuals
 * @property {() => void} scheduleRefreshVisuals Redraw the QC plots at the next frame, once however often asked.
 * @property {(idx: number) => void} setSelectedGrid
 */

/**
 * @typedef {object} RunStage
 * @property {() => void} renderRunStage Draw the run page for the current run; it is empty without one.
 * @property {() => void} scheduleRunRender Redraw the run page at the next frame, once however often asked.
 * @property {() => Promise<void>} autoSaveRunDecomposition
 * @property {(msg: JsonObject) => void} handleStreamMessage
 * @property {() => void} updateStartAvailability
 * @property {() => DecomposeParams} buildParams
 */

/**
 * @typedef {object} EditStage
 * @property {() => number} getPulsePlotHeight
 * @property {() => void} resetEditState
 * @property {() => void} refreshEditModeButtons
 * @property {(mode: EditMode | null, message?: string) => void} setEditMode
 * @property {() => void} renderEditExplorer Settle the MU and view the data allows, draw the edit page, and ask for the pulse window it lacks.
 * @property {() => void} scheduleEditRender Redraw the edit plots at the next frame, once however often asked.
 * @property {() => void} ensureEditPulseView Fetch the current MU's window when the one shown is not it.
 * @property {(action: RoiAction, payload: RoiEditRequest) => Promise<void>} requestRoiEdit
 * @property {(sel: Selection) => void} addSpikesInSelection
 * @property {(sel: Selection) => void} addArtifactInSelection
 * @property {(sel: Selection) => void} deleteSpikesInSelection
 * @property {(path: string, options?: { open?: boolean }) => Promise<void>} loadDecompositionForEditByPath Load a decomposition into Edit; `open: false` stays on the current page.
 */

/** @typedef {Core & UiService & FileSessionService & QcStage & RunStage & EditStage} App */

export {};
