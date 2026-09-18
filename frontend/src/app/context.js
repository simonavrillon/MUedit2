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

/** @typedef {typeof import("./state.js").state} State */
/** @typedef {import("./dom.js").Els} Els */
/** @typedef {ReturnType<typeof import("../api/client.js").createApiClient>} Api */
/** @typedef {"qc" | "run" | "edit"} StageKey */
/** @typedef {"muted" | "success" | "error"} Tone */
/** @typedef {{ start: number, end: number }} Span */

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
 * @property {(target: string) => void} updateWorkflowStepper
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
 * @property {() => Record<string, string>} getBidsEntityInputs
 * @property {() => Record<string, any>} getBidsSaveFields
 * @property {() => Record<string, any>} collectBidsEntities
 * @property {(entities: Record<string, string>) => void} setBidsEntitiesInput
 * @property {(data: any) => void} applyPreviewMetadata
 * @property {(file: { name?: string }, data?: any) => void} applySessionInfoFromDecomposition
 * @property {() => void} renderBidsAutoInfo
 * @property {() => void} renderBidsMuscleFields
 * @property {(payload: any, fallbackName?: string) => Promise<{ mode: string, path: string }>} persistNpzBySaveTarget
 * @property {() => void} clearUploadFormatError
 * @property {() => void} showUnsupportedUploadFormatError
 * @property {(file: { name?: string }) => string} detectLandingFileType
 * @property {(active: boolean) => void} setUploadLoading
 */

/**
 * @typedef {object} QcStage
 * @property {() => void} populateAuxSelector
 * @property {() => void} renderAuxiliaryChannels
 * @property {(gridIdx: number, start?: number, end?: number) => Promise<void>} requestQcGridWindow
 * @property {(options?: { silentFailure?: boolean, filepath?: string }) => Promise<boolean | undefined>} requestPreview
 * @property {(path: string, name: string, options?: { silentPreviewFailure?: boolean }) => Promise<boolean>} handleRawFilePath
 * @property {(waitForMiniPlots?: boolean) => Promise<void> | void} renderChannelQC
 * @property {(canvasId: string) => void} enableRoiSelection
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
 * @property {(msg: any) => void} handleStreamMessage
 * @property {() => Promise<void>} runDecomposition
 * @property {() => void} updateStartAvailability
 * @property {() => Record<string, unknown>} buildParams
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
 * @property {() => any} getPulseViewMeta
 * @property {() => number} getPulsePlotHeight
 * @property {() => number} getDrPlotHeight
 * @property {(entry: Record<string, unknown>) => void} appendEditHistory
 * @property {() => void} resetEditState
 * @property {() => void} refreshEditModeButtons
 * @property {(mode: string | null, message?: string) => void} setEditMode
 * @property {() => void} renderEditDropdowns
 * @property {() => void} renderEditExplorer
 * @property {() => void} renderInstantaneousDr
 * @property {() => void} bindEditCanvas
 * @property {() => void} bindEditDrCanvas
 * @property {() => void} bindEditTimeline
 * @property {(action: string, payload: any) => Promise<void>} requestRoiEdit
 * @property {(mode: string) => Promise<void>} requestFilterUpdate
 * @property {() => Promise<void>} updateMuFilter
 * @property {(sel: Span) => void} addSpikesInSelection
 * @property {(sel: Span) => void} addArtifactInSelection
 * @property {(sel: Span) => void} deleteSpikesInSelection
 * @property {(sel: any) => void} deleteDrInSelection
 * @property {() => void} restoreEditBackup
 * @property {() => Promise<void>} removeOutliers
 * @property {() => Promise<void>} flagMuForDeletion
 * @property {() => void} resetCurrentMuEdits
 * @property {() => void} duplicateMu
 * @property {() => Promise<void>} removeDuplicateMus
 * @property {() => Promise<void>} saveEditedFile
 * @property {(file: { name?: string }, path: string) => Promise<void>} loadDecompositionForEdit
 * @property {(path: string) => Promise<void>} loadDecompositionForEditByPath
 */

/** @typedef {Core & UiService & FileSessionService & QcStage & RunStage & EditStage} App */

export {};
