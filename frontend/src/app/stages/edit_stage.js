import {
  saveEditedFile as saveEditedFileFeature,
  loadDecompositionForEdit as loadDecompositionForEditFeature,
  handleDecompositionFile as handleDecompositionFileFeature,
  ensureEditFlagged as ensureEditFlaggedFeature,
  getRawPulse as getRawPulseFeature,
  getDisplayPulse as getDisplayPulseFeature,
  backupEditMu as backupEditMuFeature,
  restoreEditBackup as restoreEditBackupFeature,
  recomputeEditDirty as recomputeEditDirtyFeature,
  getEditTotalSamples as getEditTotalSamplesFeature,
  getPulseViewMeta as getPulseViewMetaFeature,
  refreshEditTotals as refreshEditTotalsFeature,
  resetEditState as resetEditStateFeature,
  renderEditExplorer as renderEditExplorerFeature,
  renderInstantaneousDr as renderInstantaneousDrFeature,
  requestRoiEdit as requestRoiEditFeature,
  requestFilterUpdate as requestFilterUpdateFeature,
  addSpikesInSelection as addSpikesInSelectionFeature,
  deleteSpikesInSelection as deleteSpikesInSelectionFeature,
  deleteDrInSelection as deleteDrInSelectionFeature,
  removeOutliers as removeOutliersFeature,
  flagMuForDeletion as flagMuForDeletionFeature,
  resetCurrentMuEdits as resetCurrentMuEditsFeature,
  bindEditCanvas as bindEditCanvasFeature,
  bindEditDrCanvas as bindEditDrCanvasFeature,
  duplicateMu as duplicateMuFeature,
  removeDuplicateMus as removeDuplicateMusFeature,
} from "../../features/edit.js";
import {
  appendEditHistoryEntry,
  setEditBidsRoot,
  setEditCurrentMu,
  setEditCurrentMuGrid,
} from "../../state/actions.js";
import { getEditMuIndicesForGrid } from "../../state/selectors.js";

export function createEditStageService(deps) {
  const {
    state,
    els,
    API_BASE,
    apiFetch,
    apiJson,
    COLORS,
    drawSeries,
    getCanvasPlotMetrics,
    getSuggestedNpzName,
    persistNpzBySaveTarget,
    getBidsMuscleNames,
    getBidsRoot,
    buildEntityLabelFromSession,
    applySessionInfoFromDecomposition,
    showWorkspace,
    switchStage,
    setUploadLoading,
    setEditStatus,
    setEditMode,
    refreshEditModeButtons,
    inferBidsRootFromSelectedPath,
    renderBidsMuscleFields,
  } = deps;

  function ensureEditFlagged() {
    ensureEditFlaggedFeature(state);
  }

  function getRawPulse(muIdx) {
    return getRawPulseFeature(state, muIdx);
  }

  function getDisplayPulse(muIdx) {
    return getDisplayPulseFeature(state, muIdx);
  }

  function backupEditMu() {
    backupEditMuFeature(state);
  }

  function recomputeEditDirty() {
    recomputeEditDirtyFeature(state);
  }

  function appendEditHistory(entry) {
    appendEditHistoryEntry(state, { ...entry, timestamp: new Date().toISOString() });
  }

  function getEditTotalSamples() {
    return getEditTotalSamplesFeature(state);
  }

  function getPulseViewMeta() {
    return getPulseViewMetaFeature(state);
  }

  function refreshEditTotals() {
    refreshEditTotalsFeature(state);
  }

  function resetEditState() {
    resetEditStateFeature({ state, els, refreshEditModeButtons });
  }

  function getEditMuIndices(gridIdx) {
    return getEditMuIndicesForGrid(state, gridIdx);
  }

  function renderEditDropdowns() {
    const gridSel = els.editMuGridSelect;
    const muSel = els.editMuSelect;
    if (!gridSel || !muSel) return;

    gridSel.innerHTML = "";
    (state.edit.gridNames || []).forEach((name, idx) => {
      const opt = document.createElement("option");
      opt.value = String(idx);
      opt.textContent = `Grid ${idx + 1}${name ? ` • ${name}` : ""}`;
      gridSel.appendChild(opt);
    });

    let targetGrid = state.edit.currentMuGrid || 0;
    let mus = getEditMuIndices(targetGrid);
    if (!mus.length && state.edit.gridNames?.length) {
      for (let g = 0; g < state.edit.gridNames.length; g++) {
        const list = getEditMuIndices(g);
        if (list.length) {
          targetGrid = g;
          mus = list;
          setEditCurrentMuGrid(state, g, { resetView: false });
          break;
        }
      }
    }
    gridSel.value = String(targetGrid);

    muSel.innerHTML = "";
    if (!mus.length) {
      const opt = document.createElement("option");
      opt.value = "";
      opt.textContent = "No motor units";
      muSel.appendChild(opt);
      muSel.disabled = true;
      return;
    }
    muSel.disabled = false;
    mus.forEach((muIdx) => {
      const opt = document.createElement("option");
      opt.value = String(muIdx);
      opt.textContent = `MU ${muIdx + 1}`;
      muSel.appendChild(opt);
    });
    const currentMu = state.edit.currentMu;
    if (!mus.includes(currentMu)) {
      setEditCurrentMu(state, mus[0], { resetView: false });
    }
    muSel.value = String(state.edit.currentMu);
  }

  function renderInstantaneousDr() {
    renderInstantaneousDrFeature({
      state,
      els,
      COLORS,
      drawSeries,
      getEditTotalSamples,
      ensureEditFlagged,
    });
  }

  function renderEditExplorer() {
    renderEditExplorerFeature({
      els,
      state,
      COLORS,
      drawSeries,
      renderEditDropdowns,
      getDisplayPulse,
      renderInstantaneousDr,
    });
  }

  function restoreEditBackup() {
    restoreEditBackupFeature({
      els,
      state,
      setEditStatus,
      renderEditExplorer,
      recomputeEditDirty,
      ensureEditFlagged,
    });
  }

  async function requestRoiEdit(action, payload) {
    return requestRoiEditFeature(
      {
        state,
        API_BASE,
        apiJson,
        setEditStatus,
        ensureEditFlagged,
        setEditMode,
        recomputeEditDirty,
        renderEditExplorer,
        appendEditHistory,
      },
      action,
      payload,
    );
  }

  async function requestFilterUpdate(mode) {
    return requestFilterUpdateFeature(
      {
        state,
        els,
        API_BASE,
        apiJson,
        setEditStatus,
        getBidsRoot,
        getRawPulse,
        backupEditMu,
        buildEntityLabelFromSession,
        ensureEditFlagged,
        recomputeEditDirty,
        refreshEditTotals,
        renderEditExplorer,
        appendEditHistory,
      },
      mode,
    );
  }

  function updateMuFilter() {
    return requestFilterUpdate("update-filter");
  }

  function addSpikesInSelection(sel) {
    return addSpikesInSelectionFeature(
      {
        state,
        els,
        getRawPulse,
        backupEditMu,
        getPulseViewMeta,
        getCanvasPlotMetrics,
        requestRoiEditFn: requestRoiEdit,
      },
      sel,
    );
  }

  function deleteSpikesInSelection(sel) {
    return deleteSpikesInSelectionFeature(
      {
        state,
        els,
        getRawPulse,
        backupEditMu,
        getPulseViewMeta,
        getCanvasPlotMetrics,
        requestRoiEditFn: requestRoiEdit,
      },
      sel,
    );
  }

  function deleteDrInSelection(sel) {
    return deleteDrInSelectionFeature(
      {
        state,
        els,
        backupEditMu,
        getEditTotalSamples,
        getCanvasPlotMetrics,
        getRawPulse,
        requestRoiEditFn: requestRoiEdit,
      },
      sel,
    );
  }

  async function removeOutliers() {
    return removeOutliersFeature({
      state,
      API_BASE,
      apiJson,
      setEditStatus,
      getRawPulse,
      backupEditMu,
      ensureEditFlagged,
      recomputeEditDirty,
      renderEditExplorer,
      appendEditHistory,
    });
  }

  async function flagMuForDeletion() {
    return flagMuForDeletionFeature({
      state,
      API_BASE,
      apiJson,
      setEditStatus,
      getRawPulse,
      backupEditMu,
      ensureEditFlagged,
      recomputeEditDirty,
      renderEditExplorer,
      appendEditHistory,
    });
  }

  function resetCurrentMuEdits() {
    resetCurrentMuEditsFeature({
      state,
      ensureEditFlagged,
      recomputeEditDirty,
      renderEditExplorer,
    });
  }

  async function removeDuplicateMus() {
    return removeDuplicateMusFeature({
      state,
      API_BASE,
      apiJson,
      setEditStatus,
      ensureEditFlagged,
      recomputeEditDirty,
      renderEditExplorer,
      appendEditHistory,
    });
  }

  function duplicateMu() {
    duplicateMuFeature({
      state,
      setEditStatus,
      ensureEditFlagged,
      recomputeEditDirty,
      renderEditExplorer,
      appendEditHistory,
    });
  }

  function bindEditCanvas() {
    bindEditCanvasFeature({
      els,
      state,
      getRawPulse,
      getCanvasPlotMetrics,
      renderEditExplorer,
      setEditStatus,
      addSpikesInSelection,
      deleteSpikesInSelection,
      setEditMode,
    });
  }

  function bindEditDrCanvas() {
    bindEditDrCanvasFeature({
      els,
      state,
      getCanvasPlotMetrics,
      getEditTotalSamples,
      renderEditExplorer,
      deleteDrInSelection,
    });
  }

  function saveEditedFile() {
    return saveEditedFileFeature({
      state,
      getSuggestedNpzName,
      persistNpzBySaveTarget,
      getBidsMuscleNames,
      setEditStatus,
      recomputeEditDirty,
    });
  }

  function loadDecompositionForEdit(file, absolutePath) {
    return loadDecompositionForEditFeature(
      {
        state,
        apiFetch,
        apiJson,
        API_BASE,
        applySessionInfoFromDecomposition,
        ensureEditFlagged,
        recomputeEditDirty,
        showWorkspace,
        switchStage,
        renderEditExplorer,
        renderBidsMuscleFields,
        setUploadLoading,
        setEditStatus,
        resetEditState,
        els,
      },
      file,
      absolutePath,
    );
  }

  function loadDecompositionForEditByPath(path) {
    const name = path.split("/").pop().split("\\").pop() || path;
    const bidsRoot = inferBidsRootFromSelectedPath(path);
    if (els.editBidsRoot) els.editBidsRoot.value = bidsRoot;
    setEditBidsRoot(state, bidsRoot);
    return loadDecompositionForEdit({ name }, path);
  }

  function handleDecompositionFile(file) {
    return handleDecompositionFileFeature(
      { loadDecompositionForEdit: (f) => loadDecompositionForEdit(f) },
      file,
    );
  }

  return {
    getEditMuIndices,
    ensureEditFlagged,
    getRawPulse,
    getDisplayPulse,
    backupEditMu,
    recomputeEditDirty,
    refreshEditTotals,
    restoreEditBackup,
    renderEditExplorer,
    renderInstantaneousDr,
    bindEditCanvas,
    bindEditDrCanvas,
    requestRoiEdit,
    requestFilterUpdate,
    updateMuFilter,
    addSpikesInSelection,
    deleteSpikesInSelection,
    deleteDrInSelection,
    removeOutliers,
    flagMuForDeletion,
    resetCurrentMuEdits,
    saveEditedFile,
    loadDecompositionForEdit,
    loadDecompositionForEditByPath,
    handleDecompositionFile,
    duplicateMu,
    removeDuplicateMus,
  };
}
