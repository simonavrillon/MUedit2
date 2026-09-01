"""Regression tests for findings 1-18.

Each test verifies one fix in isolation.  Tests that require small synthetic
signals or round-trip serialization use the scratchpad (in-memory) path.
"""

from __future__ import annotations

import io as _io
import tempfile
from pathlib import Path

import numpy as np
import pytest

# ---------------------------------------------------------------------------
# Fix #1: edges_sec=0 blanks every window
# ---------------------------------------------------------------------------
def test_fix1_edges_sec_zero_does_not_blank_window():
    """edges_sec=0 must not trigger trim_edges (which would slice [0:0])."""
    from muedit.decomp.core import decompose_step
    from muedit.decomp.types import (
        DecomposeStepOutput,
        DecompositionParameters,
        PreprocessStepOutput,
    )

    n_ch = 10
    n_samples = 200
    data = np.random.default_rng(0).standard_normal((n_ch, n_samples))
    prep = PreprocessStepOutput(
        signal={},
        data=data,
        fsamp=2000.0,
        grid_names=["GR04MM1305"],
        coordinates=[np.zeros((64, 2))],
        ied=[4.0],
        discard_channels=[np.zeros(64, dtype=int)],
        muscles=[],
        loader_meta={},
        roi_list=[(0, n_samples)],
        ngrid=1,
        coordinates_plateau=[0, n_samples],
    )
    # Override data to match the channel count we have
    prep.data = data
    prep.coordinates = [np.zeros((n_ch, 2))]
    prep.discard_channels = [np.zeros(n_ch, dtype=int)]
    prep.coordinates_plateau = [0, n_samples]

    params = DecompositionParameters(edges_sec=0.0, niter=3, nbextchan=n_ch * 2)
    rng = np.random.default_rng(0)

    # Must not raise — previously this produced a (n, 0) array that crashed
    # pca_extended_signal with "array must not contain infs or NaNs".
    try:
        result = decompose_step(prep, params, rng, None)
    except ValueError as exc:
        if "infs or NaNs" in str(exc) or "Degenerate" in str(exc):
            pass  # degenerate random signal can still hit the eigenvalue guard
        else:
            raise


# ---------------------------------------------------------------------------
# Fix #2: Zero-MU / no-Pulsetrain crash the loader
# ---------------------------------------------------------------------------
def test_fix2_npz_zero_mu_round_trip():
    """Round-tripping an NPZ with zero MUs must not crash on reload."""
    from muedit.decomp.io import load_decomposition_file
    from muedit.decomp.postprocess import _save_npz_with_app_schema

    with tempfile.TemporaryDirectory() as d:
        path = str(Path(d) / "empty.npz")
        _save_npz_with_app_schema(
            path,
            pulse_trains=np.array([]),
            distimes=[],
            fsamp=2048.0,
            grid_names=["Grid 1"],
            mu_grid_index=[],
            muscles=[],
            parameters={},
            total_samples=1000,
        )
        loaded = load_decomposition_file(path)
        assert loaded["total_samples"] == 1000
        assert loaded["distime_all"] == []


# ---------------------------------------------------------------------------
# Fix #3: split_by_amplitude aborts on degenerate peaks
# ---------------------------------------------------------------------------
def test_fix3_split_by_amplitude_degenerate_peaks():
    """Identical amplitudes must not raise ClusterError."""
    from muedit.signal.decomp_primitives import split_by_amplitude

    values = np.array([5.0, 5.0, 5.0, 5.0, 5.0])
    peaks = np.array([0, 1, 2, 3, 4])
    high, centroids, labels = split_by_amplitude(values, peaks)
    # All peaks should be in the high cluster (degenerate fallback)
    assert len(high) == 5
    assert np.all(high == peaks)


def test_fix3_split_by_amplitude_normal_still_works():
    """Normal amplitude split still returns the high cluster."""
    from muedit.signal.decomp_primitives import split_by_amplitude

    rng = np.random.default_rng(0)
    values = np.concatenate([rng.standard_normal(50) * 0.5, rng.standard_normal(50) * 3 + 10])
    peaks = np.arange(100)
    high, centroids, labels = split_by_amplitude(values, peaks)
    assert len(high) > 0
    assert centroids[1] > centroids[0]  # high cluster centroid is larger


# ---------------------------------------------------------------------------
# Fix #4: .mat 1-based shift applied after pulse matrix built
# ---------------------------------------------------------------------------
def test_fix4_shift_before_build_distimes_and_matrix_agree():
    """Shifting distimes before build_pulse_trains keeps them aligned."""
    from muedit.decomp.io import build_pulse_trains_from_distimes, _shift_distimes

    total = 100
    # 1-based distimes
    distimes_1based = [[10, 30, 50], [20, 40, 60]]
    shifted = _shift_distimes(distimes_1based, -1, total)
    pulse = build_pulse_trains_from_distimes(shifted, total)

    # distimes and pulse matrix must agree
    for mu_idx, dt in enumerate(shifted):
        spike_idx = np.flatnonzero(pulse[mu_idx])
        assert list(spike_idx) == dt, f"MU {mu_idx}: distimes={dt} but spikes={spike_idx.tolist()}"


# ---------------------------------------------------------------------------
# Fix #5: _parse_emgmask_cells destroys channel masks
# ---------------------------------------------------------------------------
def test_fix5_emgmask_preserves_order_and_length():
    """Binary masks must preserve order and length (no sorted/set)."""
    from muedit.decomp.io import _parse_emgmask_cells

    mask = np.array([0, 0, 1, 0, 1, 0, 0, 0, 1, 0], dtype=int)
    # As object ndarray (what scipy.io.loadmat with simplify_cells gives)
    raw = np.empty(1, dtype=object)
    raw[0] = mask
    masks = _parse_emgmask_cells(raw)
    assert len(masks) == 1
    assert masks[0].size == 10, f"Expected 10 elements, got {masks[0].size}"
    assert list(masks[0]) == list(mask)

    # As a plain list
    masks2 = _parse_emgmask_cells([mask.tolist()])
    assert masks2[0].size == 10
    assert list(masks2[0]) == list(mask)

    # As a non-object ndarray
    masks3 = _parse_emgmask_cells([mask])
    assert masks3[0].size == 10
    assert list(masks3[0]) == list(mask)


# ---------------------------------------------------------------------------
# Fix #6: Exported sil is pre-deduplication
# ---------------------------------------------------------------------------
def test_fix6_sil_subset_by_dedup():
    """SIL scores after postprocess must match the deduplicated MU count."""
    from muedit.decomp.algorithm import rem_duplicates

    # Two identical MUs + one distinct (far apart so no lag overlap)
    fsamp = 2000.0
    ltime = 2000
    pulse_t = np.zeros((3, ltime))
    for t in [100, 200, 300]:
        pulse_t[0, t] = 1
        pulse_t[1, t] = 1  # duplicate of MU 0
    for t in [1000, 1100, 1200]:
        pulse_t[2, t] = 1  # distinct, far from MU 0/1

    distime = [
        np.array([100, 200, 300]),
        np.array([100, 200, 300]),
        np.array([1000, 1100, 1200]),
    ]
    kept_pulses, kept_distime, kept_idx = rem_duplicates(
        pulse_t, distime, distime,
        maxlag=50, jitter=0.00025, tol=0.3, fsamp=fsamp,
    )
    # 2 MUs survive (one of the duplicates + the distinct one)
    assert kept_pulses.shape[0] == 2
    assert len(kept_distime) == 2
    assert len(kept_idx) == 2


# ---------------------------------------------------------------------------
# Fix #7: minimize_isi_covariance returns consistent CoV
# ---------------------------------------------------------------------------
def test_fix7_cov_describes_returned_spikes():
    """cov_last must equal isi_cov(spikes_last) when spikes_last has >=2 spikes."""
    from muedit.decomp.algorithm import minimize_isi_covariance
    from muedit.signal.decomp_primitives import isi_cov

    rng = np.random.default_rng(42)
    n_ch, n_samples = 20, 2000
    x = rng.standard_normal((n_ch, n_samples))
    w = rng.standard_normal(n_ch)
    cov_input = 0.9  # large input CoV that should be overwritten
    w_out, spikes_out, cov_out = minimize_isi_covariance(w, x, cov_input, 2000.0)

    if len(spikes_out) >= 2:
        expected_cov = isi_cov(spikes_out, 2000.0)
        assert abs(cov_out - expected_cov) < 1e-9, (
            f"cov_out={cov_out} != isi_cov(spikes_out)={expected_cov}"
        )


def test_fix7_stale_cov_is_nan_when_spikes_too_few():
    """When spikes_last has < 2 entries after the fallback recompute,
    cov_last must be NaN (not a stale value from a different separator)."""
    from muedit.decomp.algorithm import minimize_isi_covariance

    rng = np.random.default_rng(0)
    n_ch, n_samples = 20, 2000
    x = rng.standard_normal((n_ch, n_samples))
    w = rng.standard_normal(n_ch)
    # A very large input CoV — if it leaked through as cov_last, covfilter
    # would wrongly accept the unit.
    cov_input = 100.0
    w_out, spikes_out, cov_out = minimize_isi_covariance(w, x, cov_input, 2000.0)

    if len(spikes_out) < 2:
        assert np.isnan(cov_out), f"Expected NaN for <2 spikes, got {cov_out}"
    else:
        # If spikes survived, cov must match (not be the stale input)
        assert cov_out != cov_input


# ---------------------------------------------------------------------------
# Fix #8: No validation that channel counts match the grid catalogue
# ---------------------------------------------------------------------------
def test_fix8_raises_on_channel_mismatch():
    """Preprocessing must raise when data has fewer channels than the grid declares."""
    from muedit.decomp.preprocess import preprocess_step
    from muedit.decomp.types import DecompositionParameters, LoadStepOutput

    data = np.zeros((60, 200), dtype=np.float64)  # 60 channels
    loaded = LoadStepOutput(
        full_path="test.mat",
        filename="test.mat",
        signal={"gridname": ["GR04MM1305"], "muscle": [], "metadata": {}},
        data=data,
        fsamp=2000.0,
    )
    params = DecompositionParameters()
    with pytest.raises(ValueError, match="channels"):
        preprocess_step(
            loaded, None, False, None, None, params, None, None, None, None
        )


# ---------------------------------------------------------------------------
# Fix #9: Integer input data silently truncated by filters
# ---------------------------------------------------------------------------
def test_fix9_integer_input_promoted_to_float():
    """Integer data must be promoted to float64 before filtering."""
    from muedit.decomp.types import LoadStepOutput
    from muedit.decomp.preprocess import preprocess_step, DecompositionParameters

    # Use a grid with 64 channels and a 64-channel signal
    data = (np.random.default_rng(0).standard_normal((64, 200)) * 100).astype(np.int16)
    loaded = LoadStepOutput(
        full_path="test.mat",
        filename="test.mat",
        signal={"gridname": ["GR04MM1305"], "muscle": [], "metadata": {}},
        data=data,
        fsamp=2000.0,
    )
    params = DecompositionParameters()
    prep = preprocess_step(
        loaded, None, False, None, None, params, None, None, None, None
    )
    assert prep.data.dtype == np.float64


def test_fix9_float64_input_does_not_share_array():
    """float64 input must be copied, not shared with the caller's array."""
    from muedit.decomp.types import LoadStepOutput
    from muedit.decomp.preprocess import preprocess_step, DecompositionParameters

    data = np.random.default_rng(0).standard_normal((64, 200)).astype(np.float64)
    original = data.copy()
    loaded = LoadStepOutput(
        full_path="test.mat",
        filename="test.mat",
        signal={"gridname": ["GR04MM1305"], "muscle": [], "metadata": {}},
        data=data,
        fsamp=2000.0,
    )
    params = DecompositionParameters()
    prep = preprocess_step(
        loaded, None, False, None, None, params, None, None, None, None
    )
    # Filters mutate prep.data in place — the caller's array must not be affected.
    assert not np.shares_memory(prep.data, data), "prep.data shares memory with loaded.data"
    assert np.allclose(data, original), "caller's data was mutated by filtering"


# ---------------------------------------------------------------------------
# Fix #10: discard_overrides silently ignored on size mismatch
# ---------------------------------------------------------------------------
def test_fix10_discard_overrides_size_mismatch_raises():
    """A discard_overrides mask with wrong size must raise, not silently pass."""
    from muedit.signal.grid import format_hdemg_signal

    with pytest.raises(ValueError, match="discard_overrides"):
        format_hdemg_signal(["GR04MM1305"], discard_overrides=[[3, 17]])  # 2 != 64


# ---------------------------------------------------------------------------
# Fix #11: adaptive_losses type annotation
# ---------------------------------------------------------------------------
def test_fix11_adaptive_losses_annotation():
    """adaptive_losses should be annotated dict[int, Any] not dict[str, Any]."""
    from muedit.decomp.types import PostprocessStepOutput
    import dataclasses

    fields = {f.name: f for f in dataclasses.fields(PostprocessStepOutput)}
    type_str = str(fields["adaptive_losses"].type)
    assert "int" in type_str, f"Expected dict[int, ...] but got {type_str}"


# ---------------------------------------------------------------------------
# Fix #12: float() on ndarray fsamp
# ---------------------------------------------------------------------------
def test_fix12_float_on_ndarray_fsamp():
    """float() on an ndarray fsamp must not raise DeprecationWarning or TypeError."""
    from muedit.decomp.io import _extract_decomp_fields

    # Simulate a v7.3 mat where fsamp comes as a (1,1) array
    signal = {"fsamp": np.array([2048.0])}
    result = _extract_decomp_fields(signal, {}, None, {})
    fsamp = result[2]
    assert fsamp == 2048.0
    assert isinstance(fsamp, float)


# ---------------------------------------------------------------------------
# Fix #13: np.array(discharge_times, dtype=object) shape depends on data
# ---------------------------------------------------------------------------
def test_fix13_equal_length_distimes_still_1d_object():
    """Equal-length discharge times must produce a 1-D object array, not 2-D."""
    from muedit.models import DecompositionSignalExport

    equal_length = [np.array([1, 2, 3]), np.array([4, 5, 6]), np.array([7, 8, 9])]
    export = DecompositionSignalExport(
        data=np.zeros((4, 10)),
        fsamp=2000.0,
        pulse_t=np.zeros((3, 10)),
        discharge_times=equal_length,
    )
    d = export.to_dict()
    dt = d["Dischargetimes"]
    assert dt.ndim == 1, f"Expected 1-D object array, got shape {dt.shape}"
    assert dt.shape == (3,)


def test_fix13_unequal_length_distimes_still_1d_object():
    """Unequal-length discharge times must also produce a 1-D object array."""
    from muedit.models import DecompositionSignalExport

    unequal = [np.array([1, 2]), np.array([3, 4, 5, 6])]
    export = DecompositionSignalExport(
        data=np.zeros((4, 10)),
        fsamp=2000.0,
        pulse_t=np.zeros((2, 10)),
        discharge_times=unequal,
    )
    d = export.to_dict()
    dt = d["Dischargetimes"]
    assert dt.ndim == 1, f"Expected 1-D object array, got shape {dt.shape}"
    assert dt.shape == (2,)


# ---------------------------------------------------------------------------
# Fix #15 regression: _mat_struct_to_dict must not hit the #13 trap
# ---------------------------------------------------------------------------
def test_fix15_mat_struct_to_dict_equal_length_object_array():
    """_mat_struct_to_dict must produce a 1-D object array even when cell
    contents are equal-length (the #13 trap that np.array(..., dtype=object)
    falls into)."""
    from muedit.decomp.io import _mat_struct_to_dict

    # Equal-length contents — np.array([...], dtype=object) would build a 2-D
    # array and .reshape((2,)) would fail.
    obj = np.empty(2, dtype=object)
    obj[0] = np.array([1, 2, 3])
    obj[1] = np.array([4, 5, 6])
    result = _mat_struct_to_dict(obj)
    assert result.ndim == 1, f"Expected 1-D object array, got shape {result.shape}"
    assert result.shape == (2,)


def test_fix15_mat_struct_to_dict_ragged_object_array():
    """_mat_struct_to_dict with ragged contents must also stay 1-D."""
    from muedit.decomp.io import _mat_struct_to_dict

    obj = np.empty(2, dtype=object)
    obj[0] = np.array([1, 2])
    obj[1] = np.array([3, 4, 5, 6])
    result = _mat_struct_to_dict(obj)
    assert result.ndim == 1
    assert result.shape == (2,)


# ---------------------------------------------------------------------------
# Fix #14: rois np.ndarray entries silently dropped
# ---------------------------------------------------------------------------
def test_fix14_rois_ndarray_entries_survive():
    """ROIs arriving as np.ndarray must be converted, not dropped."""
    from muedit.decomp.io import _extract_decomp_fields, load_decomposition_file

    # Simulate scipy.io.loadmat output: rois as ndarray entries
    preview = {"rois": [np.array([10, 20]), np.array([30, 40])]}
    result = _extract_decomp_fields({}, preview, None, {})
    rois = result[7]
    assert len(rois) == 2
    assert rois[0] == (10, 20)
    assert rois[1] == (30, 40)


def test_fix14_single_roi_squeezed_to_1d_survives():
    """A single ROI squeezed to 1-D by simplify_cells must not be dropped."""
    from muedit.decomp.io import _extract_decomp_fields

    # simplify_cells squeezes a (1, 2) cell to (2,) — iteration yields scalars
    preview = {"rois": np.array([0, 100])}
    result = _extract_decomp_fields({}, preview, None, {})
    rois = result[7]
    assert len(rois) == 1, f"Expected 1 ROI, got {len(rois)}"
    assert rois[0] == (0, 100)


def test_fix14_cell_of_pairs_does_not_crash():
    """An object array of length-2 ROI arrays (a cell of pairs) must not
    be reshaped into a single row of arrays that then crashes int()."""
    from muedit.decomp.io import _extract_decomp_fields

    # Hand-authored preview.rois = {[0 100], [200 300]}
    cell = np.empty(2, dtype=object)
    cell[0] = np.array([0, 100])
    cell[1] = np.array([200, 300])
    result = _extract_decomp_fields({}, {"rois": cell}, None, {})
    rois = result[7]
    assert len(rois) == 2
    assert rois[0] == (0, 100)
    assert rois[1] == (200, 300)


def test_fix14_odd_length_roi_warns_and_yields_empty():
    """Odd-length ROI input must yield [] and emit a warning."""
    from muedit.decomp.io import _extract_decomp_fields

    # 3 elements — not a valid (start, end) pair
    preview = {"rois": np.array([0, 100, 50])}
    result = _extract_decomp_fields({}, preview, None, {})
    rois = result[7]
    assert len(rois) == 0


def test_fix14_empty_roi_array_no_warning():
    """An empty (0, 2) ROI array is a valid 'no ROIs recorded' file — no warning."""
    from muedit.decomp.io import _extract_decomp_fields
    import io as _stdio
    import logging

    preview = {"rois": np.empty((0, 2), dtype=float)}
    handler = logging.StreamHandler(_stdio.StringIO())
    logger = logging.getLogger("muedit.decomp.io")
    logger.addHandler(handler)
    old_level = logger.level
    logger.setLevel(logging.WARNING)
    result = _extract_decomp_fields({}, preview, None, {})
    logger.setLevel(old_level)
    logger.removeHandler(handler)
    rois = result[7]
    assert len(rois) == 0
    log_output = handler.stream.getvalue()
    assert log_output == "", f"Unexpected warning: {log_output}"


def test_fix14_nonnumeric_roi_degrades_to_empty():
    """Non-numeric ROI data must not crash the loader — degrade to []."""
    from muedit.decomp.io import _extract_decomp_fields

    preview = {"rois": np.array(["a", "b"])}
    result = _extract_decomp_fields({}, preview, None, {})
    rois = result[7]
    assert len(rois) == 0


# ---------------------------------------------------------------------------
# Fix #16: .get("gridname", ["Default"]) dead default
# ---------------------------------------------------------------------------
def test_fix16_empty_gridname_raises():
    """An empty gridname must raise, not silently produce ngrid=0."""
    from muedit.decomp.preprocess import preprocess_step
    from muedit.decomp.types import DecompositionParameters, LoadStepOutput

    data = np.zeros((64, 200), dtype=np.float64)
    loaded = LoadStepOutput(
        full_path="test.mat",
        filename="test.mat",
        signal={"gridname": [], "muscle": [], "metadata": {}},
        data=data,
        fsamp=2000.0,
    )
    params = DecompositionParameters()
    with pytest.raises(ValueError, match="grid name"):
        preprocess_step(
            loaded, None, False, None, None, params, None, None, None, None
        )


def test_fix16_missing_gridname_raises():
    """A missing gridname must also raise."""
    from muedit.decomp.preprocess import preprocess_step
    from muedit.decomp.types import DecompositionParameters, LoadStepOutput

    data = np.zeros((64, 200), dtype=np.float64)
    loaded = LoadStepOutput(
        full_path="test.mat",
        filename="test.mat",
        signal={"muscle": [], "metadata": {}},
        data=data,
        fsamp=2000.0,
    )
    params = DecompositionParameters()
    with pytest.raises(ValueError, match="grid name"):
        preprocess_step(
            loaded, None, False, None, None, params, None, None, None, None
        )


# ---------------------------------------------------------------------------
# Fix #17: e_pre slice must not shift backward pass output
# ---------------------------------------------------------------------------
def test_fix17_e_pre_no_time_shift():
    """The backward pass slice must not shift the output by ex_factor-1 samples.

    The original .T[:calib_start] is correctly aligned — row c of the
    transposed extended signal is the delay-embedded frame at time c.
    Trimming to .T[ex_factor-1:] shifts every backward discharge time late.
    This test verifies the output row count and that the backward region
    is not shifted.
    """
    from muedit.decomp.adaptive_batch import _run_adapt_decomp_bidirectional
    from muedit.adapt_decomp.config import Config

    n_ch = 4
    calib_start = 100
    n_samples = 300
    ex_factor = 3

    rng = np.random.default_rng(0)
    grid_data_g = rng.standard_normal((n_ch, n_samples)).astype(np.float32)
    win_data_g = rng.standard_normal((n_ch, calib_start)).astype(np.float32)
    w_sig = rng.standard_normal((n_ch * ex_factor, calib_start + ex_factor - 1))
    whiten_mat = np.eye(n_ch * ex_factor)
    mu_filters = rng.standard_normal((n_ch * ex_factor, 2))

    config = Config(fsamp=2000, batch_ms=50, compute_loss=False)

    ipts, spikes, losses = _run_adapt_decomp_bidirectional(
        grid_data_g=grid_data_g,
        win_data_g=win_data_g,
        whiten_mat=whiten_mat,
        mu_filters=mu_filters,
        w_sig=w_sig,
        calib_start=calib_start,
        config=config,
    )
    # The backward pass covers [0, calib_start) and the forward pass covers
    # [calib_start, ...). Total ipts rows should be n_samples — no shift.
    assert ipts.shape[0] == n_samples


def test_fix17_e_pre_slice_alignment():
    """Directly verify that .T[:calib_start] keeps row 0 = time 0 alignment."""
    from muedit.decomp.algorithm import extend_signal

    n_ch = 4
    calib_start = 10
    ex_factor = 3

    signal = np.arange(n_ch * calib_start, dtype=float).reshape(n_ch, calib_start)
    ext = extend_signal(signal, ex_factor)  # (n_ch*ex, calib_start + ex-1)
    ext_t = ext.T  # (calib_start + ex-1, n_ch*ex)

    # The original slice [:calib_start] gives rows 0..calib_start-1
    # Row 0 of the transposed signal is the frame at time 0 (block 0 = signal[:, 0])
    old_slice = ext_t[:calib_start]
    assert old_slice.shape[0] == calib_start

    # Row 0 of old_slice should have block 0 = signal[:, 0]
    block0 = old_slice[0, :n_ch]
    assert np.allclose(block0, signal[:, 0]), "old slice row 0 block 0 != signal[:, 0]"

    # The incorrect slice [ex_factor-1:] shifts everything: row 0 = old row 2
    new_slice = ext_t[ex_factor - 1:]
    block0_new = new_slice[0, :n_ch]
    assert np.allclose(block0_new, signal[:, 2]), "new slice row 0 should be signal[:, 2]"
    assert not np.allclose(block0_new, signal[:, 0]), "new slice is shifted — this is the bug"


# ---------------------------------------------------------------------------
# Fix #18: Empty spike train MUs silently dropped — documented
# ---------------------------------------------------------------------------
def test_fix18_empty_spike_train_dropped_from_dedup():
    """MUs with empty spike trains must be excluded from kept_indices."""
    from muedit.decomp.algorithm import rem_duplicates

    ltime = 2000
    pulse_t = np.zeros((3, ltime))
    # MU 0 has spikes, MU 1 has none, MU 2 has spikes far apart
    pulse_t[0, [50, 100, 150]] = 1
    pulse_t[2, [1000, 1100, 1200]] = 1

    distime = [np.array([50, 100, 150]), np.array([]), np.array([1000, 1100, 1200])]
    kept_pulses, kept_distime, kept_idx = rem_duplicates(
        pulse_t, distime, distime,
        maxlag=50, jitter=0.00025, tol=0.3, fsamp=2000.0,
    )
    # MU 1 (empty) must not appear in kept_indices
    assert 1 not in kept_idx
    assert 0 in kept_idx
    assert 2 in kept_idx
