# Loader Registry Guide

This guide explains how to add a new raw-signal loader in MUedit.

## Current Pattern

Loader dispatch is registry-based in `python/src/muedit/io/factory.py`:

- `register_loader(ext, loader, overwrite=False)`
- `get_loader(filepath)`
- `supported_extensions()`
- `LoaderFactory.load_signal(filepath)` (compatibility facade)

## Add A New Loader

1. Add a parser in `python/src/muedit/io/loaders.py`.
   - Input: `filepath: str`
   - Return: `dict` compatible with `SignalImport.from_mapping(...)` or `SignalImport`

2. Register the extension in `python/src/muedit/io/factory.py` default registry.
   - Example: `".newfmt": load_newfmt`

3. If needed, register dynamically:

```python
from muedit.io.factory import register_loader
from muedit.io.loaders import load_newfmt

register_loader(".newfmt", load_newfmt)
```

4. If the format should appear in the file picker, update accepted extensions in:
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

`metadata` and `device_name` are optional but recommended when available.

## Metadata Contract

`metadata` is a free-form `dict`, but these keys are what current backend logic
uses during BIDS export and preview payload construction.

Required:
- No metadata keys are strictly required for core decomposition to run.

Strongly recommended:
- `hardware_filters`: `str | list[str]`
  - Used for BIDS JSON sidecar `HardwareFilters` description.
  - Use `"n/a"` or `["n/a"]` when unknown.
- `gains`: `float | list[float]`
  - EMG channel gains.
- `emg_hpf`: `float | list[float]`
  - EMG high-pass cutoff(s).
- `emg_lpf`: `float | list[float]`
  - EMG low-pass cutoff(s).
- `aux_gains`: `float | list[float]`
  - Auxiliary channel gains (if auxiliary channels exist).
- `aux_hpf`: `float | list[float]`
  - Auxiliary high-pass cutoff(s).
- `aux_lpf`: `float | list[float]`
  - Auxiliary low-pass cutoff(s).

Additional top-level key (outside `metadata`):
- `device_name: str | None`
  - Propagated to derived BIDS metadata as recording device info.

Shape guidance:
- Prefer per-channel lists for `gains` / cutoff keys.
- EMG lists should align to EMG channel count in `data`.
- Auxiliary lists should align to auxiliary channel count in `auxiliary`.
- Scalars are accepted and treated as global values.
- If unknown, use explicit `"n/a"`-style placeholders instead of omitting fields.

## Error Handling

- Raise clear exceptions for malformed or unsupported files.
- Keep format-specific parsing failures inside loader functions.
- Keep API/service layers format-agnostic.
