import { DEFAULT_BIDS_ROOT } from "../../config.js";

export function createFileSessionService(deps) {
  const { els } = deps;

  function getBidsRoot() {
    const fromInput = (els.editBidsRoot?.value || "").trim();
    return fromInput || DEFAULT_BIDS_ROOT;
  }

  function getBidsMuscleNames() {
    const inputs =
      els.bidsMuscleContainer?.querySelectorAll(".bids-muscle-input");
    if (!inputs?.length) return [];
    return Array.from(inputs)
      .map((input) => String(input.value || "").trim())
      .filter(Boolean);
  }

  function clearUploadFormatError() {
    if (!els.uploadFormatError) return;
    els.uploadFormatError.textContent = "";
    els.uploadFormatError.classList.add("hidden");
  }

  function showUnsupportedUploadFormatError() {
    if (!els.uploadFormatError) return;
    els.uploadFormatError.textContent =
      "Accepted: raw (.mat, .otb+, .otb4) or decomposition (.npz, .mat)";
    els.uploadFormatError.classList.remove("hidden");
  }

  function isSupportedSignalFile(file, extensions) {
    const name = (file?.name || "").toLowerCase();
    return (
      extensions.raw.some((ext) => name.endsWith(ext)) ||
      extensions.decomposition.some((ext) => name.endsWith(ext))
    );
  }

  function detectLandingFileType(file) {
    const name = (file?.name || "").toLowerCase();
    if (name.endsWith(".otb+") || name.endsWith(".otb4")) return "raw";
    if (name.endsWith(".npz")) return "decomposition";
    if (name.endsWith(".mat")) return "ambiguous_mat";
    return "unsupported";
  }

  function inferBidsRootFromSelectedPath(filePath) {
    const rawPath = String(filePath || "");
    const normalized = rawPath.replace(/\\/g, "/");
    const parts = normalized.split("/");
    const subIndex = parts.findIndex((p) => /^sub-[^/]+$/i.test(p));

    if (subIndex > 0) {
      const root = parts.slice(0, subIndex).join("/");
      if (root) return root;
    }

    const marker = "/muedit_out";
    const markerIndex = normalized.toLowerCase().lastIndexOf(marker);
    if (markerIndex >= 0) {
      return normalized.slice(0, markerIndex + marker.length);
    }

    const lastSep = Math.max(rawPath.lastIndexOf("/"), rawPath.lastIndexOf("\\"));
    const dir = lastSep >= 0 ? rawPath.substring(0, lastSep) : ".";
    const sep = rawPath.includes("/") ? "/" : "\\";
    return dir + sep + "muedit_out";
  }

  function setUploadLoading(active) {
    if (!els.uploadLoader) return;
    els.uploadLoader.classList.toggle("hidden", !active);
  }

  return {
    getBidsRoot,
    getBidsMuscleNames,
    clearUploadFormatError,
    showUnsupportedUploadFormatError,
    isSupportedSignalFile,
    detectLandingFileType,
    inferBidsRootFromSelectedPath,
    setUploadLoading,
  };
}
