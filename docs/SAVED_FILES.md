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

Core NPZ keys (aligned with web-app save format):
- `pulse_trains`
- `discharge_times`
- `fsamp`
- `grid_names`
- `mu_grid_index`
- `muscle_names`
- `muscle`
- `total_samples`
- `parameters`
- `adaptive_losses`

Conditional key:
- `emg_data` (included only when BIDS export is not requested)
- `discard_channels` (included only when BIDS export is not requested)
- `coordinates` (included only when BIDS export is not requested)

Notes:
- CLI decomposition uses `save_npz=True` by default.
- API decomposition persists this file only when `persist_output=true` is set on the request.

## 2) BIDS Export During Decomposition

If `bids_root` is provided, MUedit exports BIDS EMG files under:

```text
<bids_root>/
  sub-<subject>/
    [ses-<session>/]
      emg/
        <entity>_emg.bdf|edf
        <entity>_emg.json
        <entity>_emg_channels.tsv
        <entity>_emg_electrodes.tsv
        <entity>_emg_coordsystem.json
```

Entity label pattern:

```text
sub-<subject>[_ses-<session>]_task-<task>[_acq-<acquisition>][_run-<run>][_recording-<recording>]
```

Also, when decomposition finds pulse trains, MUedit writes a combined BIDS decomposition NPZ:

```text
<bids_root>/sub-<subject>/[ses-<session>/]decomp/<entity>_decomp.npz
```

BIDS decomposition NPZ keys (same core schema):
- `pulse_trains`
- `discharge_times`
- `fsamp`
- `grid_names`
- `mu_grid_index`
- `muscle_names`
- `muscle`
- `total_samples`
- `parameters`
- `adaptive_losses`

## 3) Edited Decomposition Save (Web Edit Mode)

`POST /api/v1/edit/save` always saves to the BIDS source tree.

Writes:

```text
<bids_root>/sub-<subject>/[ses-<session>/]decomp/<entity>_edited.npz
```

Edited NPZ keys:
- `pulse_trains`
- `discharge_times`
- `fsamp`
- `grid_names`
- `mu_grid_index`
- `muscle_names`
- `muscle`
- `parameters`
- `total_samples`

## Important Path Rule For `bids_root`

Use the dataset root, i.e. the folder that directly contains `sub-<subject>/`.

Correct:

```text
/.../muedit_out
```

Incorrect:

```text
/.../muedit_out/sub-01/emg
/.../muedit_out/sub-01/decomp
```

MUedit appends `sub-<subject>/...` internally when resolving BIDS files.
