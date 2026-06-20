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
| BIDS EMG | `.bdf`, `.edf` | Requires a `*_channels.tsv` sidecar (legacy `*_emg_channels.tsv` also accepted) |
| Decomposition | `.npz` | Saved decomposition — goes straight to Edit |

### Loading a file

Click the folder icon in the landing page to browse.

**BIDS recordings:** point to either the `*_emg.bdf/.edf` file or the `emg/` directory. MUedit reads all grids and auxiliary channels defined in the `*_channels.tsv` sidecar automatically.

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
| Project | Project name — output is saved under `data/<project>/` (your BIDS dataset root). Leave empty to use `data/muedit_out/` |
| Subject | BIDS subject label — alphanumeric (e.g. `01`, `S06`, `pilot01`) |
| Session | BIDS session label — alphanumeric (e.g. `1`, `pre`, `post`) |
| Acquisition | BIDS `acq` label, for sequential recordings of the same task (e.g. one grid/finger recorded at a time). Optional, alphanumeric |
| Run | BIDS run index — a positive integer. Optional; leave empty to omit `run-` from the filename |
| Task | Task label (e.g. `trapezoid`) |
| Muscle | Muscle name(s) per grid |

The panel also exposes optional **participant** (age, sex, handedness) and
**acquisition/hardware** metadata (manufacturer, device model, powerline
frequency, placement scheme, task description). These are written to the BIDS
`participants.tsv` and `_emg.json` sidecars on save, and pre-filled from those
files (or from auto-detected loader metadata) when you reopen a recording.
Electrode details (type, material, inter-electrode distance) are derived
automatically from the grid model name and do not need to be entered.

> **Fill in any missing metadata before saving.** Not every recording format
> carries all of this information, so some fields may be blank when you open a
> file. To keep the BIDS export compliant, review the **Session Info**,
> participant, and acquisition/hardware fields and complete anything that is
> missing (subject, task, muscle, manufacturer, device model, powerline
> frequency, etc.) before you run a decomposition or save. Fields left empty are
> written as `n/a` placeholders, which remain valid BIDS but lose provenance.

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

Once decomposition finishes, the app automatically loads the result into **Edit mode** (Step 4) — you no longer need to open the saved `.npz` manually. From there you can browse motor units using the Grid and Motor Unit dropdowns, inspect each pulse train, and start correcting spikes right away.

---

## Step 4 — Edit

The Edit stage opens automatically after a decomposition (Step 3); you can also load a saved decomposition file (`.npz`) manually to resume work. It gives you tools to review and correct individual motor unit spike trains.

### Layout

- **Top chart** — firing rate over time (pps)
- **Bottom chart** — interactive pulse train

### Selecting a motor unit

Use the **Grid** and **Motor Unit** dropdowns, or the keyboard shortcuts `<` (previous) and `>` (next).

### Editing tools

| Button | Shortcut | Description |
|---|---|---|
| Add Spike | `A` | Activate add mode, then drag a box on the pulse train to add spikes within the selection |
| Add Artifact | `X` | Activate artifact mode, then drag a box to mark a peak as an artifact (see below) |
| Delete Spike | `D` | Activate delete mode, then drag a box (or click) to remove both spikes and artifact markers within the selection |
| Remove Outliers | `R` | Automatically remove spikes with abnormally high discharge rates |
| Update Filter | `Space` | Recompute the MU filter from the BIDS EMG signal over the current view window |
| Peel-off | `P` | Toggle peel-off for filter updates (see below) |
| Lock Spikes | `L` | Toggle spike-locking for filter updates — preserves your existing spikes when recomputing the filter (see below) |
| Flag MU | — | Mark the current MU for deletion — it will be excluded when saving |
| Duplicate MU | — | Create an identical copy of the current MU in the same grid (same pulse train and discharge times) — intended as a starting point for separating two merged units |
| Remove Duplicates | — | Run duplicate detection across all grids immediately: pairs whose lag-corrected spike overlap exceeds the **Duplicates thresh** setting are deduplicated, keeping the unit with the lowest inter-spike interval variability |
| Undo | — | Undo the last edit on the current MU |
| Reset | — | Revert all edits on the current MU to the original decomposition values |
| Save | — | Write the edited decomposition to disk |

**Peel-off during filter update:** when the Peel-off toggle is **On**, Update Filter subtracts the waveform contributions of all other motor units on the same grid from the whitened signal before recomputing the filter. This can improve separation in crowded windows where spike trains overlap, but may also overcorrect if the other units are not well estimated. It is off by default. Toggle it on or off as needed before pressing Update Filter or `Space`.

**Lock Spikes during filter update:** when the Lock Spikes toggle is **On**, Update Filter keeps the spikes you already have in the window instead of letting the recomputed filter replace them. Each existing spike is realigned to its nearest signal peak (within ±10 samples) and then merged with any newly detected spikes. Use this when you have already curated a window and want a filter refresh to *add* missed discharges without discarding your manual edits. With it **Off** (the default), the window's spikes are taken solely from the new filter. Toggle it before pressing Update Filter or `Space`; it is independent of Peel-off, so the two can be combined.

**Add Artifact workflow:** use this when a high-amplitude peak in the pulse train is clearly a noise artifact rather than a real discharge. Press `X` or click **Add Artifact** to enter artifact mode (button highlights), then drag a box around the peak. The peak is marked with an orange dot and recorded as an artifact for the current MU. Artifacts are **not** added to the spike train — they are excluded from it. The next time you press **Update Filter**, the signal around each artifact peak is subtracted from the whitened EMG (peel-off style, 25 ms window) before the filter is recomputed, preventing the artifact from corrupting the new filter. Artifact markers are preserved when you save and reload the file. To remove an artifact, use **Delete Spike** mode and drag a box over it — delete mode removes both spikes and artifact markers within the selection. **Undo** removes the most recently added artifact; **Reset** clears all artifacts for the current MU.

**Add / Delete workflow:** press the shortcut or click the button to enter the mode (button highlights), then drag a rectangular region on the pulse train canvas. The action applies to all spikes within the box. Press the shortcut again or click elsewhere to exit the mode.

**Double-click** on the pulse train canvas to zoom back out to the full signal.

**Navigation shortcuts** (same as Decompose stage):

| Key | Action |
|---|---|
| `←` / `→` | Scroll left / right |
| `↑` / `↓` | Zoom in / out |
| `<` / `>` | Previous / next MU |

**Edit bookmark:** after each edit, MUedit drops a bookmark at the region you just worked on. Zoom out (`↓`) to reveal the marker so you can quickly relocate and return to where you were editing.

### Saving

Click **Save**. The app writes the edited decomposition into the `muedit`
derivatives pipeline:

```
<bids_root>/derivatives/muedit/sub-<subject>/[ses-<session>/]decomp/<entity>_edited.npz
<bids_root>/derivatives/muedit/sub-<subject>/[ses-<session>/]decomp/<entity>_edited.json
<bids_root>/derivatives/muedit/sub-<subject>/[ses-<session>/]emg/<entity>_desc-decomposition_events.tsv
<bids_root>/derivatives/muedit/sub-<subject>/[ses-<session>/]emg/<entity>_desc-decomposition_events.json
```

The `.npz` contains the corrected pulse trains. The `.json` sidecar contains a full edit history (every add, delete, filter update, and flag action, with timestamps) that is preserved across successive save-reload-edit cycles.

Each save also refreshes the **BIDS events file** (`<entity>_desc-decomposition_events.tsv`, with a companion `.json`): a standards-compliant representation of the motor-unit discharges, with one row per spike (onset, sample index, and unit ID). It is regenerated from the current edits every time you save, so it always reflects the latest corrected spike trains. Saving additionally upserts the subject row in `participants.tsv`; see [saved-files.md](saved-files.md) for the full file list.

Flagged MUs are removed on save. Duplicate MUs are also removed on save: pairs whose lag-corrected spike overlap exceeds the **Duplicates thresh** setting (default 0.3) are considered duplicates, and only the one with the lowest inter-spike interval variability is kept. You can also trigger deduplication at any point during editing using the **Remove Duplicates** button.

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

`mu_uids` are stable identifiers (format `g<grid>_mu<rank>`) assigned at first load and preserved through edits. `history` is an append-only log — each reload of the file picks up where the previous session left off. `artifact_times` (when present) is a list of lists — one entry per MU — containing the sample indices of all peaks marked as artifacts; it is restored automatically on reload.

Additional history action types:

| `type` | Key fields | Description |
|---|---|---|
| `add_artifact` | `mu_uid`, `artifacts_added` | One or more peaks were marked as artifacts; `artifacts_added` lists the sample indices |
| `duplicate_mu` | `mu_uid`, `source_mu_uid` | A new MU was created as a copy of `source_mu_uid` |
| `remove_duplicates` | `removed_count`, `removed_mu_uids` | Deduplication was run; lists the UIDs that were removed |
| `flag_mu` | `mu_uid`, `flagged` | A MU was flagged (`true`) or unflagged (`false`) for deletion |

---

## Where files are saved

MUedit saves into a **per-project folder inside the repository's `data/`
directory**. You don't type a full path — you just name the project in the
**Project** field of the Settings panel (Session Info), and that becomes your
BIDS dataset root:

```
data/<project>/        ← BIDS dataset root (the folder that contains sub-<subject>/)
```

- Enter a project name (e.g. `study1`) → output is written under `data/study1/`.
- Leave it empty → MUedit uses `data/muedit_out/`.

From there MUedit builds the rest of the path internally: raw EMG under
`data/<project>/sub-<subject>/[ses-<session>/]emg/` and decomposition outputs
under `data/<project>/derivatives/muedit/sub-<subject>/[ses-<session>/]decomp/`.

> The base `data/` location can be relocated by setting the `MUEDIT_DATA_ROOT`
> environment variable before launching MUedit.

---

## Finalising dataset metadata before sharing

MUedit captures the metadata available at acquisition and editing time, but a
few dataset-level fields cannot be entered from the app — authors, license,
funding and ethics approvals, task descriptions and instructions, and
recording-level details such as skin preparation, institution, ground
electrode, and coordinate-system description.

**Before you share or publish a dataset**, complete these fields by running the
`notebooks/bids_dataset_metadata.ipynb` notebook once per dataset. Point it at
your project's BIDS root, fill in the values in each cell, and run it top to
bottom. The patches are idempotent — re-running only updates the fields you set
and leaves everything else untouched — and the final cell runs `bids-validator`
so you can confirm the dataset is fully compliant before distribution.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| File won't load | Check the extension is supported; for BIDS files confirm the `_channels.tsv` sidecar exists alongside the `.bdf/.edf` |
| Update Filter fails | Ensure the **Project** field is set and the original EMG file is accessible |
| Save fails | Check the **Project** field is set — output is written to `data/<project>/` |
| Port already in use | Set `MUEDIT_BACKEND_PORT` / `MUEDIT_FRONTEND_PORT` to free ports before launching |
| Browser does not open | Navigate manually to `http://localhost:8080` (or the port shown in the terminal) |
