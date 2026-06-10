export function createFileSessionService(deps) {
  const { els } = deps;

  function getBidsProject() {
    return (els.bidsProject?.value || "").trim();
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
      "Accepted: raw (.mat, .otb+, .otb4, .bdf, .edf) or decomposition (.npz, .mat)";
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
    if (name.endsWith(".bdf") || name.endsWith(".edf")) return "raw";
    if (name.endsWith(".npz")) return "decomposition";
    if (name.endsWith(".mat")) return "ambiguous_mat";
    return "unsupported";
  }

  function setUploadLoading(active) {
    if (!els.uploadLoader) return;
    els.uploadLoader.classList.toggle("hidden", !active);
  }

  return {
    getBidsProject,
    getBidsMuscleNames,
    clearUploadFormatError,
    showUnsupportedUploadFormatError,
    isSupportedSignalFile,
    detectLandingFileType,
    setUploadLoading,
  };
}
