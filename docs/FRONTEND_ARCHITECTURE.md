# Frontend Architecture

## Scope

Frontend code lives in `frontend/` and is a static browser app.

- HTML shell: `frontend/index.html`
- Styles: `frontend/style.css`
- Runtime entrypoint: `frontend/app.js`

## Runtime Entrypoint

`frontend/app.js` imports `initializeApp` from `frontend/src/app/container.js` and executes it.

There is no `bootstrap.js` layer anymore.

## Layer Map

- `frontend/src/app/`
  - Composition root, dependency wiring, and stage/service assembly.
  - Main file: `app/container.js`
  - Stage services: `app/stages/*.js`
  - Shared service wrappers: `app/services/*.js`
  - Dependency typedefs: `app/deps.js`

- `frontend/src/setup/`
  - Event binding only (DOM listeners -> orchestrator/stage methods).
  - Files: `import.js`, `run.js`, `edit.js`, `layout.js`

- `frontend/src/features/`
  - Use-case/domain logic and backend orchestration.
  - Includes decomposition flow, preview hydration, edit actions, navigation, and BIDS UI model helpers.

- `frontend/src/controllers/`
  - Rendering + view synchronization (canvas + DOM updates).
  - Files include `qc_canvas.js`, `mu_explorer.js`, `workflow.js`, `layout.js`, `bids_form.js`.

- `frontend/src/state/` + `frontend/src/state.js`
  - Mutable app state, pure selectors, and transition/mutation helpers.

- `frontend/src/api/` and `frontend/src/contracts/`
  - Binary payload decode + payload normalization before state hydration.

- Shared utilities
  - `config.js`, `http.js`, `plots.js`, `session.js`, `dom.js`

## Dependency Direction

Expected direction:

1. `app/*` can depend on all frontend modules.
2. `app/stages/*` coordinate features/controllers/state via injected deps.
3. `setup/*` wires DOM events to app/stage entrypoints.
4. `features/*` may depend on `state`, `api`, `contracts`, and shared utils.
5. `controllers/*` should stay rendering-oriented and avoid business orchestration.
6. `state/*` is leaf-level and does not depend on upper layers.

Boundary rules are documented here and enforced by code review/testing practices.

## Stage Responsibilities

- `import_stage.js`
  - Handles native file dialog flow and routes file types:
    - raw signal preview path
    - decomposition edit-load path

- `qc_stage.js`
  - Preview loading, QC channels render, ROI selection integration.

- `run_stage.js`
  - Decomposition execution, progress stream handling, MU explorer for run output.

- `edit_stage.js`
  - Edit workspace rendering, edit actions, save/export, MU-level editing interactions.

- `layout_stage.js`
  - Settings panel open/close and layout resize policies.

## Upload And Input Flow

Current import UX is dialog-based:

- Landing button (`browseSignalBtn`) triggers `/dialog/open-file`.
- Result path/name is classified (`raw` vs `decomposition`) and routed accordingly.
- `signalFileInput` exists in DOM but file drag-and-drop upload handlers are not part of the current frontend flow.

## State And Rendering Model

- State is centralized in `state.js` and mutated through `state/actions.js`.
- Selectors in `state/selectors.js` provide derived lookups (current grid/MU, index mapping, etc.).
- Canvas rendering is centralized through `plots.js` helpers.
- QC/edit interactions (ROI/selection dragging) are implemented in controller/feature modules, not in generic setup wiring.

## Transport Contracts

- Decompose preview/edit payloads may arrive as binary (`application/octet-stream`).
- `api/binary_payloads.js` decodes low-level buffers.
- `contracts/payloads.js` normalizes payload shapes before state updates.

## Practical Maintenance Notes

1. Keep wiring changes in `app/container.js`; avoid hidden orchestration in controllers.
2. Keep `setup/*` thin (listeners only).
3. When adding new UI elements, register refs in `dom.js` and pass through deps rather than ad-hoc `document.getElementById` calls.
4. Prefer updating this document at module/responsibility level instead of maintaining a per-function inventory, which drifts quickly.
