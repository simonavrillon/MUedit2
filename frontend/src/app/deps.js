/**
 * Shared dependency contracts for setup/orchestrator modules.
 * These typedefs make dependency injection explicit and keep module boundaries stable.
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
 */

/**
 * @typedef {Object} EditSetupDeps
 * @property {Object} els
 * @property {Object} state
 * @property {string} DEFAULT_BIDS_ROOT
 * @property {Function} bindEditCanvas
 * @property {Function} bindEditDrCanvas
 * @property {Function} renderEditExplorer
 * @property {Function} runEditAction
 * @property {Function} saveEditedFile
 * @property {Function} resetCurrentMuEdits
 * @property {Function} updateMuFilter
 * @property {Function} removeOutliers
 * @property {Function} flagMuForDeletion
 * @property {Function} restoreEditBackup
 * @property {Function} setEditMode
 * @property {Function} refreshEditModeButtons
 * @property {Function} handleKeyboardNavigation
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
 * @property {Function} getBidsRoot
 * @property {Function} getBidsMuscleNames
 * @property {Function} clearUploadFormatError
 * @property {Function} showUnsupportedUploadFormatError
 * @property {Function} isSupportedSignalFile
 * @property {Function} detectLandingFileType
 * @property {Function} inferBidsRootFromSelectedPath
 * @property {Function} setUploadLoading
 */

/**
 * @typedef {Object} QcStageService
 * @property {Function} populateAuxSelector
 * @property {Function} renderAuxiliaryChannels
 * @property {Function} requestQcGridWindow
 * @property {Function} requestPreview
 * @property {Function} handleRawFile
 * @property {Function} handleLandingFile
 * @property {Function} handleRawFilePath
 * @property {Function} renderChannelQC
 * @property {Function} enableRoiSelection
 * @property {Function} refreshVisuals
 * @property {Function} syncRois
 */

/**
 * @typedef {Object} RunStageService
 * @property {Function} getMuIndicesForGrid
 * @property {Function} renderMuDropdowns
 * @property {Function} renderMuExplorer
 * @property {Function} autoDownloadRunDecomposition
 * @property {Function} handleStreamMessage
 * @property {Function} runDecomposition
 */

/**
 * @typedef {Object} EditStageService
 * @property {Function} ensureEditFlagged
 * @property {Function} restoreEditBackup
 * @property {Function} renderEditExplorer
 * @property {Function} bindEditCanvas
 * @property {Function} bindEditDrCanvas
 * @property {Function} updateMuFilter
 * @property {Function} removeOutliers
 * @property {Function} flagMuForDeletion
 * @property {Function} resetCurrentMuEdits
 * @property {Function} saveEditedFile
 * @property {Function} loadDecompositionForEdit
 * @property {Function} loadDecompositionForEditByPath
 * @property {Function} handleDecompositionFile
 */
