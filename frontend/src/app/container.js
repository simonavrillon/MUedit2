import { API_BASE } from "../config.js";
import { createApiClient } from "../api/client.js";
import { createApp } from "./create-app.js";
import { els } from "./dom.js";
import { apiFetch, apiJson, SESSION_ID, waitForBackend } from "./http.js";
import { state } from "./state.js";
import { setupEditEvents } from "./stages/edit-stage.js";
import { setupImportEvents } from "./stages/import-stage.js";
import { setupLayoutEvents } from "./stages/layout-stage.js";
import { setupRunEvents } from "./stages/run-stage.js";

export async function initializeApp() {
  const app = createApp({
    state,
    els,
    api: createApiClient({
      apiFetch,
      apiJson,
      API_BASE,
      sessionId: SESSION_ID,
    }),
  });
  window.addEventListener("pagehide", (event) => {
    // A page kept in the back-forward cache can come back with its tokens.
    if (!event.persisted) app.api.closeSession();
  });
  setupImportEvents(app);
  setupRunEvents(app);
  setupEditEvents(app);
  setupLayoutEvents(app);
  app.updateStepAvailability();
  app.updateWorkflowStepper("import");

  if (els.browseSignalBtn) els.browseSignalBtn.disabled = true;
  app.setStatus("Connecting to backend…", "muted");

  if (await waitForBackend(app.api.healthUrl())) {
    if (els.browseSignalBtn) els.browseSignalBtn.disabled = false;
    app.setStatus("", "muted");
    // A reloaded page picks up the open edit session.
    await app.restoreEditSession();
  } else {
    app.setStatus("Backend unreachable — please restart the app", "error");
  }
}
