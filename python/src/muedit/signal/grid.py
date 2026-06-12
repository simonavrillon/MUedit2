"""Grid layout inference and basic signal helpers."""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class GridSpec:
    """Complete specification for one electrode grid model.

    To add a new grid, append an entry to ``_GRID_CATALOG`` below and
    fill in every field — no other file needs to change.
    """
    channel_map: np.ndarray   # 2-D layout; 0 = no electrode at that position
    nbelectrodes: int         # total active electrode count
    ied: float                # inter-electrode distance in mm
    emg_type: int             # 1 = surface HD-EMG, 2 = intramuscular
    manufacturer: str         # BIDS ElectrodeManufacturer
    electrode_type: str       # BIDS ElectrodeType
    electrode_material: str   # BIDS ElectrodeMaterial


# ── Grid catalogue ────────────────────────────────────────────────────────────
# Keys are matched as substrings of the grid_name string passed at runtime,
# so a name like "GR04MM1305_run1" still resolves correctly.
# Longer / more specific keys are listed first to avoid false-positive matches.
_GRID_CATALOG: dict[str, GridSpec] = {

    # ── OTBioelettronica 13×5 grids (64 ch) ─────────────────────────────────
    "GR04MM1305": GridSpec(
        channel_map=np.array([
            [ 0, 25, 26, 51, 52],
            [ 1, 24, 27, 50, 53],
            [ 2, 23, 28, 49, 54],
            [ 3, 22, 29, 48, 55],
            [ 4, 21, 30, 47, 56],
            [ 5, 20, 31, 46, 57],
            [ 6, 19, 32, 45, 58],
            [ 7, 18, 33, 44, 59],
            [ 8, 17, 34, 43, 60],
            [ 9, 16, 35, 42, 61],
            [10, 15, 36, 41, 62],
            [11, 14, 37, 40, 63],
            [12, 13, 38, 39, 64],
        ]),
        nbelectrodes=64, ied=4.0, emg_type=1,
        manufacturer="OTBioelettronica",
        electrode_type="surface array",
        electrode_material="gold coated",
    ),

    "HD04MM1305": GridSpec(
        channel_map=np.array([
            [52, 39, 26, 13,  0],
            [53, 40, 27, 14,  1],
            [54, 41, 28, 15,  2],
            [55, 42, 29, 16,  3],
            [56, 43, 30, 17,  4],
            [57, 44, 31, 18,  5],
            [58, 45, 32, 19,  6],
            [59, 46, 33, 20,  7],
            [60, 47, 34, 21,  8],
            [61, 48, 35, 22,  9],
            [62, 49, 36, 23, 10],
            [63, 50, 37, 24, 11],
            [64, 51, 38, 25, 12],
        ]),
        nbelectrodes=64, ied=4.0, emg_type=1,
        manufacturer="OTBioelettronica",
        electrode_type="surface array",
        electrode_material="gold coated",
    ),

    "GR08MM1305": GridSpec(
        channel_map=np.array([
            [ 0, 25, 26, 51, 52],
            [ 1, 24, 27, 50, 53],
            [ 2, 23, 28, 49, 54],
            [ 3, 22, 29, 48, 55],
            [ 4, 21, 30, 47, 56],
            [ 5, 20, 31, 46, 57],
            [ 6, 19, 32, 45, 58],
            [ 7, 18, 33, 44, 59],
            [ 8, 17, 34, 43, 60],
            [ 9, 16, 35, 42, 61],
            [10, 15, 36, 41, 62],
            [11, 14, 37, 40, 63],
            [12, 13, 38, 39, 64],
        ]),
        nbelectrodes=64, ied=8.0, emg_type=1,
        manufacturer="OTBioelettronica",
        electrode_type="surface array",
        electrode_material="gold coated",
    ),

    "HD08MM1305": GridSpec(
        channel_map=np.array([
            [52, 39, 26, 13,  0],
            [53, 40, 27, 14,  1],
            [54, 41, 28, 15,  2],
            [55, 42, 29, 16,  3],
            [56, 43, 30, 17,  4],
            [57, 44, 31, 18,  5],
            [58, 45, 32, 19,  6],
            [59, 46, 33, 20,  7],
            [60, 47, 34, 21,  8],
            [61, 48, 35, 22,  9],
            [62, 49, 36, 23, 10],
            [63, 50, 37, 24, 11],
            [64, 51, 38, 25, 12],
        ]),
        nbelectrodes=64, ied=8.0, emg_type=1,
        manufacturer="OTBioelettronica",
        electrode_type="surface array",
        electrode_material="gold coated",
    ),

    # ── OTBioelettronica 8×8 grids (64 ch) ──────────────────────────────────
    "GR10MM0808": GridSpec(
        channel_map=np.array([
            [ 8, 16, 24, 32, 40, 48, 56, 64],
            [ 7, 15, 23, 31, 39, 47, 55, 63],
            [ 6, 14, 22, 30, 38, 46, 54, 62],
            [ 5, 13, 21, 29, 37, 45, 53, 61],
            [ 4, 12, 20, 28, 36, 44, 52, 60],
            [ 3, 11, 19, 27, 35, 43, 51, 59],
            [ 2, 10, 18, 26, 34, 42, 50, 58],
            [ 1,  9, 17, 25, 33, 41, 49, 57],
        ]),
        nbelectrodes=64, ied=10.0, emg_type=1,
        manufacturer="OTBioelettronica",
        electrode_type="surface array",
        electrode_material="gold coated",
    ),

    "HD10MM0808": GridSpec(
        channel_map=np.array([
            [64, 56, 48, 40, 32, 24, 16,  8],
            [63, 55, 47, 39, 31, 23, 15,  7],
            [62, 54, 46, 38, 30, 22, 14,  6],
            [61, 53, 45, 37, 29, 21, 13,  5],
            [60, 52, 44, 36, 28, 20, 12,  4],
            [59, 51, 43, 35, 27, 19, 11,  3],
            [58, 50, 42, 34, 26, 18, 10,  2],
            [57, 49, 41, 33, 25, 17,  9,  1],
        ]),
        nbelectrodes=64, ied=10.0, emg_type=1,
        manufacturer="OTBioelettronica",
        electrode_type="surface array",
        electrode_material="gold coated",
    ),

    # ── OTBioelettronica 8×4 grids (32 ch) ──────────────────────────────────
    "GR10MM0804": GridSpec(
        channel_map=np.array([
            [32, 24, 16,  8],
            [31, 23, 15,  7],
            [30, 22, 14,  6],
            [29, 21, 13,  5],
            [28, 20, 12,  4],
            [27, 19, 11,  3],
            [26, 18, 10,  2],
            [25, 17,  9,  1],
        ]),
        nbelectrodes=32, ied=10.0, emg_type=1,
        manufacturer="OTBioelettronica",
        electrode_type="surface array",
        electrode_material="gold coated",
    ),

    "HD10MM0804": GridSpec(
        channel_map=np.array([
            [1,  9, 17, 25],
            [2, 10, 18, 26],
            [3, 11, 19, 27],
            [4, 12, 20, 28],
            [5, 13, 21, 29],
            [6, 14, 22, 30],
            [7, 15, 23, 31],
            [8, 16, 24, 32],
        ]),
        nbelectrodes=32, ied=10.0, emg_type=1,
        manufacturer="OTBioelettronica",
        electrode_type="surface array",
        electrode_material="gold coated",
    ),

    # ── Imperial College London prototype (64 ch) ────────────────────────────
    "protogrid_v1": GridSpec(
        channel_map=np.array([
            [ 0, 55,  0, 43,  0, 15,  0, 14,  0,  0],
            [ 0, 57, 53, 41, 25, 13, 24, 12,  4,  0],
            [ 0,  0, 45, 39, 23, 11, 22, 10,  2,  9],
            [ 0, 63, 47, 27, 21, 30, 20,  8,  5,  0],
            [ 0, 61, 49, 29, 19, 28, 18,  6,  7,  0],
            [ 0, 59, 51, 37, 17, 26, 16, 34,  1,  0],
            [64, 60, 54, 50, 44, 40, 35, 32,  0,  0],
            [ 0, 62, 56, 52, 46, 42, 36, 33, 31,  0],
            [ 0,  0, 58,  0, 48,  0, 38,  0,  3,  0],
        ]),
        nbelectrodes=64, ied=2.0, emg_type=1,
        manufacturer="Imperial College London",
        electrode_type="surface array",
        electrode_material="gold coated",
    ),

    # ── Intan Technologies 13×5 array (64 ch) ───────────────────────────────
    "intan64": GridSpec(
        channel_map=np.array([
            [37, 33, 34,  3,  1],
            [37, 46, 35,  5,  7],
            [39, 48, 36,  2,  9],
            [41, 50, 38, 18, 11],
            [43, 52, 40, 16, 13],
            [45, 54, 42, 14, 29],
            [47, 56, 44, 12, 27],
            [49, 58, 32, 10, 25],
            [51, 60, 31,  8, 23],
            [62, 53, 30,  6, 21],
            [64, 55, 28,  4, 19],
            [59, 57, 26, 20, 17],
            [61, 63, 24, 22, 15],
        ]),
        nbelectrodes=64, ied=4.0, emg_type=1,
        manufacturer="Intan Technologies",
        electrode_type="surface array",
        electrode_material="gold coated",
    ),

    # ── camber – Emory University intramuscular arrays (32 ch) ──────────────
    "MYOMRF-4x8": GridSpec(
        channel_map=np.array([
            [25,  1, 16, 24],
            [26,  2, 15, 23],
            [27,  3, 14, 22],
            [28,  4, 13, 21],
            [29,  5, 12, 20],
            [30,  6, 11, 19],
            [31,  7, 10, 18],
            [32,  8,  9, 17],
        ]),
        nbelectrodes=32, ied=1.0, emg_type=2,
        manufacturer="camber - Emory University",
        electrode_type="intramuscular array",
        electrode_material="gold coated",
    ),

    "MYOMNP-1x32": GridSpec(
        channel_map=np.array([
            [24, 25, 16,  1],
            [23, 26, 15,  2],
            [22, 27, 14,  3],
            [21, 28, 13,  4],
            [20, 29, 12,  5],
            [19, 30, 11,  6],
            [18, 31, 10,  7],
            [17, 32,  9,  8],
        ]),
        nbelectrodes=32, ied=1.0, emg_type=2,
        manufacturer="camber - Emory University",
        electrode_type="intramuscular array",
        electrode_material="gold coated",
    ),
}
# ── End of catalogue ──────────────────────────────────────────────────────────


def _find_spec(grid_name: str) -> GridSpec | None:
    """Return the GridSpec whose key is a substring of *grid_name*, or None."""
    for key, spec in _GRID_CATALOG.items():
        if key in grid_name:
            return spec
    return None


def format_hdemg_signal(
    grid_names: list[str],
    discard_overrides: list[list[int]] | None = None,
) -> tuple[list[np.ndarray], list[float], list[np.ndarray], list[int]]:
    """Infer grid geometry and channel masks for HD-EMG recordings."""
    coordinates = []
    ied = []
    discard_channels_vec = []
    emg_type = []

    for i, grid_name in enumerate(grid_names):
        spec = _find_spec(grid_name)

        if spec is None:
            raise ValueError(
                f"Unknown grid model '{grid_name}'. "
                "Add an entry to _GRID_CATALOG in signal/grid.py or correct the grid name."
            )

        el_channel_map = spec.channel_map
        nbelectrodes = spec.nbelectrodes
        current_ied = spec.ied
        current_emg_type = spec.emg_type

        coords = np.zeros((nbelectrodes, 2))
        rows, cols = el_channel_map.shape
        for r in range(rows):
            for c in range(cols):
                val = el_channel_map[r, c]
                if 0 < val <= nbelectrodes:
                    coords[val - 1, 0] = r
                    coords[val - 1, 1] = c

        coordinates.append(coords)
        ied.append(current_ied)
        emg_type.append(current_emg_type)

        discard_mask = np.zeros(nbelectrodes, dtype=int)
        if discard_overrides and i < len(discard_overrides):
            try:
                mask_arr = np.array(discard_overrides[i], dtype=int)
                if mask_arr.size == nbelectrodes:
                    discard_mask = mask_arr
            except (IndexError, TypeError, ValueError):
                pass

        discard_channels_vec.append(discard_mask)

    return coordinates, ied, discard_channels_vec, emg_type


def get_grid_electrode_metadata(grid_name: str) -> dict:
    """Return BIDS electrode metadata for the given grid model name."""
    spec = _find_spec(grid_name or "")
    if spec is None:
        return {
            "ElectrodeManufacturer": "n/a",
            "ElectrodeManufacturersModelName": grid_name or "n/a",
            "ElectrodeType": "surface array",
            "ElectrodeMaterial": "n/a",
        }
    return {
        "ElectrodeManufacturer": spec.manufacturer,
        "ElectrodeManufacturersModelName": grid_name,
        "ElectrodeType": spec.electrode_type,
        "ElectrodeMaterial": spec.electrode_material,
    }
