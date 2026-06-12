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

If `bids_root` is provided, MUedit writes raw EMG sidecars under the subject
tree and dataset-level files at the dataset root.

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
        <entity>_emg.bdf|edf
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
  dataset_description.json                        # created if missing
  sub-<subject>/[ses-<session>/]decomp/<entity>_decomp.npz
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

`POST /api/v1/edit/save` writes the edited decomposition into the `muedit`
derivatives pipeline and refreshes the dataset-level participant files.

Primary outputs:

```text
<bids_root>/derivatives/muedit/sub-<subject>/[ses-<session>/]decomp/<entity>_edited.npz
<bids_root>/derivatives/muedit/sub-<subject>/[ses-<session>/]decomp/<entity>_edited.json   # edit log (below)
```

Best-effort BIDS-facing side outputs (failures never block the primary save):

```text
<bids_root>/dataset_description.json                                   # created/updated
<bids_root>/participants.tsv | participants.json                      # subject row upserted from the form
<bids_root>/sub-<subject>/[ses-<session>/]emg/<entity>_*              # raw EMG sidecars re-exported (see §2)
<bids_root>/derivatives/muedit/sub-<subject>/[ses-<session>/]emg/
    <entity>_desc-decomposition_events.tsv                            # one row per MU spike (onset/sample/unit_id)
    <entity>_desc-decomposition_events.json
```

The `events.tsv` derivative is the BIDS-facing representation of motor-unit
spike times; the authoritative artefact remains the `_edited.npz` above.

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
  ]
}
```

**`mu_uids`** — one stable string ID per surviving MU (after flagged/duplicate removal), in the same order as `discharge_times`. Format: `g<grid_index>_mu<rank_within_grid>`. Assigned once at first load; preserved through successive saves.

**`history`** — append-only log of all edit actions across all sessions. Carries over when the file is saved and reloaded for further editing.

Action types and their fields. Every entry also carries a `type` and an ISO-8601
`timestamp`; fields marked `?` are present only when non-empty.

| `type` | Fields | Notes |
|---|---|---|
| `add_spikes` | `mu_uid`, `spikes_added`? | Spikes added over a region of interest. |
| `delete_spikes` | `mu_uid`, `spikes_removed`? | Spikes removed over a region of interest. |
| `delete_dr` | `mu_uid`, `spikes_added`?, `spikes_removed`? | Discharge-rate-based edit. |
| `add_artifact` | `mu_uid`, `artifacts_added`? | Artifact times added (separate channel from spikes). |
| `delete_artifact` | `mu_uid`, `artifacts_removed`? | Artifact times removed. |
| `update_filter` | `mu_uid`, `view_start`, `view_end`, `use_peeloff`, `lock_spikes`, `spikes_added`?, `spikes_removed`? | Filter re-estimation over `[view_start, view_end)`; `spikes_*` capture the net change. |
| `remove_outliers` | `mu_uid`, `spikes_removed`? | Automatic outlier-spike removal. |
| `duplicate_mu` | `mu_uid`, `source_mu_uid` | `mu_uid` is the **new** MU; `source_mu_uid` is the one it was copied from. |
| `remove_duplicates` | `removed_count`, `removed_mu_uids` | Multi-MU action — no single `mu_uid`. |
| `flag_mu` | `mu_uid`, `flagged` | `flagged: true` marks the MU for deletion. |

All spike, artifact, and view coordinates are 0-based sample indices (same units
as `discharge_times`).

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
