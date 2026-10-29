# MUedit Saved Files

Every file MUedit writes, where it goes, and what is inside. This is the page to read
before you analyse results in your own scripts.

## Where files go

**In the app**, you never type a path. The project picked under **Projects** in the settings panel names a
BIDS dataset inside MUedit's output folder:

```text
<output>/<project>/      ← BIDS dataset root (the folder that contains sub-<subject>/)
```

- `<output>` is, in order: the `MUEDIT_DATA_ROOT` environment variable; the folder picked with
  **Change output folder** under **Projects**; the repository's `data/` folder; `MUedit` in
  your Documents folder when MUedit is installed as a package.
- The project is a single folder name. A path such as `../study1` or `/data/x` is refused, so
  outputs always stay inside `<output>`. (An API call with no project writes to
  `<output>/muedit_out/`; the app always sends one.)
- A decomposition you open is saved back where it came from: into its project, or into
  **that** dataset when it lies elsewhere on disk. While it is open, the **Projects** tree and
  **Change output folder** are disabled; open a recording to use them again. A save back into the file's own dataset keeps its subject: an entity label
  naming another subject is refused, so a save never lands in another subject's folder.

Inside the dataset, raw EMG goes under `sub-<subject>/[ses-<session>/]emg/` and decompositions
under `derivatives/muedit/sub-<subject>/[ses-<session>/]decomp/`. File names start with the
entity label:

```text
sub-<subject>[_ses-<session>]_task-<task>[_acq-<acquisition>][_run-<run>][_recording-<recording>]
```

**On the command line**, `--bids-root` takes the dataset root itself (`/data/study1`), not a
folder inside it (`/data/study1/sub-01/emg`). Without `--bids-root`, see
[Command line without a dataset](#command-line-without-a-dataset).

## What a run writes

Every run from the app exports the recording into the project's dataset before decomposing,
then saves the decomposition in its derivatives. As soon as the run ends, the app also saves
it as an edited decomposition (next section), so a finished run leaves both
`<entity>_decomp.npz` and `<entity>_edited.npz`. Reopen `_edited.npz` to keep working.

```text
<bids_root>/
  dataset_description.json                       # created if missing
  participants.tsv, participants.json            # the subject's row added or updated
  sub-<subject>/[ses-<session>/]emg/
    <entity>_emg.edf                             # .bdf if the recording already has one
    <entity>_emg.json
    <entity>_channels.tsv, <entity>_channels.json
    <entity>_events.tsv, <entity>_events.json    # trigger pulses and target phases, when there are any
    sub-<subject>[_ses-<session>]_electrodes.tsv                 # one per session, all grids
    sub-<subject>[_ses-<session>]_space-<grid>_coordsystem.json  # one per grid model
  derivatives/muedit/
    dataset_description.json
    .bidsignore                                  # read only when validating derivatives/muedit/ itself
    sub-<subject>/[ses-<session>/]decomp/<entity>_decomp.npz    # the pipeline's output
```

> **The exported signal is the raw, unfiltered EMG**, as loaded, before the notch and bandpass
> filters used for decomposition. It is written as EDF+ (16-bit samples scaled to each
> channel's range); if the dataset already holds an `_emg.bdf` or `_emg.edf` for the recording,
> that file keeps its format. A failed export leaves no partial signal file.

> **Recordings opened from a BIDS dataset are not re-encoded.** MUedit writes only the files
> missing at the destination and leaves the existing signal and sidecars untouched. Recordings
> from other formats (`.mat`, `.otb+`, `.otb4`, `.rhd`, `.oebin`) are written in full.

> **The run checks the session fields first.** A placement scheme `Other` without a
> description, or an age that is not a whole number, stops the run before anything is computed.

**Bad channels.** `channels.tsv` marks a channel `bad`, with `status_description` "manually
excluded" for channels you discarded on the QC page (the app's **Automatic QC** flags count
as your selection), or "auto-detected" when the run itself ran QC (`--auto-mask-artifacts` on
the command line only). Filter updates in the editor read this status back, so they leave out
the same channels the decomposition did.

**Electrodes.** `_electrodes.tsv` lists every electrode across grids, its
`coordinate_system` column naming each electrode's grid model, which matches the `<grid>` of
one `_space-<grid>_coordsystem.json`. Electrode type, material and manufacturer come from the
grid model name. In `_emg.json` they are a single value when all grids agree and are left out
when grids differ (the per-electrode columns carry the difference). Auxiliary channel names are
made unique (`Quaternions`, `Quaternions_2`), as BIDS requires. The export passes the official
`bids-validator` with no errors.

**Events** (`<entity>_events.tsv`, sorted by onset):

- one `ttl` row per pulse of each trigger channel: `onset` at the rising edge, `duration` while
  high, `value` = line number, `channel` = its name in `_channels.tsv`. A line with more than
  50 rising edges per second is a clock and is left out (noted in the JSON);
- after **Segment target**, the target's phases in MUniverse's vocabulary:
  `muscle_on`/`muscle_off` around each contraction, `linear_isometric_ramp` (`direction`),
  `steady_isometric`, `sinusoid` (`frequency_hz`, `amplitude`, `n_cycles`, and one `cycle` row
  per trough) and `irregular`, with `level_start`/`level_end` in the target's units and, for a
  target in % MVC, `mvc_level` (ramps and plateaus) and `mvc_rate` (ramps).

The category is in `event_type`, as MUniverse writes it (MNE-BIDS reads `trial_type` and shows
these events as `n/a`). A `source` column says what wrote each row (`ttl`,
`target_segmentation`); the JSON records the MUedit version, the segmented channel and the
segmentation settings. A re-export replaces only MUedit's own rows and keeps the rest. When the
file already lists task phases from another tool (rows without `source`), MUedit adds no phase
rows. Opening the recording again draws the phases on the QC page.

## What a save writes

**Save** in the editor (and the automatic save after a run) writes:

```text
<bids_root>/derivatives/muedit/sub-<subject>/[ses-<session>/]decomp/
  <entity>_edited.npz          # the edited decomposition: the file to analyse
  <entity>_edited.json         # its edit log (see Edit log)
```

The two are written to temporary files and renamed into place together, so a save that fails
leaves the previous pair as it was. Flagged MUs and duplicates are removed (the edit log
records which).

Each save also refreshes, on a best-effort basis (a failure is reported on the page but never
blocks the save):

```text
<bids_root>/participants.tsv, participants.json             # only the fields you gave change
<bids_root>/sub-<subject>/[ses-<session>/]emg/<entity>_*    # raw EMG and sidecars (as for a run)
<bids_root>/derivatives/muedit/
  dataset_description.json     # GeneratedBy (MUedit version), link to the raw dataset
  descriptions.tsv             # what desc-decomposition means
  .bidsignore                  # ignores decomp/ when validating derivatives/muedit/ itself
  sub-<subject>/[ses-<session>/]emg/
    <entity>_desc-decomposition_events.tsv, .json
```

`desc-decomposition_events.tsv` has one row per motor unit discharge, sorted by onset, in the
MUniverse format: `onset`, `duration`, `sample`, `unit_id`, `event_type` = `motor-unit-spike`,
plus `group`, the grid the unit came from (as named in the raw `channels.tsv`, else
`Grid<n>`). It is regenerated from your current edits at every save; the `_edited.npz` remains
the authoritative file.

Until you save, every edit is also journaled in MUedit's cache folder (see
[troubleshooting](troubleshooting.md#temporary-files-and-cache)), so unsaved edits can be offered
back after a crash. After a save, it is reopening the `_edited.npz` that offers them.

## Command line without a dataset

`muedit decompose FILE` without `--bids-root` writes `<input_stem>_decomp.npz` beside the input
file. Because no dataset holds the raw EMG, that file embeds it (`emg_data`, with
`discard_channels`, `coordinates` and `loader_meta`), so the editor can still update filters.
A recording that already belongs to a BIDS dataset is saved into that dataset's derivatives
instead.

## Edit log

Next to every `_edited.npz`, the `.json` with the same stem holds:

```json
{
  "mu_uids": ["g0_mu0", "g0_mu1", "g1_mu0"],
  "history": [
    {"type": "add_spikes", "mu_uid": "g0_mu1", "timestamp": "2026-04-16T14:30:00.000Z",
     "spikes_added": [1024, 2048]},
    {"type": "update_filter", "mu_uid": "g0_mu1", "timestamp": "2026-04-16T14:32:10.000Z",
     "view_start": 0, "view_end": 40000, "use_peeloff": false, "lock_spikes": false,
     "spikes_removed": [3000]},
    {"type": "remove_duplicates", "timestamp": "2026-04-16T14:35:00.000Z",
     "removed_count": 2, "removed_mu_uids": ["g0_mu2", "g1_mu3"]}
  ],
  "artifact_times": [[7200, 9100], [], []]
}
```

- **`mu_uids`** — one stable ID per saved MU, in the order of `spike_offsets`:
  `g<grid>_mu<n>`. Assigned when a file is first loaded and kept through every save. A uid is
  never reused: a copy made with **Duplicate MU** is numbered after every uid the history has
  named, removed ones included.
- **`history`** — every edit across all sessions, carried over from save to save. Undo removes
  the entries of what it undoes, so the history always describes the saved data.
- **`artifact_times`** — present only when an MU has artifact markers: one list per MU of the
  samples marked as artifacts.

Every entry has a `type` and an ISO-8601 `timestamp`; fields marked `?` appear only when
non-empty. All sample positions are 0-based, like `spike_times`.

| `type` | Fields | Meaning |
|---|---|---|
| `add_spikes` | `mu_uid`, `spikes_added`?, `spikes_removed`? | Spikes added in a box |
| `delete_spikes` | `mu_uid`, `spikes_removed`? | Spikes removed in a box |
| `add_artifact` | `mu_uid`, `artifacts_added`? | Artifact markers added |
| `delete_artifact` | `mu_uid`, `artifacts_removed`? | Artifact markers removed |
| `update_filter` | `mu_uid`, `view_start`, `view_end`, `use_peeloff`, `lock_spikes`, `spikes_added`?, `spikes_removed`? | Filter refit over `[view_start, view_end)`; `spikes_*` are the net change |
| `remove_outliers` | `mu_uid`, `spikes_removed`? | Discharge-rate outliers removed |
| `duplicate_mu` | `mu_uid`, `source_mu_uid` | `mu_uid` is the new copy of `source_mu_uid` |
| `remove_duplicates` | `removed_count`, `removed_mu_uids`, `on_save`? | Duplicates removed; `on_save: true` when the save removed them |
| `flag_mu` | `mu_uid`, `flagged` | `flagged: true` marks the MU for deletion |
| `remove_flagged` | `removed_count`, `removed_mu_uids`, `on_save` | Flagged MUs dropped by the save |
| `reset_mu` | `mu_uid`, `spikes_added`?, `spikes_removed`?, `artifacts_removed`?, `flagged`? | MU reset to the file's state; the fields record what changed |

## NPZ file format

MUedit writes `.npz` files in **schema v2**: plain, uncompressed NumPy archives that `np.load`
reads with its default `allow_pickle=False`. Each array starts on a 64-byte boundary, so large
arrays (`pulse_trains`, `emg_data`) can be memory-mapped straight from the file.

| Key | dtype, shape | Content | In |
|---|---|---|---|
| `schema_version` | int64, `()` | `2` | all |
| `muedit_version` | unicode, `()` | The MUedit version that wrote the file, e.g. `2.2.0` | 2.2.0 and later |
| `fsamp` | float64, `()` | Sampling rate, Hz | all |
| `total_samples` | int64, `()` | Recording length in samples | all |
| `spike_times` | int32, `(n_spikes,)` | Discharge times of all MUs, concatenated; 0-based sample indices, sorted within each MU | all |
| `spike_offsets` | int64, `(n_mu + 1,)` | MU `i`'s discharge times are `spike_times[spike_offsets[i]:spike_offsets[i + 1]]` | all |
| `mu_grid_index` | int16, `(n_mu,)` | Grid of each MU, from 0 | all |
| `grid_names`, `muscle_names` | unicode, `()` | JSON list of strings | all |
| `parameters` | unicode, `()` | JSON object: the decomposition parameters | all |
| `pulse_trains` | float32, `(n_mu, total_samples)` | Pulse trains (IPTs), one row per MU | when known¹ |
| `sil` | float64, `(n_mu,)` | Silhouette score per MU | when every MU has one² |
| `sil_keys`, `sil_by_window`, `sil_by_window_offsets` | int64, float64, int64 | Per-window SIL scores, laid out like the spike times | `_decomp` |
| `rois` | int64, `(k, 2)` | `[start, end)` analysis windows | when set |
| `artifact_intervals` | int64, `(k, 2)` | `[start, end)` sample ranges masked as artifacts | when any |
| `target_segments` | unicode, `()` | JSON object: the segmented target (`target_index`, `target_name`, `target_units`, `config`, `segments`) | when segmented |
| `loader_meta` | unicode, `()` | JSON object: what the loader recorded for the BIDS export (units, gains, filters, device, placement), with the form's fields over it in an edited save | `_edited`, and CLI files outside a dataset |
| `emg_data` | float32, `(n_channels, total_samples)` | The **raw, unfiltered** EMG | CLI files outside a dataset |
| `discard_channels`, `discard_channel_offsets` | uint8, int64 | Per grid, 1 = discarded channel | CLI files outside a dataset |
| `coordinates`, `coordinate_offsets` | float32 `(n, 2)`, int64 | Per grid, electrode coordinates | CLI files outside a dataset |

¹ An edited file opened from discharge times alone (no pulse trains) is saved without them;
the editor draws binary trains from the discharge times.
² A copy made with **Duplicate MU** has no score.

When an edited file is opened, the recording's raw `channels.tsv` overrides its grid names,
muscles and sampling rate. A file whose dataset EMG is missing (the save reported a BIDS EMG
error) opens, but **Update filter** has no EMG to read.

Reading one in Python:

```python
import json
import numpy as np

with np.load("sub-01_task-ramp_edited.npz") as f:
    spikes, offsets = f["spike_times"], f["spike_offsets"]
    spike_times = [spikes[a:b] for a, b in zip(offsets[:-1], offsets[1:])]
    grid_names = json.loads(str(f["grid_names"]))
```

**Older files (schema v1)** have no `schema_version` key. They store discharge times, names and
parameters as pickled Python objects (`discharge_times`, an object array), a boolean
`artifact_mask`, and, in `emg_data`, the EMG *after* the notch and bandpass filters. MUedit
still opens them, rebuilding only arrays, lists, dicts, strings and numbers from the pickles,
so a crafted file cannot run code. It never rewrites them: saving writes a new v2 file. Outside
MUedit they need `np.load(path, allow_pickle=True)`, to be used only on files you trust. Older
datasets may also name the channels sidecar `<entity>_emg_channels.tsv`, or keep
decompositions in `sub-<subject>/decomp/`; MUedit reads both.
