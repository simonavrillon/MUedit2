# MUedit User Guide

MUedit decomposes high-density surface EMG recordings into individual motor unit pulse trains, and lets you review and correct the results interactively.

---

## Workflow overview

```
Import signal  →  Quality Check / Region Of Interest  →  Decompose  →  Edit
```

Each step is represented by a button in the top navigation bar. Steps unlock progressively as data becomes available.

---

## Step 1 — Import

### Supported file formats

| Format | Extension | Notes |
|---|---|---|
| MATLAB | `.mat` | v5 and v7.3 (HDF5) |
| OT Biolab+ | `.otb+` | Archive with XML + `.sig` |
| OT Biolab 4 | `.otb4` | Proprietary binary |
| BIDS EMG | `.bdf`, `.edf` | Requires `*_emg_channels.tsv` sidecar |
| Decomposition | `.npz` | Saved decomposition — goes straight to Edit |

### Loading a file

Click the folder icon in the landing page to browse.

**BIDS recordings:** point to either the `*_emg.bdf/.edf` file or the `emg/` directory. MUedit reads all grids and auxiliary channels defined in the `*_emg_channels.tsv` sidecar automatically.

Once the file loads the app moves to the QC stage.

---

## Step 2 — QC / ROI

This stage lets you inspect signal quality and define which part of the recording to decompose.

### Grid Channel Quality panel

Each electrode on the grid is shown as a tile. Channels can be toggled to exclude them from decomposition.

### Average EMG Activity chart

Displays the rectified average across all active channels. Use this to identify the contraction window you want to decompose.

### Auxiliary Channels panel

Shows force, torque, or other auxiliary signals recorded alongside the EMG. Use the dropdown to select individual channels or view all overlaid.

**Defining an ROI:** click and drag on the Average EMG Activity chart or the Auxiliary Channels panel to draw a region of interest. The decomposition will run only on the selected time window.


### Settings panel (left sidebar)

Open the sidebar with the hamburger button (top-left). It contains three collapsible sections:

**Session Info**

| Field | Description |
|---|---|
| File | Loaded filename (read-only) |
| Fs (Hz) | Sampling frequency (read-only, from file) |
| BIDS root path | Root folder of your BIDS dataset (for saving) |
| Subject | BIDS subject ID (e.g. `01`) |
| Task | Task label (e.g. `trapezoid`) |
| Session | Session number |
| Run | Run number |
| Muscle | Muscle name(s) per grid |

**Decomposition Settings**

| Field | Description |
|---|---|
| Iterations | FastICA iterations (default 150) |
| Analysis windows | Number of analysis windows (default 1) |
| Duplicates thresh | Cross-correlation threshold for duplicate removal (0–1, default 0.3) |
| Peeloff | Toggle peeloff; set window in ms (default 25 ms) |
| Use adaptive | Toggle adaptive decomposition mode |

**Quality Filters** (applied after decomposition)

| Filter | Description |
|---|---|
| SIL filter | Remove MUs below the SIL threshold (default 0.9) |
| COV tool | Remove MUs whose spiking variability is above the coefficient-of-variation threshold |

---

## Step 3 — Decompose

Click **Decompose Signal** to start. Progress is shown in real time:

- **Pipeline Phase** — current processing step
- **Progress bar** — percentage complete

Once decomposition finishes the app moves to the Decompose results view. You can browse motor units using the Grid and Motor Unit dropdowns, and inspect each pulse train in the chart below.

**Navigation shortcuts** (click on the plots to make them work):

| Key | Action |
|---|---|
| `←` / `→` | Scroll left / right |
| `↑` / `↓` | Zoom in / out |

---

## Step 4 — Edit

Load a decomposition file (`.npz`). The Edit stage gives you tools to review and correct individual motor unit spike trains.

### Layout

- **Top chart** — firing rate over time (Hz)
- **Bottom chart** — interactive pulse train

### Selecting a motor unit

Use the **Grid** and **Motor Unit** dropdowns, or the keyboard shortcuts `<` (previous) and `>` (next).

### Editing tools

| Button | Shortcut | Description |
|---|---|---|
| Add Spike | `A` | Activate add mode, then drag a box on the pulse train to add spikes within the selection |
| Delete Spike | `D` | Activate delete mode, then drag a box (or click) to remove spikes |
| Remove Outliers | `R` | Automatically remove spikes with abnormally high discharge rates |
| Update Filter | `Space` | Recompute the MU filter from the BIDS EMG signal over the current view window |
| Peel-off | — | Toggle peel-off for filter updates (see below) |
| Flag MU | — | Mark the current MU for deletion — it will be excluded when saving |
| Undo | — | Undo the last edit on the current MU |
| Reset | — | Revert all edits on the current MU to the original decomposition values |
| Save | — | Write the edited decomposition to disk |

**Peel-off during filter update:** when the Peel-off toggle is **On**, Update Filter subtracts the waveform contributions of all other motor units on the same grid from the whitened signal before recomputing the filter. This can improve separation in crowded windows where spike trains overlap, but may also overcorrect if the other units are not well estimated. It is off by default. Toggle it on or off as needed before pressing Update Filter or `Space`.

**Add / Delete workflow:** press the shortcut or click the button to enter the mode (button highlights), then drag a rectangular region on the pulse train canvas. The action applies to all spikes within the box. Press the shortcut again or click elsewhere to exit the mode.

**Double-click** on the pulse train canvas to zoom back out to the full signal.

**Navigation shortcuts** (same as Decompose stage):

| Key | Action |
|---|---|
| `←` / `→` | Scroll left / right |
| `↑` / `↓` | Zoom in / out |
| `<` / `>` | Previous / next MU |

### Saving

Click **Save**. The app writes two files:

```
<bids_root>/sub-<subject>/[ses-<session>/]decomp/<entity>_edited.npz
<bids_root>/sub-<subject>/[ses-<session>/]decomp/<entity>_edited.json
```

The `.npz` contains the corrected pulse trains. The `.json` sidecar contains a full edit history (every add, delete, filter update, and flag action, with timestamps) that is preserved across successive save-reload-edit cycles.

Flagged MUs are removed on save. Duplicate MUs are also removed: pairs whose lag-corrected spike overlap exceeds the **Duplicates thresh** setting (default 0.3) are considered duplicates, and only the one with the lowest inter-spike interval variability is kept.

---

## Output files

### Decomposition NPZ (`.npz`)

Written after decomposition or after saving edits. Core arrays:

| Key | Description |
|---|---|
| `discharge_times` | Spike times as sample indices, one array per MU |
| `pulse_trains` | Continuous pulse train signal, one row per MU |
| `fsamp` | Sampling frequency (Hz) |
| `grid_names` | Grid labels |
| `mu_grid_index` | Grid assignment for each MU |
| `muscle_names` | Muscle label(s) |
| `total_samples` | Total recording length in samples |
| `parameters` | Decomposition parameters used |

### Edit log sidecar (`.json`)

```json
{
  "mu_uids": ["g0_mu0", "g0_mu1", "g1_mu0"],
  "history": [
    {
      "type": "add_spikes",
      "mu_uid": "g0_mu1",
      "timestamp": "2026-04-16T14:30:00.000Z",
      "spikes_added": [1024, 2048]
    }
  ]
}
```

`mu_uids` are stable identifiers (format `g<grid>_mu<rank>`) assigned at first load and preserved through edits. `history` is an append-only log — each reload of the file picks up where the previous session left off.

---

## BIDS root path

The BIDS root is the folder that directly contains `sub-<subject>/`. Set it in the Session Info panel before saving.

```
✓  /data/muedit_out                  ← correct
✗  /data/muedit_out/sub-01/decomp    ← too deep
```

MUedit appends `sub-<subject>/[ses-<session>/]decomp/` internally.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| File won't load | Check the extension is supported; for BIDS files confirm the `_emg_channels.tsv` sidecar exists alongside the `.bdf/.edf` |
| Update Filter fails | Ensure the BIDS root path is set and the original EMG file is accessible |
| Save fails | Check the BIDS root path points to the dataset root (not a subfolder) |
| Port already in use | Set `MUEDIT_BACKEND_PORT` / `MUEDIT_FRONTEND_PORT` to free ports before launching |
| Browser does not open | Navigate manually to `http://localhost:8080` (or the port shown in the terminal) |
