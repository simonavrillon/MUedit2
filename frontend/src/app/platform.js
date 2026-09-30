// What differs between the desktop app and the browser. The desktop page gets
// its API token, its file dialogs and its output folder from the pywebview
// bridge (`window.pywebview.api`, see `muedit/desktop.py`); the browser page
// uses the HTTP dialog route and needs no token.
import { setAppToken } from "./http.js";

/** @typedef {import("../globals").DesktopBridge} DesktopBridge */
/** @typedef {{ path: string | null, name: string | null }} PickedFile */

/** The desktop app loads the page with `?desktop=1`, which a reload keeps. */
export const IS_DESKTOP = new URLSearchParams(
  globalThis.location?.search ?? "",
).has("desktop");

/** @type {Promise<DesktopBridge> | null} */
let bridgePromise = null;

/**
 * The bridge, once pywebview has injected it; pywebview may do so before or
 * after this module runs, and fires `pywebviewready` when it is done.
 *
 * @returns {Promise<DesktopBridge>}
 */
function bridge() {
  bridgePromise ??= new Promise((resolve) => {
    const ready = () => {
      const api = globalThis.pywebview?.api;
      if (typeof api?.token !== "function") return false;
      resolve(api);
      return true;
    };
    if (!ready()) {
      globalThis.addEventListener("pywebviewready", ready, { once: true });
    }
  });
  return bridgePromise;
}

/** How long the desktop page waits for the bridge's token before giving up. */
const BRIDGE_TIMEOUT_MS = 15000;

/**
 * In the desktop app, wait for the bridge and send its token from now on;
 * false when no token came within `timeoutMs`.
 *
 * @param {{ timeoutMs?: number }} [options]
 * @returns {Promise<boolean>}
 */
export async function initPlatform({ timeoutMs = BRIDGE_TIMEOUT_MS } = {}) {
  if (!IS_DESKTOP) return true;
  /** @type {ReturnType<typeof setTimeout> | undefined} */
  let timer;
  /** @type {Promise<null>} */
  const timedOut = new Promise((resolve) => {
    timer = setTimeout(() => resolve(null), timeoutMs);
  });
  try {
    const token = await Promise.race([
      bridge().then((api) => api.token()),
      timedOut,
    ]);
    if (token === null) return false;
    setAppToken(token);
    return true;
  } finally {
    clearTimeout(timer);
  }
}

/**
 * A file the user picks: the app's native dialog on the desktop, else the
 * one `browserDialog` asks the backend to open.
 *
 * @param {() => Promise<PickedFile>} browserDialog
 * @returns {Promise<PickedFile>}
 */
export async function openFile(browserDialog) {
  return IS_DESKTOP ? (await bridge()).open_file() : browserDialog();
}

/** The folder outputs go to; desktop only. */
export async function outputFolder() {
  return (await (await bridge()).app_info()).data_root;
}

/**
 * Let the user move the output folder; the new path, or null when cancelled.
 * Desktop only.
 *
 * @returns {Promise<string | null>}
 */
export async function chooseOutputFolder() {
  return (await (await bridge()).choose_output_folder()).path;
}
