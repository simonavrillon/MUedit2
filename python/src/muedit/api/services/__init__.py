"""Service-layer modules for API adapters."""

from __future__ import annotations

from muedit.api.services.decompose_service import (
    decomposition_event_stream,
    fetch_decompose_preview_binary,
    parse_stream_options,
    resolve_decompose_input,
    run_decomposition_once,
)
from muedit.api.services.editing_service import (
    add_artifact,
    add_spikes,
    delete_dr,
    delete_spikes,
    flag_mu,
    load_decomposition,
    load_decomposition_binary,
    load_decomposition_binary_from_path,
    load_decomposition_from_path,
    remove_duplicates_service,
    remove_outliers,
    save_edits,
    update_filter,
)
from muedit.api.services.preview_service import (
    build_preview,
    build_preview_from_path,
    get_qc_window,
)

__all__ = [
    "add_artifact",
    "add_spikes",
    "build_preview",
    "build_preview_from_path",
    "decomposition_event_stream",
    "delete_dr",
    "delete_spikes",
    "fetch_decompose_preview_binary",
    "flag_mu",
    "get_qc_window",
    "load_decomposition",
    "load_decomposition_binary",
    "load_decomposition_binary_from_path",
    "load_decomposition_from_path",
    "parse_stream_options",
    "remove_duplicates_service",
    "remove_outliers",
    "resolve_decompose_input",
    "run_decomposition_once",
    "save_edits",
    "update_filter",
]
