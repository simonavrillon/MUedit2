import {
  setEditBidsRoot,
  setEditCurrentMu,
  setEditCurrentMuGrid,
} from "../state/actions.js";

/**
 * @typedef {import('../app/deps.js').EditSetupDeps} EditSetupDeps
 */

/**
 * @param {EditSetupDeps} deps
 */
export function setupEditEvents(deps) {
  const {
    els,
    state,
    DEFAULT_BIDS_ROOT,
    bindEditCanvas,
    bindEditDrCanvas,
    renderEditExplorer,
    runEditAction,
    saveEditedFile,
    resetCurrentMuEdits,
    updateMuFilter,
    removeOutliers,
    flagMuForDeletion,
    restoreEditBackup,
    setEditMode,
    refreshEditModeButtons,
    handleKeyboardNavigation,
  } = deps;

  bindEditCanvas();
  bindEditDrCanvas();

  if (els.editBidsRoot && !els.editBidsRoot.value.trim()) {
    els.editBidsRoot.value = DEFAULT_BIDS_ROOT;
    setEditBidsRoot(state, DEFAULT_BIDS_ROOT);
  }

  els.editMuGridSelect?.addEventListener("change", (e) => {
    const idx = Number(e.target.value) || 0;
    setEditCurrentMuGrid(state, idx, { resetView: true });
    renderEditExplorer();
  });

  els.editMuSelect?.addEventListener("change", (e) => {
    const idx = Number(e.target.value);
    setEditCurrentMu(state, idx, { resetView: true });
    renderEditExplorer();
  });

  els.editSaveBtn?.addEventListener("click", () => {
    void runEditAction(els.editSaveBtn, saveEditedFile);
  });
  els.editResetBtn?.addEventListener("click", () => {
    void runEditAction(els.editResetBtn, () => resetCurrentMuEdits());
  });
  els.editUpdateBtn?.addEventListener("click", () => {
    void runEditAction(els.editUpdateBtn, updateMuFilter);
  });
  els.editOutliersBtn?.addEventListener("click", () => {
    void runEditAction(els.editOutliersBtn, () => removeOutliers());
  });
  els.editFlagBtn?.addEventListener("click", () => {
    void runEditAction(els.editFlagBtn, () => flagMuForDeletion());
  });
  els.editUndoBtn?.addEventListener("click", () => {
    void runEditAction(els.editUndoBtn, () => restoreEditBackup());
  });
  els.editAddBtn?.addEventListener("click", () => {
    setEditMode("add", "Drag a box on pulse train to add spikes");
  });
  els.editDeleteSpikeBtn?.addEventListener("click", () => {
    setEditMode("delete_spikes", "Drag a box on pulse train to delete spikes");
  });

  els.editBidsRoot?.addEventListener("input", (e) => {
    setEditBidsRoot(state, e.target.value);
  });

  refreshEditModeButtons();
  window.addEventListener("keydown", handleKeyboardNavigation);
}
