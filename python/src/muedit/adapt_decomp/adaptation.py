"""Adaptive decomposition for MUedit decomposition path."""

from __future__ import annotations

import logging

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
        self.n_motor_units = sep_vectors.shape[0]
        self.n_extended = whitening.shape[0]
        self.identity = np.eye(self.n_extended, dtype=np.float32)

        self.emg_extended = _extend_signal(emg.astype(np.float32), config.ex_factor)

        self._init_whitening_calibration(emg_calib.astype(np.float32), config)

        logger.info(
            "AdaptiveDecomp initialized: %d motor units, %d extended channels",
            self.n_motor_units,
            self.n_extended,
        )

    def _init_whitening_calibration(
        self, emg_calib: np.ndarray, config: Config
    ) -> None:
        """Initialise whitening covariance from calibration batches."""
        emg_extended = _extend_signal(emg_calib, config.ex_factor)
        whitened_emg = emg_extended @ self.whitening.T
        self.whitening_covariance = np.cov(whitened_emg.T).astype(np.float32)

        batch_size = config.batch_size
        for start_idx in range(0, len(whitened_emg) - batch_size + 1, batch_size):
            batch = whitened_emg[start_idx : start_idx + batch_size]
            self.whitening_covariance = (
                (1 - config.cov_alpha) * self.whitening_covariance
                + config.cov_alpha * np.cov(batch.T).astype(np.float32)
            )

    def run(self) -> tuple[np.ndarray, np.ndarray]:
        """Process the full signal and return ipts and spikes."""
        n_samples = self.emg_extended.shape[0]
        batch_size = self.config.batch_size
        n_batches = n_samples // batch_size
        extension_factor = self.config.ex_factor

        ipts_output = np.zeros((n_samples, self.n_motor_units), dtype=np.float32)
        spikes_output = np.zeros((n_samples, self.n_motor_units), dtype=np.int32)

        for batch_idx in range(n_batches):
            start_idx = batch_idx * batch_size
            end_idx = (batch_idx + 1) * batch_size
            skip = extension_factor - 1 if batch_idx == 0 else 0

            whitened_batch = self._whiten(self.emg_extended[start_idx + skip : end_idx])
            ipts_batch = self._separate(whitened_batch)
            spikes_batch = self._detect_spikes(ipts_batch * np.abs(ipts_batch))

            if self.config.adapt_sv:
                self._update_separation_vectors(
                    whitened_batch, ipts_batch, spikes_batch
                )

            ipts_output[start_idx + skip : end_idx] = ipts_batch
            spikes_output[start_idx + skip : end_idx] = spikes_batch

        remainder_samples = n_samples - n_batches * batch_size
        if remainder_samples > 0:
            start_idx = n_batches * batch_size
            ipts_output[start_idx :] = (
                self.sep_vectors @ (self.whitening @ self.emg_extended[start_idx:].T)
            ).T

        return ipts_output, spikes_output

    def _whiten(self, emg_batch: np.ndarray) -> np.ndarray:
        """Apply whitening and optionally update the whitening matrix."""
        whitened_signal = self.whitening @ emg_batch.T

        if self.config.adapt_wh:
            current_covariance = np.cov(whitened_signal).astype(np.float32)
            self.whitening_covariance = (
                (1 - self.config.cov_alpha) * self.whitening_covariance
                + self.config.cov_alpha * current_covariance
            )
            self.whitening -= self.config.wh_learning_rate * (
                (self.whitening_covariance - self.identity) @ self.whitening
            )

        return whitened_signal

    def _separate(self, whitened_signal: np.ndarray) -> np.ndarray:
        """Project whitened signal through separation vectors."""
        return (self.sep_vectors @ whitened_signal).T

    def _detect_spikes(self, ipts_squared: np.ndarray) -> np.ndarray:
        """K-means spike detection with refractory period and outlier rejection."""
        spikes = np.zeros(ipts_squared.shape, dtype=np.int32)

        for unit_idx in range(self.n_motor_units):
            peak_indices, _ = find_peaks(
                ipts_squared[:, unit_idx],
                distance=self.config.spike_dist,
            )

            if len(peak_indices) == 0:
                continue

            peak_values = ipts_squared[peak_indices, unit_idx]
            closer = np.abs(
                peak_values - self.spikes_centr[unit_idx]
            ) < np.abs(peak_values - self.base_centr[unit_idx])
            if closer.any():
                spike_vals = peak_values[closer]
                threshold = float(np.mean(spike_vals) + 3 * np.std(spike_vals))
                not_outlier = peak_values <= threshold
            else:
                not_outlier = np.zeros(len(peak_values), dtype=bool)
            labels = (closer & not_outlier).astype(np.int32)
            spikes[peak_indices, unit_idx] = labels

            if self.config.adapt_sd:
                n_spikes = int(labels.sum())
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

        return spikes

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
) -> tuple[np.ndarray, np.ndarray]:
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
