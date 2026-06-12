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

  // Raw BIDS entity inputs (subject/task/session/run) used to compose the
  // entity label. Returned untransformed so the caller owns label assembly.
  function getBidsEntityInputs() {
    return {
      subject: els.bidsSubject?.value,
      task: els.bidsTask?.value,
      session: els.bidsSession?.value,
      run: els.bidsRun?.value,
    };
  }

  // Gather the participant + hardware BIDS form fields into the snake_case
  // shape the /edit/save endpoint expects, ready to spread into the request
  // body. Keeps all save-form DOM reads here rather than in the orchestrator.
  function getBidsSaveFields() {
    const age = String(els.bidsParticipantAge?.value || "").trim();
    const sex = String(els.bidsParticipantSex?.value || "").trim();
    const handedness = String(
      els.bidsParticipantHandedness?.value || "",
    ).trim();
    const participantMeta =
      age || sex || handedness
        ? {
            age: age || "n/a",
            sex: sex || "n/a",
            handedness: handedness || "n/a",
          }
        : null;

    return {
      project: getBidsProject(),
      participant_meta: participantMeta,
      powerline_freq: Number(els.bidsPowerlineFreq?.value || 50),
      manufacturer: String(els.bidsManufacturer?.value || "").trim() || null,
      manufacturers_model_name:
        String(els.bidsDeviceModel?.value || "").trim() || null,
      placement_scheme: String(
        els.bidsPlacementScheme?.value || "ChannelSpecific",
      ),
      placement_scheme_description:
        String(els.bidsPlacementDescription?.value || "").trim() || null,
      task_description:
        String(els.bidsTaskDescription?.value || "").trim() || null,
    };
  }

  return {
    getBidsProject,
    getBidsMuscleNames,
    getBidsEntityInputs,
    getBidsSaveFields,
    clearUploadFormatError,
    showUnsupportedUploadFormatError,
    isSupportedSignalFile,
    detectLandingFileType,
    setUploadLoading,
  };
}
