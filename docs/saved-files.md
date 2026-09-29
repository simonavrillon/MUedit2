# MUedit Saved Files

This document describes what MUedit writes to disk, where files are created, and the internal structure of NPZ outputs.

## Overview

MUedit can write files through three main flows:

1. Decomposition CLI/API default NPZ export
2. BIDS EMG export during decomposition
3. Edited decomposition save (web edit mode)

## 1) Default Decomposition NPZ

When decomposition runs with `save_npz=True`, MUedit writes:

```text
<input_dir>/<input_stem>_decomp.npz
```

Example:

```text
/data/session1/recording01_decomp.npz
```

Keys (the schema is described in [NPZ file format](#npz-file-format) below):
- the core keys every MUedit `.npz` holds: `schema_version`, `fsamp`, `total_samples`,
  `spike_times`, `spike_offsets`, `mu_grid_index`, `grid_names`, `muscle`, `parameters`
- `pulse_trains` — the motor units' pulse trains (IPTs)
- `sil` — silhouette score per MU
- `sil_keys`, `sil_by_window`, `sil_by_window_offsets` — per-window SIL scores
- `adaptive_losses` — adaptive decomposition losses
- `rois` — the `(start, end)` analysis windows

Conditional keys:
- `emg_data`, `discard_channels` + `discard_channel_offsets`, `coordinates` +
  `coordinate_offsets`, `loader_meta` (included only when BIDS export is not requested: the raw EMG then
  travels with the decomposition, so the editor can update MU filters)
- `artifact_intervals` (included whenever an artifact mask was applied, with or without BIDS export)

Notes:
- CLI decomposition uses `save_npz=True` by default.
- API decomposition persists this file when `persist_output=true` is set on the request or BIDS export is requested (then under `derivatives/muedit/.../decomp/`, see below).

## 2) BIDS Export During Decomposition

If `bids_root` is provided, MUedit writes raw EMG sidecars under the subject
tree and dataset-level files at the dataset root.

> **The exported signal is the raw, unfiltered EMG.** MUedit snapshots the
> recording as loaded, *before* the notch/bandpass filters used for
> decomposition are applied, so the BIDS `_emg.bdf|edf` always carries the raw
> signal (filtering is a software step recorded in metadata, not baked into the
> exported samples).

> **Recordings imported from BIDS are not re-encoded.** When the source was
> itself loaded from a BIDS dataset (a `.bdf`/`.edf` import), MUedit only writes
> files that are **missing** at the destination — the existing signal file and
> sidecars are left untouched rather than being re-encoded/overwritten. For
> non-BIDS sources (`.mat`, `.otb+`, `.otb4`) every file is (re)written. In both
> cases "missing" is judged against the export destination, so exporting a
> BIDS-imported recording into a brand-new `bids_root` still writes everything.

> **File format.** The signal is written as EDF+ (`_emg.edf`, 16-bit samples
> scaled to each channel's range). If the destination already holds an
> `_emg.bdf` or `_emg.edf` for the same recording, that file keeps its format,
> so a recording never gets two signal files.

> **Where `<bids_root>` is.** In the app you don't type a path — you name a
> project in the **Project** field of the Settings panel, and MUedit saves under
> `data/<project>/` inside the repository (so `<bids_root>` = `data/<project>`).
> An empty project falls back to `data/muedit_out/`. The base `data/` directory
> can be relocated with the `MUEDIT_DATA_ROOT` environment variable.

```text
<bids_root>/
  dataset_description.json                       # created if missing
  participants.tsv                               # row upserted per subject
  participants.json
  .bidsignore                                    # ignores derivatives decomp/
  sub-<subject>/
    [ses-<session>/]
      emg/
        <entity>_emg.edf                           # .bdf if the recording already has one
        <entity>_emg.json
        <entity>_channels.tsv
        <entity>_channels.json
        sub-<subject>[_ses-<session>]_electrodes.tsv              # one per session (all grids)
        sub-<subject>[_ses-<session>]_space-<grid>_coordsystem.json  # one per distinct grid space
```

> **Naming change (v2):** the channels sidecar is now `<entity>_channels.tsv`
> (no `_emg` infix) with a companion `<entity>_channels.json`. The loader still
> reads the legacy `<entity>_emg_channels.tsv` name for backward compatibility.

> **BIDS-EMG compliance (electrodes & coordinate systems):** electrodes and
> coordinate-system files are **session-scoped** — they carry only the
> `sub`/`ses` entities (no `task`/`acq`/`run`), because BIDS expects electrode
> definitions to be stable within a session. Per the EMG spec, the `space`
> entity is allowed **only** on `_coordsystem.json` (never on `_electrodes.tsv`,
> which also may not carry `space`). MUedit therefore writes:
> - one `_electrodes.tsv` per session listing **all** electrodes across grids,
>   whose `coordinate_system` column holds each electrode's grid label
>   (e.g. `HD08MM1305`);
> - one `_space-<grid>_coordsystem.json` per distinct grid, where `<grid>` is the
>   alphanumeric grid model name and matches the `coordinate_system` values.
>
> Electrode sidecar JSON fields (`ElectrodeManufacturer`,
> `ElectrodeManufacturersModelName`, `ElectrodeType`, `ElectrodeMaterial`) are
> written as a single string when all grids agree, and **omitted** when grids
> differ (the per-electrode `type`/`material` columns in `_electrodes.tsv` carry
> the variation, as the spec advises). Auxiliary channel names are made unique
> in `_channels.tsv` (e.g. `Quaternions`, `Quaternions_2`) since BIDS requires
> unique channel `name` values. These changes make the export pass the
> schema-based `bids-validator` with no errors.

Entity label pattern:

```text
sub-<subject>[_ses-<session>]_task-<task>[_acq-<acquisition>][_run-<run>][_recording-<recording>]
```

Also, when decomposition finds pulse trains, MUedit writes a combined BIDS
decomposition NPZ under a `muedit` derivatives pipeline (kept out of the BIDS
validator by `.bidsignore`):

```text
<bids_root>/derivatives/muedit/
  sub-<subject>/[ses-<session>/]decomp/<entity>_decomp.npz
```

> The derivatives-level `dataset_description.json` under
> `derivatives/muedit/` is **not** written by the decomposition pipeline — it
> is created only during the edited-save flow (see §3 below).

The BIDS decomposition NPZ has the same keys as above, without `emg_data`,
`discard_channels` and `coordinates`: the raw EMG lives in the BIDS `emg/` folder.

## 3) Edited Decomposition Save (Web Edit Mode)

`POST /api/v1/edit/session/save` writes the edited decomposition into the `muedit`
derivatives pipeline and refreshes the dataset-level participant files. The edits are
held by the server-side edit session the file was opened in (`/edit/session/open`), so
the request carries only the session token and the session form's fields. The run save,
`POST /api/v1/edit/save`, writes to the same place from a finished run.

Until they are saved, the session's edits are also logged to
`<cache>/edit-logs/*.jsonl`. When the app or the page closes with unsaved edits, opening
the same file again offers to restore them; the log is deleted when the file is saved.

Primary outputs:

```text
<bids_root>/derivatives/muedit/sub-<subject>/[ses-<session>/]decomp/<entity>_edited.npz
<bids_root>/derivatives/muedit/sub-<subject>/[ses-<session>/]decomp/<entity>_edited.json   # edit log (below)
```

Best-effort BIDS-facing side outputs (failures never block the primary save):

```text
<bids_root>/dataset_description.json                                   # created if missing
<bids_root>/participants.tsv | participants.json                      # subject row upserted from the form
<bids_root>/sub-<subject>/[ses-<session>/]emg/<entity>_*              # raw EMG sidecars re-exported (see §2)
<bids_root>/derivatives/muedit/sub-<subject>/[ses-<session>/]emg/
    <entity>_desc-decomposition_events.tsv                            # one row per MU spike (onset, duration, sample, unit_id, description)
    <entity>_desc-decomposition_events.json
```

The `events.tsv` derivative is the BIDS-facing representation of motor-unit
spike times; the authoritative artefact remains the `_edited.npz` above.

Edited NPZ keys:
- the core keys (see [NPZ file format](#npz-file-format))
- `pulse_trains` (only when the pulse trains were known: after a run, or when the edited
  file had them. A file edited from spike times alone stores spike times only, and the
  editor draws binary trains from them)

Conditional key:
- `artifact_intervals` (included only when artifact regions are provided)

### Edit Log Sidecar

Alongside the NPZ, MUedit writes a JSON sidecar with the same stem:

```text
<bids_root>/derivatives/muedit/sub-<subject>/[ses-<session>/]decomp/<entity>_edited.json
```

The sidecar contains:

```json
{
  "mu_uids": ["g0_mu0", "g0_mu1", "g1_mu0"],
  "history": [
    {
      "type": "add_spikes",
      "mu_uid": "g0_mu1",
      "timestamp": "2026-04-16T14:30:00.000Z",
      "spikes_added": [1024, 2048]
    },
    {
      "type": "delete_spikes",
      "mu_uid": "g0_mu0",
      "timestamp": "2026-04-16T14:31:05.123Z",
      "spikes_removed": [512]
    },
    {
      "type": "update_filter",
      "mu_uid": "g0_mu1",
      "timestamp": "2026-04-16T14:32:10.000Z",
      "view_start": 0,
      "view_end": 40000,
      "use_peeloff": false,
      "lock_spikes": false,
      "spikes_removed": [3000]
    },
    {
      "type": "remove_outliers",
      "mu_uid": "g1_mu0",
      "timestamp": "2026-04-16T14:33:00.000Z",
      "spikes_removed": [7200, 9100]
    },
    {
      "type": "duplicate_mu",
      "mu_uid": "g0_mu2",
      "source_mu_uid": "g0_mu1",
      "timestamp": "2026-04-16T14:34:00.000Z"
    },
    {
      "type": "remove_duplicates",
      "timestamp": "2026-04-16T14:35:00.000Z",
      "removed_count": 2,
      "removed_mu_uids": ["g0_mu2", "g1_mu3"]
    },
    {
      "type": "flag_mu",
      "mu_uid": "g0_mu0",
      "timestamp": "2026-04-16T14:36:00.000Z",
      "flagged": true
    }
  ],
  "artifact_times": [[7200, 9100], [], []]
}
```

**`mu_uids`** — one stable string ID per surviving MU (after flagged/duplicate removal), in the same order as the MUs in `spike_offsets`. Format: `g<grid_index>_mu<rank_within_grid>`. Assigned once at first load; preserved through successive saves. A uid is never reused: a new MU from `duplicate_mu` is numbered after every uid the history has ever named, including removed ones.

**`history`** — append-only log of all edit actions across all sessions. Carries over when the file is saved and reloaded for further editing.

**`artifact_times`** — present when any MU has artifact markers. A list of lists — one entry per MU — containing the sample indices of all peaks marked as artifacts. Restored automatically on reload.

Action types and their fields. Every entry also carries a `type` and an ISO-8601
`timestamp`; fields marked `?` are present only when non-empty.

| `type` | Fields | Notes |
|---|---|---|
| `add_spikes` | `mu_uid`, `spikes_added`?, `spikes_removed`? | Spikes added over a region of interest; `spikes_removed` captures any net removals. |
| `delete_spikes` | `mu_uid`, `spikes_removed`? | Spikes removed over a region of interest. |
| `delete_dr` | `mu_uid`, `spikes_added`?, `spikes_removed`? | Discharge-rate-based edit. |
| `add_artifact` | `mu_uid`, `artifacts_added`? | Artifact times added (separate channel from spikes). |
| `delete_artifact` | `mu_uid`, `artifacts_removed`? | Artifact times removed. |
| `update_filter` | `mu_uid`, `view_start`, `view_end`, `use_peeloff`, `lock_spikes`, `spikes_added`?, `spikes_removed`? | Filter re-estimation over `[view_start, view_end)`; `spikes_*` capture the net change. |
| `remove_outliers` | `mu_uid`, `spikes_removed`? | Automatic outlier-spike removal. |
| `duplicate_mu` | `mu_uid`, `source_mu_uid` | `mu_uid` is the **new** MU; `source_mu_uid` is the one it was copied from. |
| `remove_duplicates` | `removed_count`, `removed_mu_uids`, `on_save`? | Multi-MU action — no single `mu_uid`. `on_save: true` when the save itself removed them. |
| `flag_mu` | `mu_uid`, `flagged` | `flagged: true` marks the MU for deletion. |
| `remove_flagged` | `removed_count`, `removed_mu_uids`, `on_save` | Flagged MUs dropped when the file was saved. |
| `reset_mu` | `mu_uid`, `spikes_added`?, `spikes_removed`?, `artifacts_removed`?, `flagged`? | The MU was reset to its loaded state; the fields record what the reset changed. |

Undo removes the entries of the action it undoes, so the history always describes
the saved data.

All spike, artifact, and view coordinates are 0-based sample indices (same units
as `spike_times`).

## NPZ File Format

MUedit writes `.npz` files in **schema v2**. They are plain, uncompressed NumPy archives
that `np.load` reads with its default `allow_pickle=False`: nothing in them is pickled.
Each array's data starts on a 64-byte boundary, so large arrays (`pulse_trains`,
`emg_data`) can be memory-mapped straight from the file.

| Key | dtype, shape | Content |
|---|---|---|
| `schema_version` | int64, `()` | `2` |
| `fsamp` | float64, `()` | Sampling rate, Hz |
| `total_samples` | int64, `()` | Recording length in samples |
| `spike_times` | int32, `(n_spikes,)` | Discharge times of all MUs, concatenated; 0-based sample indices, sorted within each MU |
| `spike_offsets` | int64, `(n_mu + 1,)` | MU `i`'s discharge times are `spike_times[spike_offsets[i]:spike_offsets[i + 1]]` |
| `mu_grid_index` | int16, `(n_mu,)` | Grid of each MU |
| `grid_names`, `muscle` | unicode, `()` | JSON list of strings |
| `parameters` | unicode, `()` | JSON object: the decomposition parameters |
| `pulse_trains` | float32, `(n_mu, total_samples)` | Pulse trains (IPTs), one row per MU |
| `sil` | float64, `(n_mu,)` | Silhouette score per MU |
| `sil_keys`, `sil_by_window`, `sil_by_window_offsets` | int64, float64, int64 | Per-window SIL scores, laid out like the spike times |
| `adaptive_losses` | unicode, `()` | JSON |
| `rois` | int64, `(k, 2)` | `[start, end)` analysis windows |
| `artifact_intervals` | int64, `(k, 2)` | `[start, end)` sample ranges masked as artifacts |
| `emg_data` | float32, `(n_channels, total_samples)` | The **raw, unfiltered** EMG |
| `discard_channels`, `discard_channel_offsets` | uint8, int64 | Per grid, 1 = discarded channel |
| `coordinates`, `coordinate_offsets` | float32 `(n, 2)`, int64 | Per grid, electrode coordinates |
| `loader_meta` | unicode, `()` | JSON object: what the loader recorded about `emg_data` for its BIDS export (`units`, gains, hardware filters, device) |

Reading one in Python:

```python
import json
import numpy as np

with np.load("recording_decomp.npz") as f:
    spikes, offsets = f["spike_times"], f["spike_offsets"]
    discharge_times = [spikes[a:b] for a, b in zip(offsets[:-1], offsets[1:])]
    grid_names = json.loads(str(f["grid_names"]))
```

**Older files (schema v1).** Files saved by earlier MUedit versions have no
`schema_version` key. They store discharge times, names and parameters as pickled Python
objects (`discharge_times`, an object array), a boolean `artifact_mask`, and, in
`emg_data`, the EMG *after* the notch and bandpass filters. MUedit still opens them: it
rebuilds only arrays, lists, dicts, strings and numbers from the pickles and refuses
anything else, so a crafted file cannot run code. MUedit never rewrites them; saving an
edit writes a new v2 file. Outside MUedit, they need `np.load(path, allow_pickle=True)`,
which should only be used on files you trust.

## Important Path Rule For `bids_root`

**In the app**, the dataset root is chosen for you from the **Project** field in
the Settings panel: output goes to `data/<project>/` inside the repository
(`data/muedit_out/` when the field is empty). You only provide the project name.

```text
Project = "study1"   →   bids_root = data/study1
Project = ""         →   bids_root = data/muedit_out
```

The base `data/` directory can be relocated with the `MUEDIT_DATA_ROOT`
environment variable.

**When calling the API/CLI directly** with an explicit `bids_root`, pass the
dataset root, i.e. the folder that directly contains `sub-<subject>/` — not a
subfolder of it.

Correct:

```text
/.../data/study1
```

Incorrect:

```text
/.../data/study1/sub-01/emg
/.../data/study1/derivatives/muedit/sub-01/decomp
```

MUedit appends the rest of the path internally: raw EMG under
`sub-<subject>/[ses-<session>/]emg/` and decomposition outputs under
`derivatives/muedit/sub-<subject>/[ses-<session>/]decomp/`. When loading, it
infers `bids_root` from either layout (and from the legacy `sub-<subject>/decomp/`
location used by older saves).
