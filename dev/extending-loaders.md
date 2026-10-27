# Extending MUedit: Loaders and Grids

How to add a raw-signal **loader** or an electrode **grid**. Follow an existing loader
(`io/_openephys.py` is a compact one) rather than this page for the details; this page lists
the rules they all follow and every file a new format touches. Paths are relative to
`python/src/muedit/` unless they start with `frontend/`, `tests/` or `docs/`.

## Add a loader

1. **Module** `io/_<format>.py`, shaped like the others:
   - `load_<format>(file_path: str, store: ArrayStore | None = None) -> SignalImport`, starting
     with `store = store if store is not None else RamStore()`.
   - Resolve and validate first: a `_<Format>Recording` dataclass built by
     `_resolve_<format>_recording(file_path)`, raising `SignalLoadError` with a specific
     message (the user reads it as-is).
   - Allocate full-length arrays with `store.allocate(...)`, fill them block by block
     (`storage.sample_blocks`), then `store.seal(...)`. A long recording never sits whole on the heap.
   - Scale EMG to millivolts and set `units: "mV"`.
   - End with `SignalImport.build(data=..., fsamp=..., grid_names=..., muscle_names=...,
     aux_data=..., aux_names=..., metadata=...)`.
2. **Register** the extension in `io/factory.py: _LOADERS`. A format saved as a folder also needs
   a branch in `get_loader`'s directory dispatch. The file dialog follows the registry.
3. **Frontend**: add the extension to `KIND_BY_EXTENSION` and the accepted-formats message in
   `frontend/src/app/stages/import-stage.js` (and to `FOLDER_NAMED_FILES` if the format's file
   has a fixed name, like `structure.oebin`).
4. **Tests**: a tiny synthetic recording writer in `tests/_io_files.py` and
   `tests/test_io_<format>.py`. Tests on real recordings take a fixture listed in
   `tests/conftest.py: DATA_FIXTURES`.
5. **Docs**: the format in the supported-formats list of `docs/user-guide.md`.

Check: `uv run pytest tests/test_io_<format>.py tests/test_io_factory.py`, then open a recording
in the app.

## Metadata

`metadata` is what the BIDS export is written from. Every loader fills the same keys, in the
same order (`acquisition_date`, `manufacturer`, `device_name`, `software_versions`,
`hardware_filters`, `powerline_freq`, `units`, `gains`, `emg_hpf`, `emg_lpf`, `aux_units`,
`aux_gains`, `aux_hpf`, `aux_lpf`, `aux_kinds`, `recording_type`, `software_filters`), with
`"n/a"` or `None` for anything the file doesn't record. Never guess a value.

- The keys that survive into a saved decomposition and its BIDS export are listed once, in
  `decomp/decomposition_file.py: LOADER_BIDS_META_KEYS`. Add a new key there too.
- Per-channel values (`gains`, cutoffs) are lists aligned to the EMG rows (or the aux rows for
  `aux_*`); a scalar applies to every channel.
- `aux_kinds` comes from `io/aux_channels.py: aux_kinds`, which also finds the force target.
- `electrode_impedances` (kOhm per EMG channel) goes to `electrodes.tsv`.
- `bids_emg_path` is set by the BIDS reader only. It marks a round trip, which makes the export
  write only missing files. Other loaders must not set it.
- Electrode geometry is not metadata: it comes from the grid name (below).

## Add a grid

Geometry and BIDS electrode metadata come from the grid model name a loader puts in
`grid_names`, matched against `_GRID_CATALOG` in `signal/grid.py`. Adding a grid is one
`GridSpec` entry; no other file changes.

- `channel_map` is the physical layout as the manufacturer draws it: each cell holds the
  **1-based** channel number, `0` where there is no electrode. Coordinates come from cell
  positions.
- `n_channels` must equal the number of non-zero cells. `ied` is in mm. `emg_type` 1 = surface
  (20–500 Hz bandpass), 2 = intramuscular (100–4400 Hz).
- A key matches as a **substring** of the grid name, checked top to bottom: put specific keys
  before shorter ones they contain, and keep keys distinctive.
- An unknown name stops preprocessing with a `MueditError` (`grid_spec`); BIDS electrode metadata
  falls back to `"n/a"` (`get_grid_electrode_metadata`).

Check that it resolves:

```python
from muedit.signal.grid import format_hdemg_signal, get_grid_electrode_metadata
format_hdemg_signal(["YOURGRID"])           # must not raise
get_grid_electrode_metadata("YOURGRID")     # no "n/a" fields
```

Loaders without a grid name in the file (Intan, Open Ephys) read one from a
`muedit_grids.json` sidecar (`io/_common.py`), else use a per-channel-count default.
