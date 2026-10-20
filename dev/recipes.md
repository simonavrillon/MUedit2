# Recipes

Checklists for the changes this repo sees most, built from the files past commits actually
touched together. Each ends with how to check the change. Read
[architecture.md](architecture.md) first if the layer names are new to you.

## Add an API route

Example: `GET /projects` (commit `8dcca6d`).

1. **Route**: in `api/routes/<area>.py`, `APIRouter(prefix="/api/v1")`. Keep it thin: parse,
   call a service, return `success_payload(...)` or a MUB1 `frame_response(...)`.
2. **Request model**: a Pydantic model in `api/schemas.py` (no free-form dicts).
3. **Logic**: in `api/services/<area>_service.py`, or in the domain package if it isn't HTTP
   work. Raise `MueditError` (or a subclass) with `field=` for anything the user can fix.
4. **Register** a new router in `api/routes/__init__.py: include_routers`.
5. **Frontend**: the path in `frontend/src/api/routes.js`, a method in `api/client.js`, and a
   normaliser in `api/payloads.js` if the response needs typing.
6. **Tests**: add the endpoint to `LIVE_ENDPOINTS` in `tests/test_api_app.py`, and test the
   behaviour through `TestClient` in `tests/test_api_<area>.py` (helpers in `tests/_api.py`).

Check: `uv run pytest tests/test_api_app.py tests/test_api_<area>.py`, `npm run check`.

## Add an edit operation

1. **Computation**: a pure function in `editing/operations.py` that takes discharge times
   (normalised with `spike_array`) and returns new ones, vectorised.
2. **Session**: a method on `EditSession` named after the op (`my-op` → `my_op`), added to
   `EditSession.OPS`. Snapshot the MU with `_begin` before changing it so undo and rollback
   work; append a history entry; return a `Change` naming the `changed` MUs.
3. **Schema**: a payload model in `api/schemas.py` and its `EDIT_OPS` entry. The route is
   generic (`/edit/ops/{op}`), so there is nothing to add in `routes/`.
4. **Frontend**: an action in `app/services/editing-service.js` that calls
   `requestEditOp(app, "my-op", args)`; an `EDIT_COMMANDS` entry in `app/stages/edit-stage.js`
   (`button`, `key`, and `edit` for an op that runs at once, or `run` arming a mode for one
   drawn as a box, with its `PULSE_BOX_EDITS` entry in `view/edit-canvas.js`); the button in
   `index.html` and its handle in `app/dom.js`.
5. **Docs**: the button and its key in `docs/user-guide.md` (Editing tools, shortcuts); a new
   history entry type in `docs/saved-files.md` (Edit log).

Op names, argument names and history types are persisted (see
[contracts.md](contracts.md#edit-operations)): pick them once.

Check: `tests/test_editing_operations.py`, `tests/test_editing_session.py` (undo, replay),
`tests/test_api_editing.py`, `frontend/tests/edit-commands.test.js`.

## Add a decomposition parameter

1. **Field** on `DecompositionParameters` (`decomp/types.py`) with its default; range-check it in
   `__post_init__`. The API (422) and the CLI (usage error) both reject through it.
2. **CLI flag** in `cli.py` (`run_decomposition_cli`), defaulting to `_DEFAULT_PARAMS`.
3. **UI** (only if users should set it): the control in `index.html`, its handle in
   `app/dom.js`, the wire key in `decomp/params.js: buildDecomposeParams` and its `DecomposeParams`
   typedef. Parameters without a control are not sent, so the backend default applies.
4. **Docs**: `docs/cli.md` (flag) and `docs/user-guide.md` (settings panel) if it has a control.

Parameters are saved with every decomposition (`parameters` in the NPZ). If the edit stage's
filter refit must reuse it, read it from the file's `parameters` in `editing/session.py`, as
`nbextchan` and `peel_off_win` are.

Check: `tests/test_decomp_types.py`, `tests/test_cli.py`, `tests/test_api_decompose.py`.

## Add a UI control or label

1. The element in `frontend/index.html` with an `id`.
2. A typed handle in `app/dom.js` (`button(...)`, `input(...)`, `select(...)`, `canvas(...)`).
3. Wire it in the stage's `setup*Events`; change state only through `state/actions.js`.
4. If another stage must call the handler, put it on the stage's typedef in `app/context.js` and
   implement it under `/** @type {Stage["name"]} */`; otherwise import it where it's used.
5. The **bold** label and what it does in `docs/user-guide.md`, worded as in `index.html`.

Check: `npm run check`, and run the app (`uv run muedit api`) to see it.

## Add a key to the decomposition NPZ

1. Write it in `decomp/decomposition_file.py: save_decomposition_npz` and read it in
   `_npz_v2_decomp`, treating it as optional (older files lack it).
2. Pass it from both writers: the run (`decomp/postprocess.py: export_step`) and the edit saves
   (`api/services/editing_service.py: _write_decomposition`).
3. Document it in `docs/saved-files.md` (NPZ file format).

Check: `tests/test_decomp_decomposition_file.py`, `tests/test_api_editing_save.py`.

## Add an environment variable

Read it in the module that uses it, its name a `*_ENV` constant at the top beside its default
(`paths.py: CACHE_DIR_ENV`, `api/memory.py: BUDGET_ENV`, `storage.py: DISK_RESERVE_ENV`), and add
a row to the environment-variable table in `docs/cli.md`. If the launchers need it,
update both `scripts/run_MUedit.sh` and `scripts/run_MUedit.ps1`.

## Add a loader or an electrode grid

See [extending-loaders.md](extending-loaders.md).
