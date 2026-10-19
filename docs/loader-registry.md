# Loader Registry Guide

This guide explains how to add a new raw-signal loader in MUedit.

## File layout

```
python/src/muedit/io/
  factory.py        ← extension→loader registry (entry point for all loading)
  _common.py        ← helpers shared by the loaders (grid sidecar, muscle names)
  _mat.py           ← MAT v5/v7.3 loader (helpers shared with decomp/decomposition_file.py)
  _otb.py           ← OTB+ and OTB4 loaders
  _intan.py         ← Intan RHD loader (all three save layouts)
  _openephys.py     ← Open Ephys binary-format loader (structure.oebin)
  _bids_reader.py   ← BIDS (EDF/BDF) loader + grid-read helpers
  bids.py           ← BIDS export + re-exports of read helpers
  npz.py            ← .npz archives MUedit writes and reads
```

## Current Pattern

Loader dispatch is registry-based in `python/src/muedit/io/factory.py`:

- `register_loader(ext, loader, *, overwrite=False)` — register/override an extension (`overwrite` is keyword-only)
- `get_loader(filepath)` — resolve the loader for a path (or a BIDS/Intan/Open Ephys recording directory)
- `supported_extensions()` — list registered extensions
- `load_signal(filepath, store=None)` — load a file and return a `SignalImport`; with a
  `store`, its EMG and auxiliary arrays are written into that session store (memory-mapped)

## Add A New Loader

1. Create `python/src/muedit/io/_yourformat.py` with the shape every loader shares:
   - `load_yourformat(filepath: str, store: ArrayStore | None = None) -> SignalImport`
   - first line: `store = store if store is not None else RamStore()`
   - resolve and validate the file first (a recording dataclass named for the module, e.g.
     `_IntanRecording`, and a `_resolve_<format>_recording(filepath)` helper), raising
     `SignalLoadError` with a specific message
   - allocate the full-length arrays with `store.allocate(...)`, fill them block by block
     (`muedit.storage.sample_blocks`), then `store.seal(...)` them, so a long recording never
     sits whole on the heap
   - end with `SignalImport.build(data=..., fsamp=..., grid_names=..., muscle_names=...,
     aux_data=..., aux_names=..., metadata=...)`, the metadata keys listed below
   - use the names the other loaders use for the same thing (`fsamp`, `n_samples`, `data`,
     `aux_data`, `aux_names`, `grid_names`, `muscle_names`, `metadata`)

2. Register the extension in the default registry in `python/src/muedit/io/factory.py`:
   ```python
   ".newfmt": load_yourformat,
   ```

3. Or register it at runtime instead:

```python
from muedit.io.factory import register_loader
from muedit.io._yourformat import load_yourformat

register_loader(".newfmt", load_yourformat)
```

4. If the format should appear in the file picker, update accepted extensions in:
   - `python/src/muedit/api/routes/dialog.py`

## Loader Output Contract

A `SignalImport` with:

- `data`
- `fsamp`
- `grid_names`
- `muscle_names`
- `aux_data`
- `aux_names`
- `metadata`: optional, but what a complete BIDS export is written from

## Metadata Contract

`metadata` is a free-form `dict`, but these keys are what current backend logic
uses during BIDS export and preview payload construction. The set of keys carried
from a loaded decomposition through to BIDS export is defined once as
`LOADER_BIDS_META_KEYS` in `python/src/muedit/decomp/decomposition_file.py` — keep new keys in
sync there.

The BIDS export reads these keys through `bids_emg_fields`, with any session-form field
filled in over the loader's value (`form_over_loader_meta`). A key no loader knows is written
as `"n/a"` (or the export default), never guessed.

Required:
- No metadata keys are strictly required for core decomposition to run.

Strongly recommended (per-channel filters and gains):
- `hardware_filters`: `str | list[str]`
  - Used for the BIDS JSON sidecar `HardwareFilters` field.
  - Use `"n/a"` or `["n/a"]` when unknown.
- `gains`: `float | list[float]`
  - EMG channel gains.
- `emg_hpf`: `float | list[float]`
  - EMG high-pass cutoff(s).
- `emg_lpf`: `float | list[float]`
  - EMG low-pass cutoff(s).
- `aux_gains` / `aux_hpf` / `aux_lpf`: `float | list[float]`
  - Auxiliary channel gains / cutoffs (if auxiliary channels exist).
- `aux_units`: `str`
  - Physical units of auxiliary channels (e.g. `"V"`). Defaults to `"a.u."` in BIDS export when not set.

Recording-level fields (round-tripped to/from the BIDS `_emg.json` sidecar):
- `manufacturer`: `str` — amplifier/system manufacturer.
- `device_name`: `str` — model name (maps to `ManufacturersModelName`).
- `powerline_freq`: `float | None` — mains frequency (e.g. `50` or `60`); `None` when the
  file does not record it (exported as `"n/a"`).
- `units`: `str` — physical units of `data`. The loaders scale EMG to millivolts and set `"mV"`
  (a `.mat` signal is taken to be in mV); the BIDS reader keeps the dataset's own units. The
  BIDS export writes `"mV"` when a loader leaves it unset.
- `recording_type`: `str` — BIDS `RecordingType` (default `"continuous"`).
- `software_filters`: `str | dict` — BIDS `SoftwareFilters` (default `"n/a"`).
- `software_versions`: `str` — acquisition software version string.
- `acquisition_date`: `str | None` — recording start, ISO 8601; written as the EDF start time.
- `electrode_impedances`: `list[float | None]` — per EMG channel, in kOhm; written to the
  `impedance` column of `electrodes.tsv` (`None` → `"n/a"`).

Source provenance (set by the BIDS loader only):
- `bids_emg_path`: `str` — absolute path of the source `_emg.bdf|edf`. Its
  presence marks the recording as a BIDS round-trip, which makes the EMG export
  write only **missing** files instead of re-encoding/overwriting the originals.
  Non-BIDS loaders must not set this.

Shape guidance:
- Prefer per-channel lists for `gains` / cutoff keys.
- EMG lists should align to EMG channel count in `data`.
- Auxiliary lists should align to auxiliary channel count in `auxiliary`.
- Scalars are accepted and treated as global values.
- If unknown, use explicit `"n/a"`-style placeholders instead of omitting fields.

> **Electrode metadata** (manufacturer, electrode type, material, IED, layout)
> is **not** taken from the loader's metadata — it is resolved from the grid model name via
> the `_GRID_CATALOG` in `python/src/muedit/signal/grid.py`. To support a new
> grid, add a `GridSpec` entry there (see
> [Adding A Grid To `_GRID_CATALOG`](#adding-a-grid-to-_grid_catalog)). Unknown
> grid names raise rather than silently defaulting.

## Adding A Grid To `_GRID_CATALOG`

Electrode geometry and BIDS electrode metadata are **not** provided by loaders.
They are resolved from the grid model name (the strings a loader puts in
`grid_names`) against `_GRID_CATALOG` in `python/src/muedit/signal/grid.py`. Adding
a grid is self-contained: append one `GridSpec` entry — no other file changes.

### `GridSpec` fields

Every field must be filled in:

| Field | Type | Meaning |
| --- | --- | --- |
| `channel_map` | `np.ndarray` (2-D) | Physical layout; each cell holds the **1-based** channel number at that row/column. `0` marks a position with **no electrode**. |
| `n_channels` | `int` | Total active electrode count (must match the count of non-zero entries in `channel_map`). |
| `ied` | `float` | Inter-electrode distance in **mm**. |
| `emg_type` | `int` | `1` = surface HD-EMG, `2` = intramuscular. |
| `manufacturer` | `str` | BIDS `ElectrodeManufacturer`. |
| `electrode_type` | `str` | BIDS `ElectrodeType` (e.g. `"surface array"`, `"intramuscular array"`). |
| `electrode_material` | `str` | BIDS `ElectrodeMaterial` (e.g. `"gold coated"`). |

### How the name is matched

`_find_spec` matches a catalog key as a **substring** of the runtime grid name,
so `"GR04MM1305"` resolves `"GR04MM1305_run1"`. Two consequences:

- **Order matters.** Entries are checked top-to-bottom; list longer / more
  specific keys **before** shorter ones so a generic key doesn't shadow a
  specific variant.
- Keep keys distinctive enough to avoid false-positive substring hits.

### `channel_map` conventions

- Values are **1-based** channel indices; only `0 < val <= n_channels` are
  placed. `0` means "no electrode here" (e.g. a corner left empty on a 13×5
  grid that carries 64 electrodes across 65 positions).
- The array shape defines the electrode grid (rows × columns); coordinates are
  derived from cell positions, so transcribe the manufacturer's map exactly.

### Example

```python
"GR04MM1305": GridSpec(
    channel_map=np.array([
        [ 0, 25, 26, 51, 52],
        [ 1, 24, 27, 50, 53],
        # ... remaining rows ...
        [12, 13, 38, 39, 64],
    ]),
    n_channels=64, ied=4.0, emg_type=1,
    manufacturer="OTBioelettronica",
    electrode_type="surface array",
    electrode_material="gold coated",
),
```

### Validation

- An unknown grid name raises `ValueError` in `format_hdemg_signal` during
  preprocessing — grids are never silently defaulted for geometry.
- `get_grid_electrode_metadata` is more lenient: it returns `"n/a"` placeholders
  for an unknown name so BIDS export can still proceed.

After adding an entry, confirm it resolves:

```python
from muedit.signal.grid import get_grid_electrode_metadata, format_hdemg_signal
get_grid_electrode_metadata("YOURGRID")          # non-"n/a" fields
format_hdemg_signal(["YOURGRID"])                # must not raise
```

## Error Handling

- Raise `muedit.errors.SignalLoadError` (a `ValueError`) for a file that is not a
  recording the loader reads: the wrong kind, malformed or truncated, or missing parts. Keep
  the specific message: the API answers it as a 400 whose message it is, a run's error event
  shows it, and the CLI prints it; any other exception is a 500 with a generic message.
- Keep format-specific parsing failures inside loader functions.
- Keep API/service layers format-agnostic.
