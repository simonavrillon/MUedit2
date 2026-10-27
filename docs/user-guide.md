# MUedit User Guide

MUedit decomposes high-density EMG recordings (surface or intramuscular arrays) into
individual motor unit pulse trains, and lets you review and correct the result
interactively. It runs as a small app in your browser. Nothing is uploaded anywhere,
the computation happens on your own machine, and your recordings and results stay in
the folders you choose.

This guide walks you through the app, stage by stage. Start with the quick start, then
come back for the detail on any control or setting. For the files MUedit writes, see
[saved files](saved-files.md); for batch work and scripting, see the
[command line](cli.md); for common problems, see [troubleshooting](troubleshooting.md).

---

## Quick start (5 minutes)

1. **Launch** MUedit — double-click **MUedit** (`MUedit.command` on macOS, `MUedit.bat`
   on Windows). It opens in your browser at <http://127.0.0.1:8000>.
2. **Open** your recording — on the landing page, click the folder icon and pick your
   file.
3. **Describe** it — open the settings panel (the icon rail on the left) and fill in
   **Project**, **Subject**, **Session**, **Task**, **Muscle**, and the participant and
   hardware fields. A decomposition will not start until these are filled in.
4. **Choose** what to decompose — drag across the **Average EMG activity** chart to
   select the window you care about. Optionally run **Automatic QC** to flag bad
   channels, and mark any noisy stretches as artifact windows with the **+** button.
5. **Decompose** — click **Decompose signal** and wait for the run to finish.
6. **Edit and save** — the result opens in the **Edit** stage. Step through the motor
   units, fix missed or extra discharges, and click **Save**.

---

## The interface at a glance

A session moves through four stages, shown as buttons across the top of the window:
**Import → QC / ROI → Decompose → Edit**. They unlock as you go.

- **Top navigation bar** — the four stage buttons. A sliding pill highlights the active
  one; completed steps get a check style. **Import** is always available, and **QC / ROI**
  once a recording is loaded. **Decompose** stays greyed out until a run has started, and
  **Edit** until a decomposition is loaded or a run has finished.
- **Status pill (top right)** — every message the app shows (connecting, errors, save
  confirmations, edit feedback) appears here. It is hidden when empty and truncates
  long messages with an ellipsis — **hover over it to read the full text**.
- **Settings rail (left)** — a narrow icon rail that expands into the settings panel.
  It holds three collapsible sections — **Session info**, **Decomposition settings**,
  **Quality filters** — and is shared across the QC, Decompose, and Edit stages. Click
  the rail's icon to open it; press `Escape` or click the backdrop to close it.
- **Landing page vs. workspace** — before you open a file you see the landing page
  ("Select your file to get started"). Clicking the **Import** button returns to it at
  any time without unloading anything; the Edit-stage keyboard shortcuts are inactive
  while it is shown.

---

## Step 1 — Import

### Supported file formats

| Format | Extension | Notes |
|---|---|---|
| MATLAB | `.mat` | v5 and v7.3 (HDF5). A MUedit decomposition `.mat` is also accepted here and opens straight into Edit. |
| OT Biolab+ | `.otb+` | Archive with XML + `.sig`. |
| OT Biolab 4 | `.otb4` | Archive with XML track descriptions + binary data. |
| Intan RHD | `.rhd` | Single-file or recording folder (all three save layouts). |
| Open Ephys | `.oebin` | Binary format: `structure.oebin`, or its recording folder. |
| BIDS EMG | `.bdf`, `.edf` | Requires a `*_channels.tsv` sidecar beside it (legacy `*_emg_channels.tsv` also accepted). |
| Decomposition | `.npz` | A saved MUedit decomposition — opens straight into Edit. |

### Opening a file

Click the folder icon on the landing page. MUedit opens a native file dialog (the
button is disabled until the dialog closes) and reads the file by its extension:

- A **raw recording** (`.mat`, `.otb+`, `.otb4`, `.bdf`, `.edf`, `.rhd`, `.oebin`) loads
  into the QC stage.
- A **decomposition** (`.npz`, or a MUedit `.mat`) opens straight into the Edit stage.
- A **`.mat`** is ambiguous: MUedit tries it as a raw recording first and, if that does
  not fit, as a saved decomposition.
- Any other extension shows the accepted list in an error box.

If the decomposition you are editing has unsaved edits when you open another file, you
are asked whether to open it anyway; the edits are offered back when you open that
decomposition again (see [Unsaved edits](#unsaved-edits)).

Format-specific notes:

- **Intan RHD** — point at the `.rhd` file or the recording folder, whichever your
  acquisition software produced; MUedit works out the layout and groups the amplifier
  channels into one grid per port. Grid identity comes from a `muedit_grids.json`
  sidecar beside the recording (a JSON object with `grid_names` and `muscle_names`),
  or defaults for 64-channel ports.
- **Open Ephys** — point at `structure.oebin` inside one recording's folder, or at the
  folder. Grid identity comes from the nearest `muedit_grids.json` sidecar (one beside
  `settings.xml` covers every recording) or defaults per headstage.
- **BIDS EMG** — point at the `*_emg.bdf/.edf` file or the `emg/` directory. MUedit reads
  every grid and auxiliary channel in the `*_channels.tsv` sidecar. Each grid's model
  must be in the grid catalogue (see [adding a grid](../dev/extending-loaders.md#add-a-grid))
  and named in the `grid_name` column or in `ElectrodeManufacturersModelName` of
  `*_emg.json`; MUedit's own exports already do this. A dataset from another tool may
  need both before it loads. Subject, session, task, acquisition, and run labels are
  parsed from the file name into the settings panel.

---

## Step 2 — QC / ROI

This stage is where you decide what is worth decomposing. You will see the electrode
grid as tiles, the average EMG activity over the whole recording, and any auxiliary
channels such as force or torque.

### EMG channels panel

Each electrode on the grid is a tile, laid out by its physical position. **Click a tile
to toggle it off** — discarded channels (shown in a warning colour) are left out of the
decomposition. Hover a tile to see its number and mean |EMG|. Use the **grid tabs**
above the panel to switch between grids. Channels already marked `bad` in a BIDS
`channels.tsv` sidecar start out discarded.

### Average EMG activity chart

A smoothed mean-|EMG| line per grid over the whole recording. **Drag across it to draw
your region of interest** — decomposition runs only on that window, which saves time and
keeps rest periods from diluting the result. With more than one analysis window, a drag
over a window you have already drawn adjusts it; otherwise it fills the first window not
drawn yet; once every window is drawn, a drag moves the window starting nearest to it.

**Artifact windows** — short bursts of noise inside your window corrupt the filter
estimation. Click the **+** button (top-right of the chart) to arm artifact selection,
then drag on the chart to mark a time region as an artifact; it is shaded on the chart.
The **+** button disarms after one window, so click it again for each one. The **−**
button removes the last window; the count next to the buttons shows how many are active.
Artifact windows are excluded from filter estimation and travel with the decomposition
request.

### Automatic QC

The **Automatic QC** button (sparkle icon, next to the grid tabs) runs the server-side
QC pipeline. It detects bad channels — **flat, saturated, noisy, low-SNR, intermittent,
and contact-loss** — and artifact-contaminated time regions automatically, and marks
what it finds.

> **Watch out:** Automatic QC **replaces** your current channel selections and artifact
> windows with what it detected — it does not merge with them. If you have already marked
> channels or windows by hand, either run Automatic QC first and then adjust, or skip it.
> You can click any tile to disagree with it and review the artifact regions before
> moving on. Nothing is committed until you decompose.

### Segment target

If you recorded a force/torque target, **Segment target** splits it into up and down
ramps, plateaus, sinusoids, and irregular blocks. **Click a segment** on either chart to
make it an analysis window; touching segments merge into one window. The menu next to the
auxiliary-channel picker (the **Segment types** button) lists each segment type found:
tick one or more types to choose every segment of them at once, untick to drop them; a
type is half-ticked when only some of its segments are chosen. Segmenting also fills the
**Task** field (`sinusoid`, `stairs`, `triangle`, `trapezoid`, `ramp up`, `ramp down`,
or `dynamic`) unless you typed a task yourself or the file named one.

If the recording's `*_events.tsv` already lists the target's phases (written by MUedit,
MUniverse, or the MUnitQuest tutorials), they are drawn as segments as soon as the file
loads, ready to click as windows, without **Segment target**.

### Auxiliary channels panel

Shows force, torque, or other auxiliary signals recorded alongside the EMG. Use the
dropdown to view **All channels** overlaid or a single channel. The chart carries the
same ROI, artifact, and segment overlays as the EMG chart.

### Settings panel (left rail)

Three collapsible sections. Open the rail with its icon on the left; click a section
header to expand it.

**Session info**

| Field | Description |
|---|---|
| File | Loaded filename (read-only) |
| Project | Pick a project from the output folder, or **+ New project…** and type its name — output is saved under `<output>/<project>/`. A folder name, not a path |
| Subject | BIDS subject label — alphanumeric (e.g. `1`, `S06`, `pilot01`) |
| Session | BIDS session label — alphanumeric (e.g. `1`, `pre`, `post`) |
| Acquisition | BIDS `acq` label, for sequential recordings of the same task. Optional, alphanumeric |
| Run | BIDS run index — a positive integer. Optional; leave empty to omit `run-` from the filename |
| Task | Task label (e.g. `trapezoid`); the field suggests common labels |
| **Participant** | Age (years), Sex, Handedness |
| **Placement** | Muscle (one field per grid), Scheme (`Other` / `Measured` / `Channel-specific`), Description (shown when Scheme is `Other`), Reference, Ground |
| **Hardware & signal** | Manufacturer, Device model, Fs (Hz) (read-only, from the file), Power line (Hz) (`Unknown` / `50` / `60`) |

These are written to the BIDS `participants.tsv` and `_emg.json` sidecars on save, and
pre-filled from those files (or from the loader's metadata) when you reopen a recording.
Electrode details (type, material, inter-electrode distance) are derived automatically
from the grid model name and do not need to be entered.

> **Fill in the session info before you decompose.** A decomposition will not start
> until **Project**, **Subject**, **Session**, **Task**, every **Muscle**, **Age**,
> **Sex**, **Handedness**, **Manufacturer**, and **Device model** are filled in, and —
> with the default placement scheme `Other` — the **Description**. Saving an edited
> decomposition asks for the description too. Pressing **Decompose signal** with any of
> them empty opens the panel on the first missing field's section, marks the empty fields
> red, and focuses the first one; each mark clears as you fill its field. Acquisition and
> Run stay optional. Not every recording format carries all of this, so some fields may
> be blank when you open a file — complete them before you run or save. Fields left empty
> are written as `n/a` placeholders, which remain valid BIDS but lose provenance.

**Decomposition settings**

| Field | Default | Description |
|---|---|---|
| Iterations | 150 | FastICA iterations |
| Analysis windows | 1 | Number of analysis windows |
| Duplicates thresh | 0.3 | Cross-correlation threshold for duplicate removal (0–1) |
| Peeloff | Off | Toggle peel-off; set Window in ms (shown when on, default 25 ms) |
| Post-processing | Windowed | How MU filters are applied after decomposition: **Windowed** (filters inside each analysis window only; pulse trains are zero outside the ROI), **Full trace** (filters applied batch by batch across the whole recording, without adaptation; units extend beyond the ROI), or **Adaptive** (online adaptive post-processing — **experimental**) |

The defaults are sensible starting points — leave them alone until you have a reason not
to.

**Quality filters** (applied after decomposition)

| Field | Default | Description |
|---|---|---|
| SIL filter | **On** | Remove MUs below the SIL threshold. This toggle is **always on** in the app (hover it: "This filter is always enabled"); only the CLI can turn it off. |
| SIL threshold | 0.9 | Silhouette threshold |
| COV tool | Off | Remove MUs whose spiking variability is above the threshold |
| COV threshold | 0.5 | Coefficient-of-variation threshold (shown when COV tool is on) |

---

## Step 3 — Decompose

Press **Decompose signal** (on the QC page) to start. The run page follows the search as
it happens:

- **Phase track** — **Load → Filter → Decompose → Post-process → Save**. The active
  phase's dot bounces; done phases fill; a failed phase turns red.
- **Search** — one row of dots per grid × window, one dot per iteration: **kept**,
  **rejected**, or **too few discharges**. A fourth, fainter state, **skipped**, marks
  iterations of a window whose basis ran out early.
- **Unit counter** — a big number labelled **"motor units found"** during the search and
  **"motor units kept"** once it finishes; with more than one grid, a per-grid count
  appears too.
- **Clock** — elapsed time ticking every second, then an estimate of the time left in
  the search once it has a pace, and "Took m:ss" when finished.
- **Cancel** stops a run in progress (you return to the QC page); **Run again** restarts
  with the current settings; **Retry save** re-runs the save if it failed.

When the run finishes, the counter shows the motor units post-processing kept, and the
status pill says where the decomposition was saved. The app then **saves the result and
preloads it into the Edit stage automatically** — the **Edit** step is ready the moment
you want it; there is no file to go and open.

> **Tip:** a finished run leaves **two** files: `<entity>_decomp.npz` (the pipeline's output)
> and `<entity>_edited.npz` (the auto-save, opened in the editor). Reopen the `_edited.npz` to
> continue where you left off. See [what a run writes](saved-files.md#what-a-run-writes).

> **Tip:** if the backend lost its cached copy of the signal (for example after a
> restart), the app reloads the file from its original path and retries once — the status
> pill shows "Session expired, reloading file...". This normally recovers by itself, unless
> the file has moved since you opened it.

---

## Step 4 — Edit

Decomposition is never perfect: it misses discharges, adds a few, and occasionally splits
one unit into two or merges two into one. The editor puts each motor unit in front of you
so you can fix that. It holds the result of a decomposition (Step 3), preloaded, ready to
open; you can also open a saved decomposition file (`.npz`, or a MUedit `.mat`) from the
landing page to resume work.

### Layout

- **Grid** and **Motor Unit** dropdowns at the top.
- **Edit toolbar** — the tools below.
- **Firing rate (pps)** — the unit's instantaneous discharge rate over time.
- **Pulse train** — the unit's pulse train over the current view, with discharge markers,
  artifact markers, your selection boxes, and the bookmark.
- **Timeline** — a whole-recording strip with the unit's discharges, your last edit's
  additions (green) and removals (red), and the visible window as a draggable rectangle.
  Drag it to pan; click it to centre the view.

### Selecting a motor unit

Use the **Grid** and **Motor Unit** dropdowns, or `<` (previous) and `>` (next). If the
current grid has no motor units, the dropdowns fall back to the first grid that has.

### Editing tools

Pick a tool, then **drag a box** over the part of the pulse train you want it to act on.
The tool acts once, on that box; pick it again for the next one. With **Delete
discharge/artifact**, a click also removes what is under it.

| Button | Shortcut | Description |
|---|---|---|
| Add discharge | `A` | Add the missed discharges inside the box |
| Add artifact | `X` | Mark a peak as an artifact so it is excluded rather than counted (see below) |
| Delete discharge/artifact | `D` | Remove both discharges and artifact markers inside the box (or click) |
| Remove outliers | `R` | Automatically remove discharges with implausibly high discharge rates |
| Update filter | `Space` | Recompute the unit's filter from the EMG over the window you are looking at — the single most useful thing to do to a marginal unit |
| Peel-off | `P` | Toggle peel-off for filter updates (see below) |
| Lock discharges | `L` | Toggle discharge-locking for filter updates (see below) |
| Flag MU | — | Mark the current MU for deletion — it is dropped when you save |
| Duplicate MU | — | Create an identical copy of the current MU in the same grid, as a starting point for separating two merged units |
| Remove duplicates | — | Run duplicate detection immediately, exactly as the decomposition does (within each grid, then across grids) |
| Undo | — | Take back the last edit, whichever MU it was on (up to 100 edits). **There is no redo** |
| Reset | — | Return the current MU to how the file had it (discharge times and pulse train), clearing its artifacts and flag. Reset can itself be undone |
| Save | — | Write the edited decomposition to disk |

A static hint line under the toolbar lists the shortcuts.

**Peel-off during filter update** (`P`) — when on, Update filter subtracts the other
motor units' waveform contributions (same grid) from the whitened signal before
recomputing the filter. It can improve separation in crowded windows, but may overcorrect
if the other units are not well estimated. Off by default.

**Lock discharges during filter update** (`L`) — when on, Update filter keeps the discharges you
already have in the window instead of replacing them: each existing discharge is realigned to
its nearest signal peak (within ±10 samples) and merged with any newly detected discharges.
Use it when you have already curated a window and want a filter refresh to *add* missed
discharges without discarding your edits. Off by default; independent of peel-off, so the
two can be combined.

**Add artifact workflow** (`X`) — for a high-amplitude peak that is clearly noise, not a
real discharge. Drag a box around it; the peak is marked with an orange dot and recorded as
an artifact. Artifacts are **not** added to the discharges — they are excluded from it.
The next **Update filter** subtracts the signal around each artifact peak (peel-off style,
25 ms) before recomputing, so the artifact cannot corrupt the new filter. Artifact markers
are preserved when you save and reload. Remove one with **Delete discharge/artifact** (delete mode
removes both discharges and artifact markers in the box); **Undo** takes back an artifact you
just added; **Reset** clears all artifacts for the current MU.

**Flag, duplicate, remove duplicates** — a flagged MU draws as a flat line at 0 and is
dropped on save. Duplicate MU copies a unit so you can edit the two copies apart.
**Remove duplicates** deduplicates within each grid and then across grids, at the
**Duplicates thresh** setting, keeping the unit with the lowest inter-discharge-interval
variability. An MU copied with **Duplicate MU** is removed on save unless you have edited
it enough to fall below the threshold.

> **Watch out:** **Save** and **Remove duplicates** both reset the undo history. And there
> is no redo — once you undo, the edit is gone unless you redo it by hand.

### Navigation shortcuts

These work only on the Edit stage, only when you are not typing in a field, and not while
the landing page is shown.

| Key | Action |
|---|---|
| `←` / `→` | Scroll left / right |
| `↑` / `↓` | Zoom in / out (`↓` also reveals the bookmark) |
| `<` / `>` | Previous / next MU (wraps) |
| `A` / `D` / `X` / `R` / `Space` / `P` / `L` | The matching tool above |
| Double-click the pulse train | Zoom back out to the full recording |
| `Escape` | Close the settings panel / segment-type menu |

> **Tip:** the bookmark is invisible until you zoom out (`↓`) or double-click — it appears
> as a green "You stopped here" line at the region you last worked on, so you can find your
> way back.

### Unsaved edits

MUedit journals every edit to disk as you make it. If the app closes or crashes before you
save, opening the same file again asks whether to restore the unsaved edits: **OK**
replays them, **Cancel** discards them. Edits to a file that has since changed on disk are
not offered. Reloading the page (not the app) keeps the file open with all its edits.

> **Tip:** closing or reloading the browser tab with unsaved edits triggers the browser's
> own "Leave site?" confirmation. That and the restore prompt on reopen are two different
> prompts for the same situation — the first guards the tab, the second recovers your work.

### Saving

Click **Save**. MUedit writes your corrected decomposition as a BIDS derivative under
your project folder, alongside a sidecar recording the full history of every edit you
made, with timestamps. That history survives save-and-reload cycles, so you can come back
to a file tomorrow and keep going, and anyone looking at the data later can see exactly
what was done by hand. Each save also writes the discharges as a standard BIDS events
table, one row per discharge, regenerated from your current edits. Flagged and duplicate MUs
are removed on save.

---

## Where files are saved

Output goes into the BIDS dataset the **Project** field names, inside MUedit's output folder:
`<output>/<project>/`. Pick a project, or **+ New project…** and type a folder name. A
decomposition opened from a dataset elsewhere is saved back into it while you leave its
**Project** unchanged. The decomposition to analyse is the `_edited.npz` under
`derivatives/muedit/`.

[Saved files](saved-files.md) has the path rules, every file a run and a save write, the NPZ
keys, and the edit-log format.

---

## Finalising dataset metadata before sharing

MUedit captures the metadata available at acquisition and editing time, but a few
dataset-level fields cannot be entered from the app — authors, licence, funding and ethics
approvals, task descriptions and instructions, and recording-level details such as skin
preparation, institution, and coordinate-system description.

**Before you share or publish a dataset**, complete these by running the
`notebooks/bids_dataset_metadata.ipynb` notebook once per dataset. Point it at your
project's BIDS root, fill in the values in each cell, and run it top to bottom. The
patches are idempotent — re-running only updates the fields you set — and the final cell
runs `bids-validator` so you can confirm the dataset is fully compliant before
distribution.

---

## Command line and scripting

MUedit also has a command line, for batch work and scripting: `muedit api` starts just
the backend, and `muedit decompose` runs a decomposition without the interface. See the
[command-line reference](cli.md) for every flag and the environment variables.

---

## Troubleshooting

See [troubleshooting](troubleshooting.md) for common symptoms and fixes, the log file
location, and where MUedit keeps temporary files.
