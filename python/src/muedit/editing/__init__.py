"""Post-hoc spike editing operations for motor unit decompositions."""

from __future__ import annotations

from muedit.editing.operations import (
    FilterUpdateResult,
    SpikeTimes,
    add_artifact_in_roi,
    add_spikes_in_roi,
    delete_high_discharge_rate_spikes_in_roi,
    delete_spikes_in_roi,
    remove_discharge_rate_outliers,
    update_motor_unit_filter_window,
)

__all__ = [
    "FilterUpdateResult",
    "SpikeTimes",
    "add_artifact_in_roi",
    "add_spikes_in_roi",
    "delete_high_discharge_rate_spikes_in_roi",
    "delete_spikes_in_roi",
    "remove_discharge_rate_outliers",
    "update_motor_unit_filter_window",
]
