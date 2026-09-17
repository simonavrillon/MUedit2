"""Validation tests for the decomposition pipeline preprocessing steps.

Two preprocessing stages are covered, both exercised on real HD-EMG data
loaded from ``data/Novecento.otb4`` (6x HD08MM1305 grids, 384 channels,
2000 Hz):

1. **Filtering** — :func:`muedit.signal.filters.bandpass_signals` and
   :func:`muedit.signal.filters.notch_signals`, the two operations applied
   per grid in :func:`muedit.decomp.preprocess.preprocess_step` before
   decomposition.
2. **Convolutive sphering** — the ``extend_signal`` → ``pca_extended_signal``
   → ``whiten_extended_signal`` chain used in :func:`muedit.decomp.core.decompose_step`
   to whiten the delay-embedded signal prior to FastICA.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pytest

from muedit.decomp.algorithm import pca_extended_signal, whiten_extended_signal
from muedit.signal.decomp_primitives import extend_signal
from muedit.signal.filters import bandpass_signals, notch_signals

# Band edges for the surface-EMG bandpass (emg_type=1), used to partition the
# spectrum when checking real-data filtering.
_BP_LOW, _BP_HIGH = 20.0, 500.0


# ---------------------------------------------------------------------------
# 1. Filtering
# ---------------------------------------------------------------------------


class TestFiltering:
    """Validate the bandpass + notch filters applied in the preprocess step."""

    def test_bandpass_on_real_emg(self, novecento_emg: dict[str, Any]) -> None:
        """Surface bandpass (20-500 Hz) on real Novecento EMG.

        Asserts the bandpass: (a) preserves the (channels, samples) layout,
        (b) removes per-channel DC, (c) redistributes spectral energy so the
        in-band (20-500 Hz) share of total power rises while the out-of-band
        share drops, and (d) leaves a signal with comparable in-band variance
        — i.e. the EMG content survives the filter.
        """
        data = novecento_emg["data"]
        fsamp = novecento_emg["fsamp"]  # 2000 Hz

        # Use the first grid (64 channels) to keep the test light.
        grid0 = data[:64, :].copy()

        filtered = bandpass_signals(grid0, fsamp, emg_type=1)

        # (a) Layout preserved.
        assert filtered.shape == grid0.shape
        assert np.isfinite(filtered).all()

        # (b) Per-channel DC removed (high-pass edge).
        per_channel_mean = np.mean(filtered, axis=1)
        assert np.max(np.abs(per_channel_mean)) < 1e-3, (
            f"max |mean| after bandpass = {np.max(np.abs(per_channel_mean)):.2e}"
        )
        # The raw signal has non-trivial DC offsets to remove.
        assert np.max(np.abs(np.mean(grid0, axis=1))) > 1e-3

        # (c) Spectral energy redistribution across all channels.
        freqs = np.fft.rfftfreq(grid0.shape[1], 1.0 / fsamp)
        in_band = (freqs >= _BP_LOW) & (freqs <= _BP_HIGH)

        def _band_powers(sig: np.ndarray) -> tuple[float, float]:
            spec = np.abs(np.fft.rfft(sig, axis=1)) ** 2
            return spec[:, in_band].sum(), spec[:, ~in_band].sum()

        in_raw, out_raw = _band_powers(grid0)
        in_filt, out_filt = _band_powers(filtered)

        in_ratio_raw = in_raw / (in_raw + out_raw)
        in_ratio_filt = in_filt / (in_filt + out_filt)
        assert in_ratio_filt > in_ratio_raw, (
            f"in-band power ratio should increase: {in_ratio_raw:.3f} -> {in_ratio_filt:.3f}"
        )
        # The out-of-band energy is attenuated.
        assert out_filt < out_raw * 0.5, (
            f"out-of-band power should drop by >50%: {out_raw:.2e} -> {out_filt:.2e}"
        )

        # (d) In-band variance is preserved within an order of magnitude —
        # the filter should not destroy the EMG signal.
        var_raw = np.var(grid0)
        var_filt = np.var(filtered)
        assert var_filt > 0.1 * var_raw, (
            f"filtered variance too small vs raw: {var_filt:.2e} vs {var_raw:.2e}"
        )

    def test_notch_on_real_emg(self, novecento_emg: dict[str, Any]) -> None:
        """FFT-based notch on real Novecento EMG.

        Asserts the notch: (a) preserves layout, (b) returns a real-valued,
        finite signal, and (c) does not inflate total spectral energy (it can
        only remove, not add).  The notch is data-driven (median+5·std per
        ~50 Hz spectral chunk) so it targets whatever narrowband interference is
        present rather than a fixed mains frequency.
        """
        data = novecento_emg["data"]
        fsamp = novecento_emg["fsamp"]

        grid0 = data[:64, :].copy()
        filtered = notch_signals(grid0, fsamp)

        # (a) Layout preserved.
        assert filtered.shape == grid0.shape

        # (b) Real and finite.
        assert np.isreal(filtered).all()
        assert np.isfinite(filtered).all()

        # (c) Total energy does not increase — the notch only zeros bins.
        energy_in = np.sum(grid0**2)
        energy_out = np.sum(filtered**2)
        assert energy_out <= energy_in * (1 + 1e-9), (
            f"notch must not add energy: {energy_out:.4e} > {energy_in:.4e}"
        )

        # At least *some* energy is removed (real recordings carry residual
        # narrowband interference the notch catches).
        assert energy_out < energy_in, "notch should remove at least some energy"


# ---------------------------------------------------------------------------
# 2. Convolutive sphering
# ---------------------------------------------------------------------------


class TestConvolutiveSphering:
    """Validate extend -> PCA -> whiten, the convolutive-sphering stage."""

    @pytest.fixture()
    def grid0_window(self, novecento_emg: dict[str, Any]) -> np.ndarray:
        """First grid (64 channels), 10 s of samples -- enough for stable PCA."""
        data = novecento_emg["data"]
        fsamp = novecento_emg["fsamp"]
        n = int(10.0 * fsamp)
        return data[:64, :n].copy()

    def test_extend_signal_on_real_emg(self, grid0_window: np.ndarray) -> None:
        """Offline extension of real EMG: shape and delay structure.

        Verifies the (channels*exfactor, samples+exfactor-1) layout and that
        block m is the input delayed by m samples -- the convolutive embedding
        that gives the sphering its name.
        """
        n_ch, n_samples = grid0_window.shape
        exfactor = 4

        extended = extend_signal(grid0_window, exfactor, samples_first=False)

        expected_rows = n_ch * exfactor
        expected_cols = n_samples + exfactor - 1
        assert extended.shape == (expected_rows, expected_cols)
        assert extended.dtype == np.float64

        # Block 0 is the unshifted signal.
        np.testing.assert_allclose(extended[0:n_ch, 0:n_samples], grid0_window)
        # Block 1 is delayed by one sample; leading column is zero.
        np.testing.assert_allclose(extended[n_ch : 2 * n_ch, 1 : 1 + n_samples], grid0_window)
        assert np.all(extended[n_ch : 2 * n_ch, 0] == 0.0)

    def test_whitening_on_real_emg_decorrelates(self, grid0_window: np.ndarray) -> None:
        """Convolutive sphering on real EMG: whitened covariance is a projector.

        PCA drops low-energy components, so the whitened covariance is an
        idempotent projector with unit eigenvalues on the retained subspace
        (decorrelated, unit-variance components) and zero elsewhere.
        """
        exfactor = 3
        extended = extend_signal(grid0_window, exfactor, samples_first=False)
        eigvecs, eigvals = pca_extended_signal(extended)
        whitened, _ = whiten_extended_signal(extended, eigvecs, eigvals)

        n_comp = eigvecs.shape[1]
        cov_whitened = np.cov(whitened, bias=True)

        # Symmetric by construction.
        np.testing.assert_allclose(cov_whitened, cov_whitened.T, atol=1e-10)

        # Idempotent -> projector onto the retained subspace.
        np.testing.assert_allclose(
            cov_whitened @ cov_whitened,
            cov_whitened,
            atol=1e-4,
            err_msg="whitened covariance must be idempotent (a projector)",
        )

        # n_comp eigenvalues ~1 (sphered), rest ~0.  Tolerance is looser than
        # the synthetic case because real EMG has a broad eigenvalue spectrum.
        eigvals_w = np.linalg.eigvalsh(cov_whitened)[::-1]
        np.testing.assert_allclose(eigvals_w[:n_comp], 1.0, atol=1e-3)
        np.testing.assert_allclose(eigvals_w[n_comp:], 0.0, atol=1e-4)
