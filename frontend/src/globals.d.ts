// Globals the desktop app injects into the page.

/** The calls `muedit.desktop.DesktopApi` exposes to the page. */
export interface DesktopBridge {
  token(): Promise<string>;
  app_info(): Promise<{ version: string; data_root: string; log_file: string }>;
  open_file(): Promise<{ path: string | null; name: string | null }>;
  choose_output_folder(): Promise<{ path: string | null }>;
}

declare global {
  // eslint-disable-next-line no-var
  var pywebview: { api: DesktopBridge } | undefined;
}
