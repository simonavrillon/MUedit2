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

### Edit Log Sidecar

Alongside the NPZ, MUedit writes a JSON sidecar with the same stem:

```text
<bids_root>/sub-<subject>/[ses-<session>/]decomp/<entity>_edited.json
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
      "spikes_added": [],
      "spikes_removed": [3000]
    },
    {
      "type": "remove_outliers",
      "mu_uid": "g1_mu0",
      "timestamp": "2026-04-16T14:33:00.000Z",
      "spikes_removed": [7200, 9100]
    },
    {
      "type": "flag_mu",
      "mu_uid": "g0_mu0",
      "timestamp": "2026-04-16T14:34:00.000Z",
      "flagged": true
    }
  ]
}
```

**`mu_uids`** — one stable string ID per surviving MU (after flagged/duplicate removal), in the same order as `discharge_times`. Format: `g<grid_index>_mu<rank_within_grid>`. Assigned once at first load; preserved through successive saves.

**`history`** — append-only log of all edit actions across all sessions. Carries over when the file is saved and reloaded for further editing.

Action types and their fields:

| `type` | Fields |
|---|---|
| `add_spikes` | `mu_uid`, `spikes_added` |
| `delete_spikes` | `mu_uid`, `spikes_removed` |
| `delete_dr` | `mu_uid`, `spikes_removed` |
| `update_filter` | `mu_uid`, `view_start`, `view_end`, `spikes_added`?, `spikes_removed`? |
| `remove_outliers` | `mu_uid`, `spikes_removed` |
| `flag_mu` | `mu_uid`, `flagged` |

All spike coordinates are 0-based sample indices (same units as `discharge_times`).

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
