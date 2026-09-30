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
    │   └── binary-payloads.js           Binary codecs (MUB1 frames, viewport series)
    ├── app/
    │   ├── container.js                 Entry: builds the app context, wires events
    │   ├── create-app.js                Merges every service into one `app` context
    │   ├── context.js                   `App` typedef: the contract every service meets
    │   ├── dom.js                       Typed getElementById map (els)
    │   ├── http.js                      apiFetch, apiJson, waitForBackend, session header
    │   ├── state.js                     Global state object + createEditSlice (mirror of the edit session)
    │   ├── services/
    │   │   ├── navigation.js            Stepper, showWorkspace, keyboard nav, view math
    │   │   ├── ui.js                    UI service (toggles, status, layout)
    │   │   ├── layout.js                Settings panel, resize observer
    │   │   ├── file-session.js          BIDS form fields, file type detection, save
    │   │   ├── editing-service.js        Edit session calls (open, recover, ops, save, restore)
    │   │   ├── view-fetcher.js          One pulse-window request in flight, only the latest waits
    │   │   └── error-service.js         Error handler
    │   └── stages/
    │       ├── lifecycle.js             Stage registry: switchStage, enter/exit/render hooks
    │       ├── import-stage.js          File-open dialog, landing interactions
    │       ├── qc-stage.js              QC/preview: raw file loading, ROI, channel QC
    │       ├── run-stage.js             Decomposition execution, the live run page
    │       ├── edit-stage.js            Spike editing, filter updates, save
    │       └── layout-stage.js          Section collapse, settings toggle events
    ├── decomp/
    │   ├── live.js                       The run as the run page shows it (phases, dots, summary, plan)
    │   ├── params.js                    Decompose parameter builder
    │   └── run.js                       Decomposition runner + stream handler
    ├── editing/
    │   └── operations.js                Drawn boxes → edit requests, discharge rates, dropdown model
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
        ├── run-live.js                  Run page rendering (plan, live search dots, result)
        ├── plots.js                      Low-level canvas drawing primitives
        ├── qc-renderer.js               QC visual rendering (channel grid, aux, ROI)
        └── select-renderers.js          Paired grid/MU dropdown renderer
```
