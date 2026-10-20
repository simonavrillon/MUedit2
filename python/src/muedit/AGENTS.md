# Backend conventions

Rules for `python/src/muedit/`. Ruff and mypy (config in `pyproject.toml`) already enforce
formatting, imports, annotations and the bug-finder rules; this file covers what they can't.
Architecture: [dev/architecture.md](../../../dev/architecture.md). Wire formats:
[dev/contracts.md](../../../dev/contracts.md).

## Layers

- Imports flow one way: `api` → `editing` → `decomp` → `io` → `signal`, all over `models`,
  `errors` and `storage`. Never import `muedit.api` from a domain package.
- Routes stay thin: parse, call a service, return `success_payload(...)` or `frame_response(...)`.
  Logic goes in `api/services/`, or in a domain package when it isn't HTTP work.
- Arrays cross HTTP as MUB1 frames (`api/binary.py`), never as JSON lists.
- `muedit/__init__.py` exports only `installed_version()`; import from the sub-package, so
  `import muedit.paths` doesn't load SciPy.

## Errors and logging

- Raise a `MueditError` subclass (`muedit/errors.py`) for anything the user can fix, with
  `field=` naming the request field at fault. Its message reaches the user verbatim, so make it
  specific. Any other exception is a bug and becomes a generic 500.
- Don't catch exceptions to return error dicts; let `api/errors.py` answer.
- A broad `except Exception` needs `# noqa: BLE001` and a reason in parentheses, and must log.
- A user error is an INFO line. Something lost but survived is one WARNING saying what was lost.
  A bug is an ERROR with its traceback.

## Arrays and memory

- Full-length arrays go into an `ArrayStore` (`storage.py`) block by block
  (`store.allocate` → fill → `store.seal`), never built whole on the heap. Functions that create
  them take `store: ArrayStore | None = None` and fall back to `RamStore()`.
- Discharge times are sorted, unique, non-negative int32 sample indices: normalise with
  `models.spike_array`. Process them vectorised, with no per-spike Python loops.
- Use the array aliases from `models.py` (`FloatArray`, `IntArray`, `BoolArray`). `Any` is only
  for JSON and array boundaries (that's why `ANN401` is off).
- The work dtype follows `DecompositionParameters.compute_dtype` (float32 by default); accumulate
  in float64 where precision matters.

## Style

- Single-line docstrings on every module, class and function. Comment only what the code can't
  say: a spec rule (BIDS), a unit fact, an ordering constraint, why a constant has its value.
- Reuse the codebase's name for a concept: `fsamp`, `n_samples`, `data`, `grid_names`,
  `muscle_names`, `aux_data`, `aux_names`, `store`, `spike_times`, `mu_grid_index`. Grep first.
- Write the simplest change that does the job: no new abstraction layers or speculative options.
- Settings and results are `@dataclass` (frozen where nothing mutates them); request models are
  Pydantic, in `api/schemas.py` only.
- Constants are module-level `UPPER_CASE`, with a `#:` comment when the value needs a reason;
  environment variable names are `*_ENV` constants beside their default.
- Private helpers start with `_`; sub-packages declare their public names in `__all__`.

## Before you finish

`uv run ruff check . && uv run ruff format .`, `uv run mypy`, `uv run pytest -m "not data"`.
Update the doc that owns anything user-visible you changed (see the root `AGENTS.md`).
