# 03 - Edit Stage Controls

The edit stage is the most complex section. This document lists every control button, canvas interaction, keyboard shortcut, and operation available to the user.

The decomposition being edited lives on the server, in an edit session (see
[backend 05-editing-operations.md](../backend/05-editing-operations.md)). The page holds its
token, every MU's discharge and artifact times, the per-MU fields and the edit history. It
fetches the current MU's pulse train for the window on screen only (`GET /series/pulse`). Every
edit is one `POST /edit/ops/{op}`, and the page applies the change the server returns.

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
| `editFlagBtn` | Flag MU | `flagMuForDeletion()` | `op: flag` | Toggles the deletion flag of the current MU. A flagged MU is drawn as a flat line and is dropped on save. |
| `editDeduplicateBtn` | Remove Duplicates | `removeDuplicateMus()` | `op: remove-duplicates` | Removes duplicate MUs with the decomposition's rules (within each grid, then across grids unless `duplicatesbgrids` is false). The change's `kept_indices` reorder every per-MU array (`keepEditMus`). Clears the undo stack. |
| `editDuplicateBtn` | Duplicate MU | `duplicateMu()` | `op: duplicate` | Appends a copy of the current MU with a new UID `g{grid}_mu{n}` and switches to it. |
| `editOutliersBtn` | Remove Outliers | `removeOutliers()` | `op: remove-outliers` | Removes outlier spikes from the current MU based on discharge rate statistics. |
| `editAddBtn` | Add Spike | `setEditMode("add")` | — | Enters "add" mode. The user then drags a box on the pulse canvas to add spikes. |
| `editAddArtifactBtn` | Add Artifact | `setEditMode("add_artifact")` | — | Enters "add_artifact" mode. The user drags a box on the pulse canvas to mark artifacts. |
| `editDeleteSpikeBtn` | Delete Spike/Artifact | `setEditMode("delete_spikes")` | — | Enters "delete_spikes" mode. The user drags a box on the pulse canvas to delete the spikes and artifacts in it. |
| `editUpdateBtn` | Update Filter | `updateMuFilter()` | `op: update-filter` | Refits the current MU's filter on the EMG in view. Sends the peel-off and lock-spike toggles and the project. 120s timeout. |
| `editPeelOffToggle` | Peel-off: Off/On | `applyLabeledToggle(...)` | — | Sets `use_peeloff`, read by `requestFilterUpdate`. |
| `editLockSpikesToggle` | Lock: Off/On | `applyLabeledToggle(...)` | — | Sets `lock_spikes`, read by `requestFilterUpdate`. |
| `editUndoBtn` | Undo | `undoEdit()` | `op: undo` | Takes back the last edit, whichever MU it touched (up to 100 levels). Disabled while `state.edit.canUndo` is false. |
| `editResetBtn` | Reset | `resetCurrentMuEdits()` | `op: reset` | Brings the current MU back to the file's discharge times and pulse train, and clears its artifacts and flag. |
| `editSaveBtn` | Save | `saveEditedFile()` | `POST /edit/session/save` | Saves the edited decomposition to a .npz file. Enabled once a decomposition is open. 120s timeout. |

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
| Mouse drag (in "add" mode) | Selects a region; on release, calls `addSpikesInSelection(sel)` → `op: add-spikes` |
| Mouse drag (in "add_artifact" mode) | Selects a region; on release, calls `addArtifactInSelection(sel)` → `op: add-artifact` |
| Mouse drag (in "delete_spikes" mode) | Selects a region; on release, calls `deleteSpikesInSelection(sel)` → `op: delete-spikes` |
| Double-click | Resets view to the whole recording and shows the bookmark |

The canvas draws `state.edit.pulseView`, the window `ensureEditPulseView` fetched: one min/max
pair per pixel column, or the samples when zoomed in far enough. It is refetched when the MU,
the view, the canvas width or the MU's `version` changes. `createViewFetcher` keeps one request
in flight and drops the windows asked for in between. A drawn box is only mapped to samples and
pulse values (`getPulseViewMeta`) once the window on screen matches the MU's current version.

Renders:
- Pulse train trace (uniform pulse color); a flagged MU is a flat line at 0
- Spike markers (purple circles at `COLORS.muPurple`), at the values the frame carries
- Artifact markers (larger, dark outline, `COLORS.artifactMarker`)
- Selection rectangles (draft during drag + committed)
- Bookmark (green vertical line + "You stopped here" label)

### Discharge Rate Canvas (`#editDrCanvas`)

| Interaction | Action |
|---|---|
| Mouse drag (always available) | Selects a DR range; on release, calls `deleteDrInSelection(sel)` → `op: delete-dr` |

Renders:
- Instantaneous discharge rate (`dischargeRates`: one point per interval, at its midpoint), 0 Hz at the bottom and the fastest rate in view (`fastestRateInView`) at the top
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
| `getPulseViewMeta` | `(state)` | `{s, e, minVal, maxVal, span}` of the window on screen, or `null` until it has been fetched for the current MU at its current version |
| `buildEditDropdownModel` | `(state, getEditMuIndices)` | Pure logic: resolves target grid (first non-empty), MU options list, whether grid/MU switch is needed |
| `resetEditState` | `(app)` | Calls `resetEditSlice(state)` + `refreshEditModeButtons()` |
| `dischargeRates` | `(spikes, fsamp, totalSamples)` | Pure: `{positions, rates}`, one rate (Hz) per positive interval at its midpoint |
| `fastestRateInView` | `({positions, rates}, view)` | Pure: the fastest rate whose midpoint is in view; the top of the rate plot |
| `addSpikesInSelection` | `(app, sel)` | Maps the drawn box to samples and a pulse value (`pulseBox`), calls `requestRoiEdit("add-spikes", …)` |
| `addArtifactInSelection` | `(app, sel)` | Same, with `requestRoiEdit("add-artifact", …)` |
| `deleteSpikesInSelection` | `(app, sel)` | Same, with the box's value range: `requestRoiEdit("delete-spikes", …)` |
| `deleteDrInSelection` | `(app, sel)` | Maps the rate-canvas box to a rate threshold on the plot's own scale; calls `requestRoiEdit("delete-dr", …)` |

## Edit Actions (`app/services/editing-service.js`)

Every action goes through `requestEditOp(app, op, args)`: it posts `/edit/ops/{op}` with the
session token, applies the change frame (`applyEditChange`) and refreshes the mode buttons.

| Function | `op` | Arguments sent | Then |
|---|---|---|---|
| `requestRoiEdit(app, action, payload)` | `add-spikes`, `add-artifact`, `delete-spikes`, `delete-dr` | `mu, x_start, x_end, y_min, y_max` | Bookmark at the box's centre, clear the selections, leave the mode |
| `requestFilterUpdate(app)` | `update-filter` | `mu, view_start, view_end, use_peeloff, lock_spikes, project` | Bookmark at the view's centre |
| `removeOutliers(app)` | `remove-outliers` | `mu` | "Outliers removed" or "No outliers detected" (`removed_count`) |
| `removeDuplicateMus(app)` | `remove-duplicates` | — | "N duplicates removed" |
| `flagMuForDeletion(app)` | `flag` | `mu, flag` | — |
| `undoEdit(app)` | `undo` | — | Clear the selections |
| `resetCurrentMuEdits(app)` | `reset` | `mu` | Clear the selections |
| `duplicateMu(app)` | `duplicate` | `mu` | Switch to the new MU (`changed[0]`) |
| `saveEditedFile(app)` | — (`POST /edit/session/save`) | the session form's fields | `applyEditSave` |
| `loadDecompositionForEdit(app, file, path)` | — (`POST /edit/session/open`) | `path` | Offer recovery, `showEditSession` |
| `restoreEditSession(app)` | — (`GET /edit/session`) | the token `sessionStorage` kept | `showEditSession` |

---

## Modification Flow (Per Edit)

```
1. Canvas selection -> samples and pulse values (pulseBox; needs the fetched window)
2. requestRoiEdit(action, payload) / another action
   -> requestEditOp(app, op, args)
      -> api.editOp(op, {token, ...args})       POST /edit/ops/{op}
      -> applyEditChange(state, frame):
           keepEditMus(kept_indices)            [when MUs were removed or reordered]
           distimes/artifactTimes of the `changed` MUs
           flagged, muUids, muGridIndex, versions, hasPulse, dirty, canUndo
           editHistory cut at history_start, `history` appended
      -> refreshEditModeButtons()               [Undo follows canUndo]
3. setEditBookmark({muIdx, position}) + setShowBookmark(false)
4. Clear the selections + setEditMode(null)
5. renderEditExplorer()                         [the MU's new version refetches its window]
```

---

## Undo

- The server keeps an undo stack of up to 100 edits across all MUs. Each step restores the
  MU's discharge times, artifacts, flag and pulse train as they were, and cuts the history back
  to where it was.
- `state.edit.canUndo` mirrors the server's; `editUndoBtn` is disabled while it is false.
- `remove-duplicates` and a save clear the stack (the MUs were renumbered).
- There is **no redo**.

### Per-MU Reset (broader than undo)

- `op: reset` restores the current MU to the file's state (discharge times and pulse train),
  clears its artifacts and flag, and logs a `reset_mu` entry. It can itself be undone. Earlier
  entries are kept: after a reload they describe edits already in the file.

---

## Edit History Log

`state.edit.editHistory` mirrors the session's history, entry for entry. Each is tagged with:

| Field | Description |
|---|---|
| `type` | Entry type (see below) |
| `mu_uid` | Stable identifier of the MU |
| `timestamp` | When the edit was made (UTC, ISO 8601) |
| `spikes_added` / `spikes_removed` | The samples added / removed |
| `artifacts_added` / `artifacts_removed` | Likewise for artifacts |
| `view_start` / `view_end` | View window of a filter update |
| `use_peeloff` / `lock_spikes` | Filter flags of a filter update |
| `flagged` | Flag state |
| `source_mu_uid` | For duplicate operations |
| `removed_count` / `removed_mu_uids` | For dedup operations |

### Entry Types

`add_spikes`, `delete_spikes`, `delete_dr`, `add_artifact`, `delete_artifact`, `update_filter`, `remove_outliers`, `remove_duplicates`, `flag_mu`, `duplicate_mu`, `reset_mu`; the backend appends `remove_flagged` and `remove_duplicates` with `on_save: true` when saving drops MUs

History is **persisted with the saved file** (the `.json` edit log next to the `.npz`).

On **open**, `showEditSession` restores the view to the last edited MU and position
(`resumePosition`): the view of its last filter update, else around the samples it changed.

### Unsaved Edits

The server also logs every operation to disk as it is applied. When a file is opened and an
earlier session left unsaved edits to it (the app crashed or was closed before saving), the open
reports `recoverable_edits`. `loadDecompositionForEdit` asks "N unsaved edits to this file were
left from an earlier session. Restore them?" and posts `/edit/session/recover` with the answer.

A page reload (a WebView that crashed, not a restart of the app) keeps the session: its token is
in `sessionStorage`, and `restoreEditSession` reopens it at startup.

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
        1. api.editSessionSave(withBidsSaveFields({
             token, muscle, entity_label, file_label, software_versions
           }))                                   POST /edit/session/save  [120s timeout]
           Only the session form's fields travel; the server writes its own state
           (discharge times, pulse trains, flags, uids, history, artifacts).
        2. applyEditSave(state, saved):
           keepEditMus(saved.kept_indices)       [the server dropped flagged and duplicate MUs]
           the per-MU fields (dirty is false again)
           editHistory = saved.edit_history      [with the remove_flagged/remove_duplicates entries]
        3. refreshEditModeButtons()               [the undo stack starts over]
        4. renderEditExplorer()                   [only if the save dropped or reordered MUs]
        5. setEditStatus("Edited decomposition saved to {path}", "success")
        6. (on error: handleError(err, setEditStatus, "Save failed"))
```
