# Loader Registry Guide

This guide explains how to add a new raw-signal loader in MUedit.

## File layout

```
python/src/muedit/io/
  factory.py        ← extension→loader registry (entry point for all loading)
  loaders.py        ← thin re-exports only; do NOT add parsing logic here
  _mat.py           ← MAT v5/v7.3 loader
  _otb.py           ← OTB+ and OTB4 loaders
  _bids_reader.py   ← BIDS (EDF/BDF) loader + grid-read helpers
  bids.py           ← BIDS export + re-exports of read helpers
```

## Current Pattern

Loader dispatch is registry-based in `python/src/muedit/io/factory.py`:

- `register_loader(ext, loader, overwrite=False)` — register/override an extension
- `get_loader(filepath)` — resolve the loader for a path (or a BIDS `emg/` directory)
- `supported_extensions()` — list registered extensions
- `load_signal(filepath)` — load and normalize to the internal mapping shape

> The `LoaderFactory` class facade was removed in v2; call the module-level
> functions above directly.

## Add A New Loader

1. Create `python/src/muedit/io/_yourformat.py`.
   - Input: `filepath: str`
   - Return: `dict` compatible with `SignalImport.from_mapping(...)` or `SignalImport`

2. Re-export the loader from `python/src/muedit/io/loaders.py`:
   ```python
   from muedit.io._yourformat import load_yourformat
   ```

3. Register the extension in the default registry in `python/src/muedit/io/factory.py`:
   ```python
   ".newfmt": load_newfmt,
   ```

4. If needed, register dynamically instead:

```python
from muedit.io.factory import register_loader
from muedit.io._yourformat import load_yourformat

register_loader(".newfmt", load_yourformat)
```

5. If the format should appear in the file picker, update accepted extensions in:
   - `python/src/muedit/api/routes/dialog.py`

## Loader Output Contract

At minimum, ensure the loader output can be normalized to `SignalImport` fields:

- `data`
- `fsamp`
- `gridname`
- `muscle`
- `auxiliary`
- `auxiliaryname`
- `emgnotgrid`

`metadata` is optional but recommended when available for a complient BIDS export.

## Metadata Contract

`metadata` is a free-form `dict`, but these keys are what current backend logic
uses during BIDS export and preview payload construction. The set of keys carried
from a loaded decomposition through to BIDS export is defined once as
`LOADER_BIDS_META_KEYS` in `python/src/muedit/decomp/io.py` — keep new keys in
sync there.

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

Recording-level fields (round-tripped to/from the BIDS `_emg.json` sidecar):
- `manufacturer`: `str` — amplifier/system manufacturer.
- `device_name`: `str` — model name (maps to `ManufacturersModelName`).
- `powerline_freq`: `float` — mains frequency (e.g. `50` or `60`).
- `units`: `str` — physical units of `data` (default `"uV"`).
- `recording_type`: `str` — BIDS `RecordingType` (default `"continuous"`).
- `software_filters`: `str | dict` — BIDS `SoftwareFilters` (default `"n/a"`).
- `software_versions`: `str` — acquisition software version string.

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
> is **not** taken from the loader — it is resolved from the grid model name via
> the `_GRID_CATALOG` in `python/src/muedit/signal/grid.py`. To support a new
> grid, add a `GridSpec` entry there (see
> [Adding A Grid To `_GRID_CATALOG`](#adding-a-grid-to-_grid_catalog)). Unknown
> grid names raise rather than silently defaulting.

## Adding A Grid To `_GRID_CATALOG`

Electrode geometry and BIDS electrode metadata are **not** provided by loaders.
They are resolved from the grid model name (the strings a loader puts in
`gridname`) against `_GRID_CATALOG` in `python/src/muedit/signal/grid.py`. Adding
a grid is self-contained: append one `GridSpec` entry — no other file changes.

### `GridSpec` fields

Every field must be filled in:

| Field | Type | Meaning |
| --- | --- | --- |
| `channel_map` | `np.ndarray` (2-D) | Physical layout; each cell holds the **1-based** channel number at that row/column. `0` marks a position with **no electrode**. |
| `nbelectrodes` | `int` | Total active electrode count (must match the count of non-zero entries in `channel_map`). |
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

- Values are **1-based** channel indices; only `0 < val <= nbelectrodes` are
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
    nbelectrodes=64, ied=4.0, emg_type=1,
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

- Raise clear exceptions for malformed or unsupported files.
- Keep format-specific parsing failures inside loader functions.
- Keep API/service layers format-agnostic.
