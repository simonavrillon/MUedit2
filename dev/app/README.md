# MUedit Frontend Development Docs

Auto-generated documentation of the MUedit2 frontend workflow, architecture, and code surface.

## Contents

| File | Description |
|---|---|
| [01-architecture.md](01-architecture.md) | Boot sequence, dependency injection, state model, stage lifecycle |
| [02-workflow-stages.md](02-workflow-stages.md) | The four-stage user workflow: Import, QC, Decompose, Edit |
| [03-edit-stage-controls.md](03-edit-stage-controls.md) | Every editing control button, keyboard shortcut, and operation |
| [04-api-surface.md](04-api-surface.md) | All API endpoints, payloads, and binary formats |
| [05-worktree.md](05-worktree.md) | Worktree: user-exposed elements vs app-internal functions, per module |

## How to Use This Documentation

- **Understanding the app flow**: start with `02-workflow-stages.md`
- **Understanding the codebase architecture**: start with `01-architecture.md`
- **Finding a specific button/action**: check `03-edit-stage-controls.md`
- **Finding an API endpoint**: check `04-api-surface.md`
- **Checking who calls a function or uses an element**: check `05-worktree.md`

## Frontend File Map

```
frontend/
├── index.html                          Static DOM scaffolding (all UI elements)
├── app.js                               Entry point (imports initializeApp)
└── src/
    ├── config.js                        Constants: API_BASE, COLORS, extensions
    ├── api/
    │   ├── client.js                    API client factory (all HTTP calls)
    │   ├── routes.js                    Endpoint path table
    │   ├── payloads.js                  Response normalizers
    │   └── binary-payloads.js           Binary format decoders (MQCR, MELD, MDPV)
    ├── app/
    │   ├── container.js                 DI composition root (wires all services)
    │   ├── deps.js                      JSDoc typedefs for DI contracts
    │   ├── dom.js                       Centralized getElementById map (els)
    │   ├── http.js                      apiFetch, apiJson, waitForBackend
    │   ├── state.js                     Global state object + createEditSlice
    │   ├── services/
    │   │   ├── navigation.js            Stage transitions, keyboard nav, view math
    │   │   ├── ui.js                    UI service (toggles, progress, status, layout)
    │   │   ├── layout.js                Settings panel, resize observer
    │   │   ├── file-session.js          BIDS form fields, file type detection
    │   │   ├── editing-service.js        Edit API calls (ROI, filter, save, load)
    │   │   └── error-service.js         Error handler
    │   └── stages/
    │       ├── import-stage.js          File-open dialog, landing interactions
    │       ├── qc-stage.js              QC/preview: raw file loading, ROI, channel QC
    │       ├── run-stage.js             Decomposition execution, MU explorer
    │       ├── edit-stage.js            Spike editing, filter updates, save
    │       └── layout-stage.js          Section collapse, settings toggle events
    ├── decomp/
    │   ├── explorer.js                  Run-stage MU dropdown/explorer model builders
    │   ├── params.js                    Decompose parameter builder
    │   └── run.js                       Decomposition runner + stream handler
    ├── editing/
    │   └── operations.js                Edit operations (backup, restore, add/delete spikes)
    ├── io/
    │   ├── bids.js                      BIDS entity builders and parsers
    │   └── grid.js                      Grid dimension inference
    ├── signal/
    │   └── qc.js                        QC feature logic (preview, grid window, file handling)
    ├── state/
    │   ├── actions.js                   State mutators (set* functions)
    │   ├── selectors.js                 State selectors (get* functions)
    │   └── transitions.js               State transitions (begin/rollback raw preview)
    └── view/
        ├── bids-renderer.js             BIDS form rendering
        ├── edit-canvas.js               Edit-stage canvas rendering + interaction binding
        ├── explorer.js                   Run-stage explorer rendering
        ├── plots.js                      Low-level canvas drawing primitives
        ├── qc-renderer.js               QC visual rendering (channel grid, aux, ROI)
        └── select-renderers.js          Paired grid/MU dropdown renderer
```
