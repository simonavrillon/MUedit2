/**
 * Layout stage service keeps UI layout concerns isolated from app orchestration.
 */
export function createLayoutStageService(deps) {
  const {
    ensureSettingsToggleIcon,
    toggleSettingsOpen,
    setSettingsOpen,
    initLayoutResizePolicy,
  } = deps;

  return {
    ensureSettingsToggleIcon,
    toggleSettingsOpen,
    setSettingsOpen,
    initLayoutResizePolicy,
  };
}
