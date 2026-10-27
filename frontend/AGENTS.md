# Frontend conventions

Rules for `frontend/`. ESLint, Prettier and `tsc --checkJs` (`npm run check`) already enforce
formatting, kebab-case filenames, unused names and types; this file covers what they can't.
Architecture: [dev/architecture.md](../dev/architecture.md#frontend-shape). Wire formats:
[dev/contracts.md](../dev/contracts.md).

## Shape

- Vanilla ES modules, no framework, no bundler, no runtime dependencies. The browser loads
  `app.js` directly; don't add a build step.
- Every feature function takes the `app` context (`@param {App} app`) and destructures what it
  uses. A member goes on `app` (and on its typedef in `app/context.js`) only when another module
  calls it through `app`; everything else is imported where it's used.
- A service factory reads only `state`, `els` and `api` while it is constructed. Other services
  are reached as `app.x()` inside function bodies.
- Service and stage methods are typed from `context.js`: `/** @type {EditStage["name"]} */`.
  Only private helpers carry their own `@param`s.
- Type parameters as what callers should pass. When a function guards against missing input,
  write `T | null | undefined` and keep the guard. `unknown` is only for caught errors and
  validators of raw input. Name a shape next to the code that owns it (state shapes in
  `state/state.js`, cross-module ones in `context.js`); single-use bags stay inline.

## State and DOM

- Change state only through the setters in `state/actions.js`; read with `state/selectors.js`
  where a selector exists. A new raw file resets state through `state/transitions.js`.
- `state.edit` mirrors the server's edit session. Apply server answers with `setEditSession`,
  `applyEditChange` and `applyEditSave`; never patch discharge times locally.
- Every scripted element has a typed handle in `app/dom.js`, used as `app.els.x`. Element ids
  in `index.html` are referenced by the docs, so rename them with care.
- Hide elements with the `hidden` attribute (`el.hidden = true`); `css/base.css` makes it win
  over any `display` a class sets. There is no `.hidden` class.
- View modules (`view/`) only draw the state they're given. Ask for missing data in the stage's
  render member (`ensureEditPulseView`, `ensureQcTraces`), not in a draw function.
- Colours are CSS tokens in `css/tokens.css`. Canvases read them through `COLORS` in
  `config.js`: a new canvas colour is a token plus a `COLOR_TOKENS` entry, never a literal.

## Backend calls

- Paths live only in `api/routes.js`; calls only in `api/client.js`. `tests/test_api_app.py`
  fails if `routes.js` names a path the backend doesn't serve.
- Backend JSON is `JsonObject` until it is normalised at the boundary (`api/payloads.js`,
  `api/binary-payloads.js`). Regions arrive as `[start, end]` pairs and become `Span`s there.
- Branch on `ApiError.field` / `.status`, never on message text. Report failures with
  `handleError(err, setStatus, label)`.
- Requests that send `upload_token` go through `withUpload` (`app/services/upload.js`).

## Style

- Each module opens with a comment stating its role and its rules (see
  `app/services/editing-service.js`). Elsewhere, comment only what the code can't say.
- Reuse the names the backend and other modules use for the same concept (`fsamp`, `gridNames`,
  `muGridIndex`, `spikeTimes`).
- Write the simplest change that does the job.

## Tests

`node --test tests/*.test.js`. `tests/fake-dom.js` (a minimal DOM and a recording 2D context)
and `tests/frames.js` (MUB1 encoding like the backend's `pack_frame`) are helpers, not tests.
Replace `app` members with recorders to test a stage alone. When a frame layout changes, update
`frames.js` together with `api/binary.py`.

## Before you finish

`npm run check` (eslint, prettier --check, tsc, node --test). For UI changes, run the app and
look. Update `docs/user-guide.md` for any label, control or shortcut you changed.
