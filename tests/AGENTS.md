# Test conventions

Rules for the pytest suite in `tests/`. The frontend suite is covered in `frontend/AGENTS.md`.
Tests are linted and type-checked like the package (`uv run ruff check .`, `uv run mypy`).

## Layout

- One file per source module: `test_<package>_<module>.py` (`test_editing_session.py`,
  `test_io_intan.py`, `test_api_editing.py`). Add to the existing file before creating one.
- Three suites cross modules and are named for what they check: `test_decomp_bids_export.py`
  (EMG-BIDS conformance, from a run to an edit save), `test_memory_regression.py` (the peak
  memory of each stage, against a budget) and `test_decomp_benchmark.py` (units and accuracy on
  the recordings under `data/`).
- Group tests for one function or behaviour in a `class TestThing:`; mark sections with
  `# ── name ───` dividers. Test names are sentences about behaviour:
  `test_mu_uids_are_numbered_per_grid_and_never_reused`.
- Single-line docstrings, as in the package.

## Shared helpers (reuse before writing new ones)

| Helper | For |
|---|---|
| `conftest.py` | `client` (`TestClient`), sample-file fixtures, a throwaway `MUEDIT_CACHE_DIR`; `short_emg`, `synthetic_mu`, `store_prep` and `decomposed` for the decomposition and edit tests |
| `_api.py` | `API` prefix, `SESSION_HEADER`, envelope readers (`error`, `frame`), stored runs |
| `_frames.py` | decode MUB1 frames as the page does |
| `_editing_data.py` | synthetic MUs, pulse trains, decomposition files |
| `_io_files.py` | tiny synthetic recordings in every import format |
| `_synthetic_emg.py` | synthetic HD-EMG for the QC and decomposition tests; `ACTIVE_ROI`, `STORE_PARAMS`; `bandpass_signals`, the whole-array bandpass `bandpass_inplace` matches |
| `_platform.py` | what a platform allows: `MAPPED_FILES_STAY`, `deleted`, `store_files` |
| `_metrics_helpers.py`, `_report.py` | spike-train agreement metrics and benchmark reports |

## Data

- Prefer synthetic data; CI has no recordings. CI runs `-m "not data"`.
- A test that needs a recording under `data/` takes one of the fixtures in
  `conftest.py: DATA_FIXTURES`, which gives it the `data` marker automatically. A test that
  fetches one with `request.getfixturevalue` must mark itself (`pytest.param(..., marks=pytest.mark.data)`).
  Add new sample fixtures to `DATA_FIXTURES`.
- Write files into `tmp_path`, never into the repo. The memory and benchmark suites are the
  exception: they leave their reports in `tests/reports/` (`MUEDIT_TEST_REPORT_DIR`) and
  `tests/benchmark_leaderboard.json` (`MUEDIT_BENCHMARK_LEADERBOARD`), both gitignored.

## What to test

- API behaviour through `TestClient` and the real routes (status, envelope, `detail.field`);
  domain logic by calling the module directly.
- Test the fix or feature itself. Don't add speculative guard tests or tests that restate the
  implementation.
- Seed randomness (`np.random.default_rng(0)`), and use `pytest.approx` / `np.testing` for floats.

## Run

```bash
uv run pytest -m "not data"          # the CI tier (~1.5 min)
uv run pytest tests/test_x.py -k y   # one area
uv run pytest                        # everything, needs recordings under data/
```

`MUEDIT_REQUIRE_DATA=1` turns a missing sample file into a failure instead of a skip.
