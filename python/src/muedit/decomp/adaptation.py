"""Adaptive decomposition: batch-by-batch whitening, separation-vector and centroid updates."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np

from muedit.decomp.types import DecompositionParameters
from muedit.signal.decomp_primitives import (
    POSTPROC_MIN_ISI_SEC,
    find_refractory_peaks,
    signed_square,
    split_by_amplitude,
    zeroed_matmul,
)
from muedit.signal.streaming import StreamedExtender

logger = logging.getLogger(__name__)

_CALIB_CHUNK = 4096
# Peaks outside [base / mult, mult * spike] centroid are not candidate discharges.
_SPIKE_HEIGHT_MULT = 3

#: ``sink(start, pulse_trains, spikes)`` receives each processed segment: samples
#: ``[start, start + len(pulse_trains))``, pulse_trains ``(n, n_mu)`` in the work dtype, spikes ``(n, n_mu)`` 0/1.
BatchSink = Callable[[int, np.ndarray, np.ndarray], None]


@dataclass
class Calibration:
    """Whitening covariance (only fitted with ``adapt_wh``) and starting centroids of the calibration window."""

    whitening_covariance: np.ndarray | None
    base_centr: np.ndarray
    spikes_centr: np.ndarray


def calibration_centroids(pulse_trains: np.ndarray, fsamp: float) -> tuple[np.ndarray, np.ndarray]:
    """Baseline and spike centroids of each MU's squared calibration pulse train ``(n_mu, n)``."""
    n_mu = pulse_trains.shape[0]
    base_centr = np.zeros(n_mu, dtype=np.float32)
    spikes_centr = np.ones(n_mu, dtype=np.float32)
    for j in range(n_mu):
        pulse_train = pulse_trains[j]
        peaks = find_refractory_peaks(pulse_train, fsamp, min_isi_sec=POSTPROC_MIN_ISI_SEC)
        if len(peaks) > 1:
            _, centroids, _ = split_by_amplitude(pulse_train, peaks)
            hi = int(np.argmax(centroids))
            spikes_centr[j] = float(centroids[hi])
            base_centr[j] = float(centroids[1 - hi])
        elif len(peaks) == 1:
            spikes_centr[j] = float(pulse_train[peaks[0]])
    return base_centr, spikes_centr


class AdaptiveDecomp:
    """Adaptive decomposition implementation with stateful online learning."""

    def __init__(
        self,
        source: StreamedExtender,
        whiten_mat: np.ndarray,
        mu_filters: np.ndarray,
        calibration: tuple[int, int] | Calibration,
        params: DecompositionParameters,
        fsamp: float,
    ) -> None:
        """Set up one pass over ``source``; ``calibration`` is a sample range to fit, or an earlier pass's fit."""
        self.params = params
        self.fsamp = fsamp
        self.dtype = params.work_dtype
        self.batch_size = int(params.adapt_batch_ms * fsamp / 1000)
        self.source = source
        self.n_samples = source.n_samples
        self.whiten_mat = whiten_mat.astype(self.dtype, copy=True)
        self.mu_filters = mu_filters.astype(self.dtype, copy=True)  # (n_extended, n_mu)
        self.n_mu = mu_filters.shape[1]
        self.n_extended = whiten_mat.shape[0]
        self.identity = np.eye(self.n_extended, dtype=self.dtype)
        self.whitening_covariance: np.ndarray | None = None

        if isinstance(calibration, Calibration):
            self.whitening_covariance = calibration.whitening_covariance
            self._set_centroids(calibration.base_centr, calibration.spikes_centr)
        else:
            # The centroids are projected with the filters as given (float64 from the decomposition).
            self._calibrate(*calibration, np.asarray(mu_filters, dtype=np.float64))
        self.calibration = Calibration(
            whitening_covariance=self.whitening_covariance,
            base_centr=self.base_centr.copy(),
            spikes_centr=self.spikes_centr.copy(),
        )

        logger.info(
            "AdaptiveDecomp initialized: %d motor units, %d extended channels",
            self.n_mu,
            self.n_extended,
        )

    def _set_centroids(self, base_centr: np.ndarray, spikes_centr: np.ndarray) -> None:
        """Start from these baseline and spike centroids, and the threshold between them."""
        self.base_centr = np.array(base_centr, dtype=self.dtype)
        self.spikes_centr = np.array(spikes_centr, dtype=self.dtype)
        self.height = self.spikes_centr - (self.spikes_centr - self.base_centr) / 2

    def _calibrate(self, start: int, stop: int, centroid_filters: np.ndarray) -> None:
        """Fit the centroids and, with ``adapt_wh``, the whitening covariance in one read of ``[start, stop)``."""
        start = max(start, self.source.first_complete)
        n = stop - start
        bs = self.batch_size
        n_batches = n // bs
        alpha = self.params.adapt_cov_alpha
        adapt_wh = self.params.adapt_wh

        # The covariance is the window's, then a moving average over the window's full batches.
        # That average is linear, so each batch adds its term with its final weight as the read
        # reaches it, and the window covariance joins at the end.
        total = np.zeros(self.n_extended)
        outer = np.zeros((self.n_extended, self.n_extended))
        batch_terms = np.zeros((self.n_extended, self.n_extended))
        pulse_trains = np.empty((self.n_mu, n))
        # Whitened chunks are summed in float64 rather than stacked, so the calibration never
        # holds more than one chunk of the extended window; a chunk holds whole batches.
        chunk = max(1, _CALIB_CHUNK // bs) * bs
        for lo in range(start, stop, chunk):
            hi = min(lo + chunk, stop)
            whitened = (self.source.read(lo, hi) @ self.whiten_mat.T).astype(np.float64)
            pulse_trains[:, lo - start : hi - start] = signed_square(
                centroid_filters.T @ whitened.T
            )
            if not adapt_wh:
                continue
            total += whitened.sum(axis=0)
            outer += whitened.T @ whitened
            for b in range(lo, min(hi, start + n_batches * bs), bs):
                weight = alpha * (1 - alpha) ** (n_batches - 1 - (b - start) // bs)
                batch_terms += weight * np.cov(whitened[b - lo : b - lo + bs].T)
        self._set_centroids(*calibration_centroids(pulse_trains, self.fsamp))

        if adapt_wh:
            mean = total / n
            window_cov = (outer - n * np.outer(mean, mean)) / max(n - 1, 1)
            self.whitening_covariance = (
                (1 - alpha) ** n_batches * window_cov + batch_terms
            ).astype(self.dtype)

    def _batch_bounds(self, start: int, stop: int, reverse: bool) -> list[tuple[int, int]]:
        """Batches of ``[start, stop)`` in processing order; the partial one is processed last."""
        bs = self.batch_size
        n_full = (stop - start) // bs
        if reverse:
            bounds = [(stop - (k + 1) * bs, stop - k * bs) for k in range(n_full)]
            partial = (start, stop - n_full * bs)
        else:
            bounds = [(start + k * bs, start + (k + 1) * bs) for k in range(n_full)]
            partial = (start + n_full * bs, stop)
        if partial[1] > partial[0]:
            bounds.append(partial)
        return bounds

    def run(
        self,
        start: int = 0,
        stop: int | None = None,
        reverse: bool = False,
        sink: BatchSink | None = None,
    ) -> None:
        """Adapt over ``[start, stop)`` forward or backward."""
        stop = self.n_samples if stop is None else stop
        for batch_start, seg_end in self._batch_bounds(start, stop, reverse):
            full_batch = seg_end - batch_start == self.batch_size
            # The recording start has no look-back: it is never processed (zero output,
            # no adaptation).
            seg_start = max(batch_start, self.source.first_complete)
            if seg_start >= seg_end:
                continue

            # One read covers the batch and the sample on each side of it, the
            # context find_peaks needs to see a discharge on a batch edge.
            read_start = max(0, seg_start - 1)
            ext = self.source.read(read_start, min(seg_end + 1, self.n_samples))
            batch = ext[seg_start - read_start : seg_end - read_start]
            batch_mask = self.source.mask(seg_start, seg_end)
            batch_artifact = batch_mask is not None and bool(batch_mask.any())

            # Before _whiten, which updates the whitening in place: the context
            # must be projected with the same matrix as the batch it flanks.
            edges = (
                self._project(ext[:1]) if seg_start > 0 else None,
                self._project(ext[-1:]) if seg_end < self.n_samples else None,
            )

            # Only a full, artifact-free batch adapts the whitening, which needs a covariance.
            if not full_batch:
                whitened_batch = self.whiten_mat @ batch.T
                pulse_trains_batch = self._separate(whitened_batch)
                spikes_batch = self._detect_spikes(pulse_trains_batch, edges, update_centroids=True)
                if batch_mask is not None:
                    spikes_batch[batch_mask] = 0
                if self.params.adapt_sv and not batch_artifact:
                    self._update_separation_vectors(
                        whitened_batch, pulse_trains_batch, spikes_batch
                    )
            elif batch_artifact:
                # A full batch with an artifact adapts nothing, so the artifact cannot steer the model.
                whitened_batch = self.whiten_mat @ batch.T
                pulse_trains_batch = self._separate(whitened_batch)
                spikes_batch = self._detect_spikes(
                    pulse_trains_batch, edges, update_centroids=False
                )
                spikes_batch[batch_mask] = 0
            else:
                whitened_batch = self._whiten(batch)
                pulse_trains_batch = self._separate(whitened_batch)
                spikes_batch = self._detect_spikes(pulse_trains_batch, edges, update_centroids=True)
                if self.params.adapt_sv:
                    self._update_separation_vectors(
                        whitened_batch, pulse_trains_batch, spikes_batch
                    )

            if sink is not None:
                sink(seg_start, pulse_trains_batch, spikes_batch)

    def _whiten(self, emg_batch: np.ndarray) -> np.ndarray:
        """Apply whitening and optionally update the whitening matrix."""
        whitened_signal = self.whiten_mat @ emg_batch.T

        if self.params.adapt_wh:
            covariance = self._update_covariance(whitened_signal)
            self.whiten_mat -= self.params.adapt_wh_learning_rate * (
                (covariance - self.identity) @ self.whiten_mat
            )

        return whitened_signal

    def _update_covariance(self, whitened_signal: np.ndarray) -> np.ndarray:
        """Blend the covariance of one whitened batch into the running whitening covariance."""
        assert self.whitening_covariance is not None  # fitted whenever adapt_wh
        alpha = self.params.adapt_cov_alpha
        self.whitening_covariance = (1 - alpha) * self.whitening_covariance + alpha * np.cov(
            whitened_signal
        ).astype(self.dtype)
        return self.whitening_covariance

    def _separate(self, whitened_signal: np.ndarray) -> np.ndarray:
        """Project whitened signal through separation vectors (one MU is a one-row product)."""
        return zeroed_matmul(self.mu_filters.T, whitened_signal).T

    def _project(self, emg_rows: np.ndarray) -> np.ndarray:
        """Project extended samples with the current whitening, without adapting."""
        return self._separate(self.whiten_mat @ emg_rows.T)

    def _detect_spikes(
        self,
        pulse_trains_batch: np.ndarray,
        edges: tuple[np.ndarray | None, np.ndarray | None],
        update_centroids: bool = True,
    ) -> np.ndarray:
        """Spikes of a batch, its peaks found with the flanking ``edges`` samples as context."""
        lo, hi = edges
        parts = [part for part in (lo, pulse_trains_batch, hi) if part is not None]
        pulse_trains = signed_square(np.concatenate(parts, axis=0))
        start = 0 if lo is None else lo.shape[0]
        stop = start + pulse_trains_batch.shape[0]
        spikes = np.zeros(pulse_trains_batch.shape, dtype=np.int32)

        for j in range(self.n_mu):
            min_h = float(self.base_centr[j] / _SPIKE_HEIGHT_MULT)
            max_h = float(_SPIKE_HEIGHT_MULT * self.spikes_centr[j])
            peak_indices = find_refractory_peaks(
                pulse_trains[:, j],
                self.fsamp,
                min_isi_sec=POSTPROC_MIN_ISI_SEC,
                height=(min_h, max_h),
            )
            # Padding peaks steer the refractory gate but belong to the neighbouring batches.
            peak_indices = peak_indices[(peak_indices >= start) & (peak_indices < stop)]
            if len(peak_indices) == 0:
                continue

            peak_values = pulse_trains[peak_indices, j]
            labels = (peak_values > self.height[j]).astype(np.int32)
            spikes[peak_indices - start, j] = labels

            if update_centroids and self.params.adapt_sd:
                n_spikes = int(labels.sum())
                n_non_spikes = len(peak_values) - n_spikes
                if n_spikes > 0:
                    spike_centroid = np.mean(peak_values[labels.astype(bool)])
                    self.spikes_centr[j] = (
                        self.params.adapt_spike_prev_weight * self.spikes_centr[j]
                        + n_spikes * spike_centroid
                    ) / (self.params.adapt_spike_prev_weight + n_spikes)

                if n_non_spikes > 0:
                    baseline_centroid = np.mean(peak_values[~labels.astype(bool)])
                    self.base_centr[j] = (
                        self.params.adapt_spike_prev_weight * self.base_centr[j]
                        + n_non_spikes * baseline_centroid
                    ) / (self.params.adapt_spike_prev_weight + n_non_spikes)

                self.height[j] = (
                    self.spikes_centr[j] - (self.spikes_centr[j] - self.base_centr[j]) / 2
                )

        return spikes

    def _update_separation_vectors(
        self,
        whitened_signal: np.ndarray,
        pulse_trains: np.ndarray,
        spikes: np.ndarray,
    ) -> None:
        """Gradient ascent on the logcosh contrast with Gram-Schmidt deflation."""
        spike_counts = spikes.sum(axis=0)
        gradients = whitened_signal @ (np.tanh(pulse_trains) * spikes) / np.maximum(spike_counts, 1)

        active = spike_counts > 0
        if active.any():
            new_filters = self.mu_filters.copy()
            new_filters[:, active] += self.params.adapt_sv_learning_rate * gradients[:, active]
            norms = np.linalg.norm(new_filters[:, active], axis=0)
            new_filters[:, active] /= np.where(norms > 1e-8, norms, 1.0)
            for j in range(1, self.n_mu):
                if not active[j]:
                    continue
                new_filters[:, j] -= new_filters[:, :j] @ (new_filters[:, :j].T @ new_filters[:, j])
                norm = np.linalg.norm(new_filters[:, j])
                if norm > 1e-8:
                    new_filters[:, j] /= norm
            self.mu_filters[:, active] = new_filters[:, active]
