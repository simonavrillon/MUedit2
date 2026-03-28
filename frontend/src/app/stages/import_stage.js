export function createImportStageService(deps) {
  const {
    apiJson,
    API_BASE,
    setStatus,
    clearUploadFormatError,
    setUploadLoading,
    showUnsupportedUploadFormatError,
    detectLandingFileType,
    inferBidsRootFromSelectedPath,
    handleRawFilePath,
    loadDecompositionForEditByPath,
    setEditBidsRootInput,
  } = deps;

  async function handleNativeDialogOpen() {
    clearUploadFormatError();
    setUploadLoading(false);

    let result;
    try {
      result = await apiJson(`${API_BASE}/dialog/open-file`);
    } catch (err) {
      console.error("File dialog failed:", err);
      setStatus("Failed to open file dialog", "error");
      return;
    }

    if (!result.path) return;

    const { path, name } = result;
    const bidsRoot = inferBidsRootFromSelectedPath(path);
    setEditBidsRootInput(bidsRoot);

    const kind = detectLandingFileType({ name });

    if (kind === "unsupported") {
      showUnsupportedUploadFormatError();
      return;
    }
    if (kind === "raw") {
      await handleRawFilePath(path, name);
    } else if (kind === "decomposition") {
      await loadDecompositionForEditByPath(path);
    } else {
      const ok = await handleRawFilePath(path, name, {
        silentPreviewFailure: true,
      });
      if (!ok) await loadDecompositionForEditByPath(path);
    }
  }

  return { handleNativeDialogOpen };
}
