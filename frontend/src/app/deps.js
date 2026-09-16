/**
 * Shared dependency contracts for setup/orchestrator modules.
 * These typedefs make dependency injection explicit and keep module boundaries stable.
 *
 * Inside each create*Service factory, feature functions receive one shared
 * context bag (`ctx`): the factory's own deps spread together with its local
 * helpers. Features destructure only the keys they use.
 */

/**
 * @typedef {Object} ImportSetupDeps
 * @property {Object} els
 * @property {Object} state
 * @property {Function} handleNativeDialogOpen
 * @property {Function} setStatus
 * @property {Function} showWorkspace
 * @property {Function} switchStage
 * @property {Function} updateWorkflowStepper
 */

/**
 * @typedef {Object} RunSetupDeps
 * @property {Object} els
 * @property {Object} state
 * @property {Function} runDecomposition
 * @property {Function} enableRoiSelection
 * @property {Function} syncRois
 * @property {Function} refreshVisuals
 * @property {Function} setupToggle
 * @property {Function} setupLockedOnToggle
 * @property {Function} toggleConditional
 * @property {Function} updateStartAvailability
 * @property {Function} renderAuxiliaryChannels
 * @property {Function} renderMuExplorer
 * @property {Function} [runAutoQc]
 * @property {Function} [toggleArtifactMode]
 * @property {Function} [removeLastArtifact]
 */

/**
 * @typedef {Object} EditSetupDeps
 * @property {Object} els
 * @property {Object} state
 * @property {Function} bindEditCanvas
 * @property {Function} bindEditDrCanvas
 * @property {Function} bindEditTimeline
 * @property {Function} renderEditExplorer
 * @property {Function} runEditAction
 * @property {Function} saveEditedFile
 * @property {Function} resetCurrentMuEdits
 * @property {Function} updateMuFilter
 * @property {Function} removeOutliers
 * @property {Function} flagMuForDeletion
 * @property {Function} duplicateMu
 * @property {Function} removeDuplicateMus
 * @property {Function} restoreEditBackup
 * @property {Function} setEditMode
 * @property {Function} refreshEditModeButtons
 * @property {Function} handleKeyboardNavigation
 * @property {Function} applyLabeledToggle
 */

/**
 * @typedef {Object} LayoutSetupDeps
 * @property {Object} els
 * @property {Function} toggleSettingsOpen
 * @property {Function} setSettingsOpen
 * @property {Function} initLayoutResizePolicy
 */

export {};

/**
 * @typedef {Object} UiService
 * @property {Function} setStatus
 * @property {Function} setEditStatus
 * @property {Function} setRunPhase
 * @property {Function} updateProgress
 * @property {Function} updateWorkflowStepper
 * @property {Function} updateStepAvailability
 * @property {Function} setSettingsOpen
 * @property {Function} toggleSettingsOpen
 * @property {Function} ensureSettingsToggleIcon
 * @property {Function} initLayoutResizePolicy
 * @property {Function} scheduleLayoutRerender
 * @property {Function} showWorkspace
 * @property {Function} switchStage
 * @property {Function} setupToggle
 * @property {Function} setupLockedOnToggle
 * @property {Function} toggleConditional
 * @property {Function} isToggleOn
 * @property {Function} runEditAction
 */

/**
 * @typedef {Object} FileSessionService
 * @property {Function} getBidsProject
 * @property {Function} getBidsMuscleNames
 * @property {Function} clearUploadFormatError
 * @property {Function} showUnsupportedUploadFormatError
 * @property {Function} detectLandingFileType
 * @property {Function} setUploadLoading
 */

/**
 * @typedef {Object} QcStageService
 * @property {Function} populateAuxSelector
 * @property {Function} renderAuxiliaryChannels
 * @property {Function} requestQcGridWindow
 * @property {Function} requestPreview
 * @property {Function} handleRawFilePath
 * @property {Function} renderChannelQC
 * @property {Function} enableRoiSelection
 * @property {Function} refreshVisuals
 * @property {Function} syncRois
 * @property {Function} runAutoQc
 * @property {Function} toggleArtifactMode
 * @property {Function} removeLastArtifact
 */

/**
 * @typedef {Object} RunStageService
 * @property {Function} getMuIndicesForGrid
 * @property {Function} renderMuDropdowns
 * @property {Function} renderMuExplorer
 * @property {Function} autoSaveRunDecomposition
 * @property {Function} handleStreamMessage
 * @property {Function} runDecomposition
 */

/**
 * @typedef {Object} EditStageService
 * @property {Function} getEditMuIndices
 * @property {Function} ensureEditFlagged
 * @property {Function} getRawPulse
 * @property {Function} getDisplayPulse
 * @property {Function} backupEditMu
 * @property {Function} recomputeEditDirty
 * @property {Function} refreshEditTotals
 * @property {Function} restoreEditBackup
 * @property {Function} renderEditExplorer
 * @property {Function} renderInstantaneousDr
 * @property {Function} bindEditCanvas
 * @property {Function} bindEditDrCanvas
 * @property {Function} bindEditTimeline
 * @property {Function} requestRoiEdit
 * @property {Function} requestFilterUpdate
 * @property {Function} updateMuFilter
 * @property {Function} addSpikesInSelection
 * @property {Function} addArtifactInSelection
 * @property {Function} deleteSpikesInSelection
 * @property {Function} deleteDrInSelection
 * @property {Function} removeOutliers
 * @property {Function} flagMuForDeletion
 * @property {Function} resetCurrentMuEdits
 * @property {Function} duplicateMu
 * @property {Function} removeDuplicateMus
 * @property {Function} saveEditedFile
 * @property {Function} loadDecompositionForEdit
 * @property {Function} loadDecompositionForEditByPath
 */
