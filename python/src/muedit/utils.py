"""General-purpose numerical helpers shared across decomposition modules."""

import logging

import numpy as np

logger = logging.getLogger(__name__)


def demean(signal):
    """Remove per-channel DC offset from a 2D signal array."""
    return signal - np.mean(signal, axis=1, keepdims=True)


def format_hdemg_signal(signal, grid_names, fsamp, discard_overrides=None):
    """Infer grid geometry and channel masks for HD-EMG recordings."""
    coordinates = []
    ied = []
    discard_channels_vec = []
    emg_type = []

    ch_idx = 0

    for i, grid_name in enumerate(grid_names):
        el_channel_map = None
        nbelectrodes = 64
        current_ied = 4
        current_emg_type = 1

        if "GR04MM1305" in grid_name:
            el_channel_map = np.array(
                [
                    [0, 25, 26, 51, 52],
                    [1, 24, 27, 50, 53],
                    [2, 23, 28, 49, 54],
                    [3, 22, 29, 48, 55],
                    [4, 21, 30, 47, 56],
                    [5, 20, 31, 46, 57],
                    [6, 19, 32, 45, 58],
                    [7, 18, 33, 44, 59],
                    [8, 17, 34, 43, 60],
                    [9, 16, 35, 42, 61],
                    [10, 15, 36, 41, 62],
                    [11, 14, 37, 40, 63],
                    [12, 13, 38, 39, 64],
                ]
            )
            current_ied = 4

        elif "HD04MM1305" in grid_name:
            el_channel_map = np.array(
                [
                    [52, 39, 26, 13, 0],
                    [53, 40, 27, 14, 1],
                    [54, 41, 28, 15, 2],
                    [55, 42, 29, 16, 3],
                    [56, 43, 30, 17, 4],
                    [57, 44, 31, 18, 5],
                    [58, 45, 32, 19, 6],
                    [59, 46, 33, 20, 7],
                    [60, 47, 34, 21, 8],
                    [61, 48, 35, 22, 9],
                    [62, 49, 36, 23, 10],
                    [63, 50, 37, 24, 11],
                    [64, 51, 38, 25, 12],
                ]
            )
            current_ied = 4

        elif "GR08MM1305" in grid_name in grid_name:
            el_channel_map = np.array(
                [
                    [0, 25, 26, 51, 52],
                    [1, 24, 27, 50, 53],
                    [2, 23, 28, 49, 54],
                    [3, 22, 29, 48, 55],
                    [4, 21, 30, 47, 56],
                    [5, 20, 31, 46, 57],
                    [6, 19, 32, 45, 58],
                    [7, 18, 33, 44, 59],
                    [8, 17, 34, 43, 60],
                    [9, 16, 35, 42, 61],
                    [10, 15, 36, 41, 62],
                    [11, 14, 37, 40, 63],
                    [12, 13, 38, 39, 64],
                ]
            )
            current_ied = 8

        elif "HD08MM1305" in grid_name:
            el_channel_map = np.array(
                [
                    [52, 39, 26, 13, 0],
                    [53, 40, 27, 14, 1],
                    [54, 41, 28, 15, 2],
                    [55, 42, 29, 16, 3],
                    [56, 43, 30, 17, 4],
                    [57, 44, 31, 18, 5],
                    [58, 45, 32, 19, 6],
                    [59, 46, 33, 20, 7],
                    [60, 47, 34, 21, 8],
                    [61, 48, 35, 22, 9],
                    [62, 49, 36, 23, 10],
                    [63, 50, 37, 24, 11],
                    [64, 51, 38, 25, 12],
                ]
            )
            current_ied = 8

        elif "GR10MM0808" in grid_name:
            el_channel_map = np.array(
                [
                    [8, 16, 24, 32, 40, 48, 56, 64],
                    [7, 15, 23, 31, 39, 47, 55, 63],
                    [6, 14, 22, 30, 38, 46, 54, 62],
                    [5, 13, 21, 29, 37, 45, 53, 61],
                    [4, 12, 20, 28, 36, 44, 52, 60],
                    [3, 11, 19, 27, 35, 43, 51, 59],
                    [2, 10, 18, 26, 34, 42, 50, 58],
                    [1, 9, 17, 25, 33, 41, 49, 57],
                ]
            )
            current_ied = 10

        elif "HD10MM0808" in grid_name:
            el_channel_map = np.array(
                [
                    [64, 56, 48, 40, 32, 24, 16, 8],
                    [63, 55, 47, 39, 31, 23, 15, 7],
                    [62, 54, 46, 38, 30, 22, 14, 6],
                    [61, 53, 45, 37, 29, 21, 13, 5],
                    [60, 52, 44, 36, 28, 20, 12, 4],
                    [59, 51, 43, 35, 27, 19, 11, 3],
                    [58, 50, 42, 34, 26, 18, 10, 2],
                    [57, 49, 41, 33, 25, 17, 9, 1],
                ]
            )
            current_ied = 10


        elif "GR10MM0804" in grid_name:
            el_channel_map = np.array(
                [
                    [32, 24, 16, 8],
                    [31, 23, 15, 7],
                    [30, 22, 14, 6],
                    [29, 21, 13, 5],
                    [28, 20, 12, 4],
                    [27, 19, 11, 3],
                    [26, 18, 10, 2],
                    [25, 17, 9, 1],
                ]
            )
            nbelectrodes = 32
            current_ied = 10

        elif "HD10MM0804" in grid_name:
            el_channel_map = np.array(
                [
                    [1, 9, 17, 25],
                    [2, 10, 18, 26],
                    [3, 11, 19, 27],
                    [4, 12, 20, 28],
                    [5, 13, 21, 29],
                    [6, 14, 22, 30],
                    [7, 15, 23, 31],
                    [8, 16, 24, 32],
                ]
            )
            nbelectrodes = 32
            current_ied = 10

        elif "protogrid_v1" in grid_name:
            el_channel_map = np.array(
                [
                    [0, 55, 0, 43, 0, 15, 0, 14, 0, 0],
                    [0, 57, 53, 41, 25, 13, 24, 12, 4, 0],
                    [0, 0, 45, 39, 23, 11, 22, 10, 2, 9],
                    [0, 63, 47, 27, 21, 30, 20, 8, 5, 0],
                    [0, 61, 49, 29, 19, 28, 18, 6, 7, 0],
                    [0, 59, 51, 37, 17, 26, 16, 34, 1, 0],
                    [64, 60, 54, 50, 44, 40, 35, 32, 0, 0],
                    [0, 62, 56, 52, 46, 42, 36, 33, 31, 0],
                    [0, 0, 58, 0, 48, 0, 38, 0, 3, 0],
                ]
            )
            current_ied = 2

        elif "intan64" in grid_name:
            el_channel_map = np.array(
                [
                    [37, 33, 34, 3, 1],
                    [37, 46, 35, 5, 7],
                    [39, 48, 36, 2, 9],
                    [41, 50, 38, 18, 11],
                    [43, 52, 40, 16, 13],
                    [45, 54, 42, 14, 29],
                    [47, 56, 44, 12, 27],
                    [49, 58, 32, 10, 25],
                    [51, 60, 31, 8, 23],
                    [62, 53, 30, 6, 21],
                    [64, 55, 28, 4, 19],
                    [59, 57, 26, 20, 17],
                    [61, 63, 24, 22, 15],
                ]
            )
            current_ied = 4

        elif "MYOMRF-4x8" in grid_name:
            el_channel_map = np.array(
                [
                    [25, 1, 16, 24],
                    [26, 2, 15, 23],
                    [27, 3, 14, 22],
                    [28, 4, 13, 21],
                    [29, 5, 12, 20],
                    [30, 6, 11, 19],
                    [31, 7, 10, 18],
                    [32, 8, 9, 17],
                ]
            )
            nbelectrodes = 32
            current_ied = 1
            current_emg_type = 2

        elif "MYOMNP-1x32" in grid_name:
            el_channel_map = np.array(
                [
                    [24, 25, 16, 1],
                    [23, 26, 15, 2],
                    [22, 27, 14, 3],
                    [21, 28, 13, 4],
                    [20, 29, 12, 5],
                    [19, 30, 11, 6],
                    [18, 31, 10, 7],
                    [17, 32, 9, 8],
                ]
            )
            nbelectrodes = 32
            current_ied = 1
            current_emg_type = 2

        else:
            logger.warning("Unknown grid type '%s'. Using default 8x8.", grid_name)
            el_channel_map = np.arange(1, 65).reshape(8, 8)
            current_ied = 10

        coords = np.zeros((nbelectrodes, 2))
        if el_channel_map is not None:
            rows, cols = el_channel_map.shape
            for r in range(rows):
                for c in range(cols):
                    val = el_channel_map[r, c]
                    if val > 0 and val <= nbelectrodes:
                        coords[val - 1, 0] = r
                        coords[val - 1, 1] = c

        coordinates.append(coords)
        ied.append(current_ied)
        emg_type.append(current_emg_type)

        grid_signal = signal[ch_idx : ch_idx + nbelectrodes, :]
        discard_mask = np.zeros(nbelectrodes, dtype=int)

        override_applied = False
        if discard_overrides and i < len(discard_overrides):
            try:
                mask_arr = np.array(discard_overrides[i], dtype=int)
                if mask_arr.size == nbelectrodes:
                    discard_mask = mask_arr
                    override_applied = True
            except (IndexError, TypeError, ValueError):
                override_applied = False

        discard_channels_vec.append(discard_mask)
        ch_idx += nbelectrodes

    return coordinates, ied, discard_channels_vec, emg_type
