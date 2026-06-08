"""Adaptive decomposition for MUedit decomposition path."""

from __future__ import annotations

import logging
from typing import Any

import numpy as np
from scipy.signal import find_peaks

from muedit.adapt_decomp.config import Config

logger = logging.getLogger(__name__)


def _extend_signal(emg: np.ndarray, extension_factor: int) -> np.ndarray:
    """Delay-embedding extension. No-op when extension_factor=1."""
    n_samples, n_channels = emg.shape
    output = np.zeros((n_samples, n_channels * extension_factor), dtype=emg.dtype)
    for i in range(extension_factor):
        output[i :, n_channels * i : n_channels * (i + 1)] = emg[: n_samples - i]
    return output


class AdaptiveDecomp:
    """Adaptive decomposition implementation with stateful online learning."""

    def __init__(
        self,
        emg: np.ndarray,
        whitening: np.ndarray,
        sep_vectors: np.ndarray,
        base_centr: np.ndarray,
        spikes_centr: np.ndarray,
        emg_calib: np.ndarray,
        config: Config,
    ) -> None:
        self.config = config
        self.whitening = whitening.astype(np.float32, copy=True)
        self.sep_vectors = sep_vectors.astype(np.float32, copy=True)
        self.base_centr = base_centr.astype(np.float32, copy=True)
        self.spikes_centr = spikes_centr.astype(np.float32, copy=True)
        self.height = self.spikes_centr - (self.spikes_centr - self.base_centr) / 2
        self.n_motor_units = sep_vectors.shape[0]
        self.n_extended = whitening.shape[0]
        self.identity = np.eye(self.n_extended, dtype=np.float32)

        self.emg_extended = _extend_signal(emg.astype(np.float32), config.ex_factor)

        self._init_whitening_calibration(emg_calib.astype(np.float32), config)
        if config.compute_loss:
            self._init_contrast_calibration(emg_calib.astype(np.float32), config)

        logger.info(
            "AdaptiveDecomp initialized: %d motor units, %d extended channels",
            self.n_motor_units,
            self.n_extended,
        )

    def _init_whitening_calibration(
        self, emg_calib: np.ndarray, config: Config
    ) -> None:
        """Initialise whitening covariance from calibration batches and compute KL divergence stats."""
        emg_extended = _extend_signal(emg_calib, config.ex_factor)
        whitened_emg = emg_extended @ self.whitening.T
        self.whitening_covariance = np.cov(whitened_emg.T).astype(np.float32)

        batch_size = config.batch_size
        kl_divs: list[float] = []
        for start_idx in range(0, len(whitened_emg) - batch_size + 1, batch_size):
            batch = whitened_emg[start_idx : start_idx + batch_size]
            self.whitening_covariance = (
                (1 - config.cov_alpha) * self.whitening_covariance
                + config.cov_alpha * np.cov(batch.T).astype(np.float32)
            )
            if config.compute_loss:
                kl = self._kl_divergence()
                if not np.isnan(kl):
                    kl_divs.append(kl)

        if config.compute_loss and kl_divs:
            self.kl_div_calib_mean = float(np.mean(kl_divs))
            self.kl_div_calib_std  = float(np.std(kl_divs)) or 1.0
        else:
            self.kl_div_calib_mean = 0.0
            self.kl_div_calib_std  = 1.0

    def _init_contrast_calibration(
        self, emg_calib: np.ndarray, config: Config
    ) -> None:
        """Compute contrast calibration stats (mean/std per MU) from the calibration segment."""
        emg_extended = _extend_signal(emg_calib, config.ex_factor)
        whitened = self.whitening @ emg_extended.T  # (n_extended, n_calib)

        batch_size = config.batch_size
        n_batches = emg_extended.shape[0] // batch_size
        contrast_values: list[np.ndarray] = []

        for b in range(n_batches):
            start = b * batch_size
            end   = (b + 1) * batch_size
            ipts_batch = (self.sep_vectors @ whitened[:, start:end]).T  # (batch, n_mu)
            ipts_sq    = ipts_batch * np.abs(ipts_batch)
            spikes_batch = self._detect_spikes(ipts_sq, update_centroids=False)
            contrast = self._contrast_value(ipts_batch, spikes_batch)
            contrast_values.append(contrast)

        if contrast_values:
            arr = np.stack(contrast_values, axis=0)  # (n_batches, n_mu)
            self.contrast_calib_mean = np.nanmean(arr, axis=0).astype(np.float32)
            self.contrast_calib_std  = np.nanstd(arr,  axis=0).astype(np.float32)
            self.contrast_calib_std[self.contrast_calib_std == 0] = 1.0
        else:
            self.contrast_calib_mean = np.zeros(self.n_motor_units, dtype=np.float32)
            self.contrast_calib_std  = np.ones(self.n_motor_units,  dtype=np.float32)

    def run(self) -> tuple[np.ndarray, np.ndarray, dict[str, Any]]:
        """Process the full signal and return ipts, spikes, and per-batch losses."""
        n_samples  = self.emg_extended.shape[0]
        batch_size = self.config.batch_size
        n_batches  = n_samples // batch_size
        extension_factor = self.config.ex_factor

        ipts_output   = np.zeros((n_samples, self.n_motor_units), dtype=np.float32)
        spikes_output = np.zeros((n_samples, self.n_motor_units), dtype=np.int32)

        wh_losses:    np.ndarray | None = None
        sv_losses:    np.ndarray | None = None
        total_losses: np.ndarray | None = None
        if self.config.compute_loss:
            wh_losses    = np.full(n_batches, np.nan, dtype=np.float32)
            sv_losses    = np.full((n_batches, self.n_motor_units), np.nan, dtype=np.float32)
            total_losses = np.full(n_batches, np.nan, dtype=np.float32)

        for batch_idx in range(n_batches):
            start_idx = batch_idx * batch_size
            end_idx   = (batch_idx + 1) * batch_size
            skip = extension_factor - 1 if batch_idx == 0 else 0

            whitened_batch = self._whiten(self.emg_extended[start_idx + skip : end_idx])
            ipts_batch     = self._separate(whitened_batch)
            ipts_sq        = ipts_batch * np.abs(ipts_batch)
            spikes_batch   = self._detect_spikes(ipts_sq, update_centroids=True)

            if self.config.compute_loss:
                kl  = self._kl_divergence()
                whl = self._wh_loss(kl)
                svl = self._sv_loss(self._contrast_value(ipts_batch, spikes_batch))
                wh_losses[batch_idx]    = whl
                sv_losses[batch_idx]    = svl
                total_losses[batch_idx] = (0.0 if np.isnan(whl) else whl) + float(np.nansum(svl))

            if self.config.adapt_sv:
                self._update_separation_vectors(whitened_batch, ipts_batch, spikes_batch)

            ipts_output[start_idx + skip : end_idx]   = ipts_batch
            spikes_output[start_idx + skip : end_idx] = spikes_batch

        remainder_samples = n_samples - n_batches * batch_size
        if remainder_samples > 0:
            start_idx = n_batches * batch_size
            ipts_output[start_idx:] = (
                self.sep_vectors @ (self.whitening @ self.emg_extended[start_idx:].T)
            ).T

        losses: dict[str, Any] = {}
        if self.config.compute_loss:
            losses = {
                "wh_loss":    wh_losses,
                "sv_loss":    sv_losses,
                "total_loss": total_losses,
            }

        return ipts_output, spikes_output, losses

    def _whiten(self, emg_batch: np.ndarray) -> np.ndarray:
        """Apply whitening and optionally update the whitening matrix."""
        whitened_signal = self.whitening @ emg_batch.T

        if self.config.adapt_wh or self.config.compute_loss:
            current_covariance = np.cov(whitened_signal).astype(np.float32)
            self.whitening_covariance = (
                (1 - self.config.cov_alpha) * self.whitening_covariance
                + self.config.cov_alpha * current_covariance
            )

        if self.config.adapt_wh:
            self.whitening -= self.config.wh_learning_rate * (
                (self.whitening_covariance - self.identity) @ self.whitening
            )

        return whitened_signal

    def _separate(self, whitened_signal: np.ndarray) -> np.ndarray:
        """Project whitened signal through separation vectors."""
        return (self.sep_vectors @ whitened_signal).T

    def _detect_spikes(
        self, ipts_squared: np.ndarray, update_centroids: bool = True
    ) -> np.ndarray:
        """Spike detection with amplitude-bounded peak finding and midpoint threshold."""
        spikes = np.zeros(ipts_squared.shape, dtype=np.int32)

        for unit_idx in range(self.n_motor_units):
            min_h = float(self.base_centr[unit_idx] / self.config.spike_height_mult)
            max_h = float(self.config.spike_height_mult * self.spikes_centr[unit_idx])
            peak_indices, _ = find_peaks(
                ipts_squared[:, unit_idx],
                distance=self.config.spike_dist,
                height=(min_h, max_h),
            )

            if len(peak_indices) == 0:
                continue

            peak_values = ipts_squared[peak_indices, unit_idx]
            labels = (peak_values > self.height[unit_idx]).astype(np.int32)
            spikes[peak_indices, unit_idx] = labels

            if update_centroids and self.config.adapt_sd:
                n_spikes     = int(labels.sum())
                n_non_spikes = len(peak_indices) - n_spikes
                if n_spikes > 0:
                    spike_centroid = np.mean(peak_values[labels.astype(bool)])
                    self.spikes_centr[unit_idx] = (
                        self.config.spike_prev_weight * self.spikes_centr[unit_idx]
                        + n_spikes * spike_centroid
                    ) / (self.config.spike_prev_weight + n_spikes)

                if n_non_spikes > 0:
                    baseline_centroid = np.mean(peak_values[~labels.astype(bool)])
                    self.base_centr[unit_idx] = (
                        self.config.spike_prev_weight * self.base_centr[unit_idx]
                        + n_non_spikes * baseline_centroid
                    ) / (self.config.spike_prev_weight + n_non_spikes)

                self.height[unit_idx] = self.spikes_centr[unit_idx] - (
                    self.spikes_centr[unit_idx] - self.base_centr[unit_idx]
                ) / 2

        return spikes

    def _kl_divergence(self) -> float:
        """KL divergence between the estimated whitened covariance and the identity."""
        cov  = self.whitening_covariance
        n    = cov.shape[0]
        sign, logdet = np.linalg.slogdet(cov)
        if sign <= 0:
            return np.nan
        return float(0.5 * (-logdet + np.trace(cov) - n))

    def _wh_loss(self, kl_div: float) -> float:
        """Normalised whitening loss (squared z-score against calibration KL divergences)."""
        if np.isnan(kl_div):
            return np.nan
        return float(((kl_div - self.kl_div_calib_mean) / (self.kl_div_calib_std + 1e-12)) ** 2)

    def _contrast_value(
        self, ipts_batch: np.ndarray, spikes_batch: np.ndarray
    ) -> np.ndarray:
        """Mean contrast function value at spike positions, per MU (nan when no spikes)."""
        n_spikes  = spikes_batch.sum(axis=0).astype(float)
        mean_ipts = (ipts_batch * spikes_batch).sum(axis=0) / (n_spikes + 1e-6)
        mean_ipts = mean_ipts.copy()
        mean_ipts[n_spikes == 0] = np.nan
        if self.config.contrast_func == "logcosh":
            return np.log(np.cosh(mean_ipts))
        return mean_ipts ** 3 / 6

    def _sv_loss(self, contrast: np.ndarray) -> np.ndarray:
        """Normalised separation vector loss (squared z-score against calibration contrast)."""
        return ((contrast - self.contrast_calib_mean) / (self.contrast_calib_std + 1e-12)) ** 2

    def _update_separation_vectors(
        self,
        whitened_signal: np.ndarray,
        ipts: np.ndarray,
        spikes: np.ndarray,
    ) -> None:
        """Gradient ascent on contrast function with Gram-Schmidt deflation."""
        if self.config.contrast_func == "logcosh":
            gradients_all = np.tanh(ipts) * spikes
        else:
            gradients_all = (ipts**2 / 2.0) * spikes

        spike_counts = spikes.sum(axis=0)
        gradients = whitened_signal @ gradients_all / np.maximum(spike_counts, 1)

        separation_vectors_new = self.sep_vectors.copy()

        if self.config.sv_epochs == 1:
            active = spike_counts > 0
            if active.any():
                separation_vectors_new[active] += (
                    self.config.sv_learning_rate * gradients[:, active].T
                )
                norms = np.linalg.norm(separation_vectors_new[active], axis=1, keepdims=True)
                separation_vectors_new[active] /= np.where(norms > 1e-8, norms, 1.0)
                for unit_idx in range(1, self.n_motor_units):
                    if not active[unit_idx]:
                        continue
                    separation_vectors_new[unit_idx] -= (
                        separation_vectors_new[:unit_idx].T
                        @ (separation_vectors_new[:unit_idx] @ separation_vectors_new[unit_idx])
                    )
                    norm = np.linalg.norm(separation_vectors_new[unit_idx])
                    if norm > 1e-8:
                        separation_vectors_new[unit_idx] /= norm
                self.sep_vectors[active] = separation_vectors_new[active]
        else:
            for unit_idx in range(self.n_motor_units):
                if spike_counts[unit_idx] == 0:
                    continue

                for epoch in range(self.config.sv_epochs):
                    separation_vectors_new[unit_idx] += (
                        self.config.sv_learning_rate * gradients[:, unit_idx]
                    )
                    norm = np.linalg.norm(separation_vectors_new[unit_idx])
                    if norm > 1e-8:
                        separation_vectors_new[unit_idx] /= norm

                    convergence_limit = 1.0 - abs(np.dot(
                        separation_vectors_new[unit_idx],
                        self.sep_vectors[unit_idx],
                    ))
                    self.sep_vectors[unit_idx] = separation_vectors_new[unit_idx].copy()

                    if (
                        convergence_limit < self.config.sv_tol
                        or epoch == self.config.sv_epochs - 1
                    ):
                        if unit_idx > 0:
                            separation_vectors_new[unit_idx] -= (
                                separation_vectors_new[:unit_idx].T
                                @ (separation_vectors_new[:unit_idx] @ separation_vectors_new[unit_idx])
                            )
                            norm = np.linalg.norm(separation_vectors_new[unit_idx])
                            if norm > 1e-8:
                                separation_vectors_new[unit_idx] /= norm
                        self.sep_vectors[unit_idx] = separation_vectors_new[unit_idx]
                        break


def run_adaptive_decomposition(
    emg: np.ndarray,
    whitening: np.ndarray,
    sep_vectors: np.ndarray,
    base_centr: np.ndarray,
    spikes_centr: np.ndarray,
    emg_calib: np.ndarray,
    config: Config,
) -> tuple[np.ndarray, np.ndarray, dict[str, Any]]:
    """Functional entry point for adaptive decomposition."""
    model = AdaptiveDecomp(
        emg=emg,
        whitening=whitening,
        sep_vectors=sep_vectors,
        base_centr=base_centr,
        spikes_centr=spikes_centr,
        emg_calib=emg_calib,
        config=config,
    )
    return model.run()
