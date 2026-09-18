# 03 - Edit Stage Controls

The edit stage is the most complex section. This document lists every control button, canvas interaction, keyboard shortcut, and operation available to the user.

## Edit Stage Layout

```
+----------------------------------------------------------------+
|  Editing Workspace                                              |
|  [editMuGridSelect v] [editMuSelect v]                          |
|                                                                |
|  +--- Toolbar ---------------------------------------------+  |
|  | [Flag MU]  [Remove Duplicates]  [Duplicate MU]            |  |
|  | [Remove Outliers]  [Add Spike]  [Add Artifact]            |  |
|  | [Delete Spike/Artifact]                                   |  |
|  | [Update Filter]  [Peel-off: Off]  [Lock: Off]             |  |
|  | [Undo]  [Reset]  [Save]                                    |  |
|  +-----------------------------------------------------------+  |
|                                                                |
|  Shortcuts: < > R A X D Space P L <- -> up down               |
|  [editStatus pill]                                             |
+----------------------------------------------------------------+
|                                                                |
|  +-----------------------------------------------------------+ |
|  | Firing Rate (pps)          (#editDrCanvas)                 | |
|  +-----------------------------------------------------------+ |
|  +-----------------------------------------------------------+ |
|  | Pulse Train                (#editPulseCanvas)              | |
|  +-----------------------------------------------------------+ |
|  [-----------------------------------------------------------] |
|  Navigation Timeline (#editTimelineCanvas)                     |
+----------------------------------------------------------------+
```

---

## Control Buttons

### Button Reference Table

| Button ID | Label | Handler | API Call | Description |
|---|---|---|---|---|
| `editFlagBtn` | Flag MU | `flagMuForDeletion()` | `POST /edit/flag-mu` | Toggles deletion flag for current MU. Flagged MUs display zeroed pulse trains and are excluded from save. |
| `editDeduplicateBtn` | Remove Duplicates | `removeDuplicateMus()` | `POST /edit/remove-duplicates` | Removes duplicate MUs with the decomposition's rules (within each grid, then across grids unless `duplicatesbgrids` is false). Reorders all parallel arrays via `keepEditMus(kept_indices)`. |
| `editDuplicateBtn` | Duplicate MU | `duplicateMu()` | (local, no API) | Appends a copy of the current MU (distimes + pulse train) with a new UID `g{gridIdx}_mu{n}`. Switches to the new MU. |
| `editOutliersBtn` | Remove Outliers | `removeOutliers()` | `POST /edit/remove-outliers` | Removes outlier spikes from the current MU based on discharge rate statistics. |
| `editAddBtn` | Add Spike | `setEditMode("add")` | (no API yet) | Enters "add" mode. User then drags a box on the pulse canvas to add spikes. |
| `editAddArtifactBtn` | Add Artifact | `setEditMode("add_artifact")` | (no API yet) | Enters "add_artifact" mode. User drags on pulse canvas to mark an artifact region. |
| `editDeleteSpikeBtn` | Delete Spike/Artifact | `setEditMode("delete_spikes")` | (no API yet) | Enters "delete_spikes" mode. User drags on pulse canvas to delete spikes in the selected region. |
| `editUpdateBtn` | Update Filter | `updateMuFilter()` | `POST /edit/update-filter` | Re-runs the separation filter on the current MU using current distimes. Sends peel-off and lock-spike flags. 120s timeout. |
| `editPeelOffToggle` | Peel-off: Off/On | `applyLabeledToggle(...)` | (no direct API) | Toggle that sets `use_peeloff` flag, read by `requestFilterUpdate` when "Update Filter" is clicked. |
| `editLockSpikesToggle` | Lock: Off/On | `applyLabeledToggle(...)` | (no direct API) | Toggle that sets `lock_spikes` flag, read by `requestFilterUpdate` when "Update Filter" is clicked. |
| `editUndoBtn` | Undo | `restoreEditBackup()` | (local, no API) | Restores the last backup (single-level undo). Disabled when `state.edit.backup === null`. |
| `editResetBtn` | Reset | `resetCurrentMuEdits()` | (local, no API) | Restores current MU to its original loaded state (originalDistimes, originalPulseTrains). Clears artifacts and logs a `reset_mu` entry. |
| `editSaveBtn` | Save | `saveEditedFile()` | `POST /edit/save` | Saves the edited decomposition to a .npz file. Disabled until dirty. 120s timeout. |

### Button Busy State

All mutating edit actions are wrapped by `runEditAction(button, fn)` which:
- Prevents re-entrant clicks via `dataset.busy`
- Sets `is-running` CSS class + `aria-busy` on the button
- Disables the button visually during the async operation

---

## Canvas Interactions

### Pulse Train Canvas (`#editPulseCanvas`)

| Interaction | Action |
|---|---|
| Mouse drag (in "add" mode) | Selects a region; on release, calls `addSpikesInSelection(sel)` → `POST /edit/add-spikes` |
| Mouse drag (in "add_artifact" mode) | Selects a region; on release, calls `addArtifactInSelection(sel)` → `POST /edit/add-artifact` |
| Mouse drag (in "delete_spikes" mode) | Selects a region; on release, calls `deleteSpikesInSelection(sel)` → `POST /edit/delete-spikes` |
| Double-click | Resets view to full `[0, pulse.length]` and shows bookmark |

Renders:
- Pulse train line trace (uniform pulse color)
- Spike markers (purple circles at `COLORS.muPurple`)
- Artifact markers (larger, dark outline, `COLORS.artifactMarker`)
- Selection rectangles (draft during drag + committed)
- Bookmark (green vertical line + "You stopped here" label)

### Discharge Rate Canvas (`#editDrCanvas`)

| Interaction | Action |
|---|---|
| Mouse drag (always available) | Selects a DR range; on release, calls `deleteDrInSelection(sel)` → `POST /edit/delete-dr` |

Renders:
- Instantaneous discharge rate series (line trace, `COLORS.warning`)
- DR markers at midpoints between consecutive spikes
- Selection rectangles (draft + committed)
- "No data" message if flagged or no spikes

### Timeline Canvas (`#editTimelineCanvas`)

| Interaction | Action |
|---|---|
| Mouse drag | Pans the view window (maintains span, clamps to [0, total]) |
| Click (no drag) | Centers the view window on the click position |

Renders:
- Faint bar background
- Green marks for `spikes_added` / `artifacts_added` from the last history entry for this MU
- Red marks for `spikes_removed` / `artifacts_removed`
- Faint purple marks for current spike positions
- Purple view-window rectangle (draggable)

---

## Dropdowns

| Element ID | Type | Change Handler | Description |
|---|---|---|---|
| `editMuGridSelect` | `<select>` | `setEditCurrentMuGrid(state, idx, {resetView:true})` | Selects the active grid; resets view |
| `editMuSelect` | `<select>` | `setEditCurrentMu(state, idx, {resetView:true})` | Selects the active motor unit within the grid; resets view |

Dropdown auto-fallback: if the current grid has no MUs, `buildEditDropdownModel` falls back to the first grid that has MUs.

---

## Keyboard Shortcuts

All shortcuts fire only when `state.currentStage === "edit"` and focus is not in an `INPUT`, `TEXTAREA`, or `SELECT`.

| Key | Action | Function Called |
|---|---|---|
| `a` | Enter add-spikes mode | `setEditMode("add", "Drag a box on pulse train to add spikes")` |
| `d` | Enter delete-spikes mode | `setEditMode("delete_spikes", "Drag a box on pulse train to delete spikes")` |
| `x` | Enter add-artifact mode | `setEditMode("add_artifact", "Drag a box on pulse train to mark an artifact")` |
| `r` | Remove outliers | `runEditAction(editOutliersBtn, removeOutliers)` |
| `Space` | Update filter | `runEditAction(editUpdateBtn, updateMuFilter)` |
| `p` | Toggle peel-off | `applyLabeledToggle(editPeelOffToggle, ...)` |
| `l` | Toggle lock spikes | `applyLabeledToggle(editLockSpikesToggle, ...)` |
| `<` | Previous MU | `goToMu("prev", "edit")` |
| `>` | Next MU | `goToMu("next", "edit")` |
| `←` | Scroll left | `adjustView(view, total, "scroll_left")` |
| `→` | Scroll right | `adjustView(view, total, "scroll_right")` |
| `↑` | Zoom in | `adjustView(view, total, "zoom_in")` |
| `↓` | Zoom out | `adjustView(view, total, "zoom_out")` + `setShowBookmark(true)` |

---

## Editing Operations (`editing/operations.js`)

| Function | Parameters | What It Does |
|---|---|---|
| `ensureEditFlagged` | `(state)` | Ensures `state.edit.flagged` array matches `distimes` length; fills with `false` if mismatched |
| `getRawPulse` | `(state, muIdx)` | Returns `state.edit.pulseTrains[muIdx]` or `[]` |
| `getDisplayPulse` | `(state, muIdx)` | Returns raw pulse, or all-zeros if MU is flagged for deletion |
| `backupEditMu` | `(state)` | Snapshots current MU's `distimes`, `flagged`, `pulseTrain`, `artifactTimes` and the history length into `state.edit.backup` |
| `restoreEditBackup` | `(app)` | Restores the backup; drops that MU's history entries logged since the backup; clears backup; clears selections; re-renders |
| `recomputeEditDirty` | `(state)` | Compares `distimes` vs `originalDistimes` by JSON stringify; sets `state.edit.dirty` |
| `getEditTotalSamples` | `(state)` | Returns `totalSamples` or `pulseTrains[0].length` |
| `getPulseViewMeta` | `(state)` | Returns `{s, e, minVal, maxVal, span, slice}` for the current view window of the current MU's pulse |
| `refreshEditTotals` | `(state)` | Calls `setEditTotalSamples` with `getEditTotalSamples` |
| `buildEditDropdownModel` | `(state, getEditMuIndices)` | Pure logic: resolves target grid (first non-empty), MU options list, whether grid/MU switch is needed |
| `resetEditState` | `(app)` | Calls `resetEditSlice(state)` + `refreshEditModeButtons()` |
| `addSpikesInSelection` | `(app, sel)` | Converts canvas-pixel selection to sample/value coords, backs up MU, calls `requestRoiEdit("add-spikes", {...})` |
| `addArtifactInSelection` | `(app, sel)` | Same but calls `requestRoiEdit("add-artifact", {...})` |
| `deleteSpikesInSelection` | `(app, sel)` | Converts selection to value range, backs up MU, calls `requestRoiEdit("delete-spikes", {...})` with `artifact_times` |
| `deleteDrInSelection` | `(app, sel)` | Converts DR-canvas selection to a DR threshold; backs up MU; calls `requestRoiEdit("delete-dr", {...})` |
| `computeInstantaneousDr` | `(spikes, fsamp, totalSamples)` | Pure: builds a series of length `totalSamples` with DR values at midpoints between consecutive spikes |
| `duplicateMu` | `(app)` | Appends a copy of the current MU with a new UID `g{gridIdx}_mu{n}`, `n` above every uid the history has named; switches to the new MU; appends history `duplicate_mu` |
| `resetCurrentMuEdits` | `(app)` | Restores current MU's `distimes`/`pulseTrain` from `originalDistimes`/`originalPulseTrains`; clears artifacts; appends history `reset_mu` with the spikes/artifacts it changed; clears backup |

---

## ROI Edit Operations (Backend Calls)

Each ROI-based edit sends a request to the backend and updates local state from the response.

| Action | API Route | Payload Keys | Response Updates |
|---|---|---|---|
| `add-spikes` | `POST /edit/add-spikes` | `distimes, mu_index, pulse_train, fsamp, x_start, x_end, y_min, y_max` | `distimes` for current MU |
| `add-artifact` | `POST /edit/add-artifact` | same + `artifact_times` | `distimes` + `artifactTimes` for current MU |
| `delete-spikes` | `POST /edit/delete-spikes` | same + `artifact_times` | `distimes` + `artifactTimes` for current MU |
| `delete-dr` | `POST /edit/delete-dr` | `distimes, mu_index, pulse_train, fsamp, x_start, x_end, y_min` | `distimes` for current MU |
| `update-filter` | `POST /edit/update-filter` | `project, edit_signal_token, file_label, grid_index, mu_index, distimes, mu_grid_index, pulse_train, view_start, view_end, use_peeloff, lock_spikes, flagged, artifact_times` | `distimes` + `pulseTrain` for current MU |
| `remove-outliers` | `POST /edit/remove-outliers` | `distimes, mu_index, pulse_train, fsamp` | `distimes` for current MU |
| `remove-duplicates` | `POST /edit/remove-duplicates` | `distimes, fsamp, total_samples, mu_grid_index, parameters` | `keepEditMus(kept_indices)` reorders all parallel arrays together, remaps current MU/bookmark, clears the undo backup |
| `flag-mu` | `POST /edit/flag-mu` | `distimes, mu_index, flag` | `flagged` for current MU |
| `save` | `POST /edit/save` | `distimes, flagged, pulse_trains, total_samples, fsamp, grid_names, mu_grid_index, mu_uids, parameters, muscle, edit_history, artifact_times, entity_label, file_label, edit_signal_token, software_versions` | Saved path, `kept_indices`, `edit_history`; the backend drops flagged and duplicate MUs, and the edit state adopts both so it mirrors the file |

---

## Modification Flow (Per Edit)

```
1. backupEditMu()          -- snapshot current MU state to state.edit.backup
2. Convert canvas selection -> sample/value coordinates
3. requestRoiEdit(action, payload)
   -> api.editAction(action, payload)   POST /edit/<action>
   -> setEditDistimesForMu(muIdx, response.distimes)
   -> setEditArtifactTimesForMu(muIdx, response.artifact_times)  [if applicable]
4. ensureEditFlagged() + setEditFlagForMu(muIdx, false)  -- un-flag after edit
5. appendEditHistory(entry)  -- log entry with spikes_added/removed, artifacts_added/removed
6. setEditBookmark({muIdx, position}) + setShowBookmark(false)
7. Clear all selections + setEditMode(null)
8. recomputeEditDirty() + renderEditExplorer()
```

---

## Undo / Redo

### Undo (single-level)

- `backupEditMu()` snapshots the current MU's `distimes`, `flagged`, `pulseTrain`, `artifactTimes` into `state.edit.backup` **before** any mutating action.
- `restoreEditBackup()` restores that snapshot, then:
  - Calls `dropEditHistoryForMuSince(state, muUid, backup.historyLength)` to remove every entry the undone action logged (a delete can log both `delete_spikes` and `delete_artifact`; a no-op logs none)
  - Sets `state.edit.backup = null` (undo is one-shot)
  - Clears all selections
  - Recomputes dirty and re-renders
- The `editUndoBtn` is **disabled** when `state.edit.backup === null`.
- There is **no redo** — once undone, the backup is gone.

### Per-MU Reset (broader than undo)

- `resetCurrentMuEdits()` restores the current MU to its **original** loaded state (`originalDistimes`, `originalPulseTrains`), clears artifacts, logs a `reset_mu` entry, and clears the backup. Earlier entries are kept: after a reload they describe edits already in the baseline.

---

## Edit History Log

`state.edit.editHistory` is an array of entries, each tagged with:

| Field | Description |
|---|---|
| `type` | Entry type (see below) |
| `mu_uid` | Stable identifier of the MU |
| `timestamp` | When the edit was made |
| `spikes_added` | Count of spikes added |
| `spikes_removed` | Count of spikes removed |
| `artifacts_added` | Count of artifacts added |
| `artifacts_removed` | Count of artifacts removed |
| `view_start` / `view_end` | View window at time of edit |
| `use_peeloff` / `lock_spikes` | Filter flags at time of edit |
| `flagged` | Flag state |
| `source_mu_uid` | For duplicate operations |
| `removed_count` / `removed_mu_uids` | For dedup operations |

### Entry Types

`add_spikes`, `delete_spikes`, `delete_dr`, `add_artifact`, `delete_artifact`, `update_filter`, `remove_outliers`, `remove_duplicates`, `flag_mu`, `duplicate_mu`, `reset_mu`; the backend appends `remove_flagged` and `remove_duplicates` with `on_save: true` when saving drops MUs

History is **persisted to the saved file** (included in `editSave` payload as `edit_history`).

On **reload**, `loadDecompositionForEdit` reads `data.edit_history` and restores the view to the last edited MU/position.

### History Mutation Functions

| Function | Description |
|---|---|
| `appendEditHistoryEntry(state, entry)` | Push a new entry |
| `dropEditHistoryForMuSince(state, muUid, fromIndex)` | Remove a MU's entries at or after `fromIndex` (used by undo) |
| `setEditHistory(state, history)` | Replace entire array (used by load) |

---

## Status Feedback

| Element ID | Purpose |
|---|---|
| `editStatus` | `aria-live="polite"` status pill; updated by `setEditStatus(msg, kind)` |

Status messages are set after each operation (e.g., "Spikes added", "Filter updated", "MU flagged", "Saved to ...").

---

## Save Flow

```
User clicks Save (#editSaveBtn)
  -> runEditAction(editSaveBtn, saveEditedFile)
     -> saveEditedFile(app)
        1. Build payload from state.edit:
           - distimes, flagged, pulse_trains, total_samples, fsamp
           - grid_names, mu_grid_index, mu_uids, parameters
           - muscle, edit_history, artifact_times
           - entity_label (from BIDS entities)
           - file_label, edit_signal_token, software_versions
        2. persistNpzBySaveTarget(payload, fallbackName, fileSession, ui)
           -> api.editSave(payload)    POST /edit/save  [120s timeout]
           -> returns { mode, path, keptIndices, editHistory }
        3. setEditHistory(state, saved.editHistory)  [adopts the backend's remove_flagged/remove_duplicates entries]
        4. keepEditMus(state, saved.keptIndices) + renderEditExplorer()  [only if the save dropped MUs]
        5. setEditOriginalDistimes(state, state.edit.distimes) + recomputeEditDirty()  [clears dirty]
        6. setEditStatus("Edited decomposition saved to {path}", "success")
        7. (on error: handleError(err, setEditStatus, "Save failed"))
```
