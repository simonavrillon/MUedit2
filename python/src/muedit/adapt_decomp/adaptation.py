"""Adaptive decomposition for MUedit decomposition path."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import numpy as np

from muedit.adapt_decomp.config import Config
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

#: ``sink(start, ipts, spikes)`` receives each processed segment: samples
#: ``[start, start + len(ipts))``, ipts ``(n, n_mu)`` float32, spikes ``(n, n_mu)`` 0/1.
BatchSink = Callable[[int, np.ndarray, np.ndarray], None]


@dataclass
class Calibration:
    """Whitening covariance, loss baselines and starting centroids fitted once on the calibration window."""

    whitening_covariance: np.ndarray
    kl_div_calib_mean: float
    kl_div_calib_std: float
    contrast_calib_mean: np.ndarray | None = None
    contrast_calib_std: np.ndarray | None = None
    base_centr: np.ndarray | None = None
    spikes_centr: np.ndarray | None = None


def calibration_centroids(ipts_sq: np.ndarray, fsamp: float) -> tuple[np.ndarray, np.ndarray]:
    """Baseline and spike centroids of each MU's squared calibration pulse train ``(n_mu, n)``."""
    n_mu = ipts_sq.shape[0]
    base_centr = np.zeros(n_mu, dtype=np.float32)
    spikes_centr = np.ones(n_mu, dtype=np.float32)
    for j in range(n_mu):
        pt = ipts_sq[j]
        peaks = find_refractory_peaks(pt, fsamp, min_isi_sec=POSTPROC_MIN_ISI_SEC)
        if len(peaks) > 1:
            _, centroids, _ = split_by_amplitude(pt, peaks)
            hi = int(np.argmax(centroids))
            spikes_centr[j] = float(centroids[hi])
            base_centr[j] = float(centroids[1 - hi])
        elif len(peaks) == 1:
            spikes_centr[j] = float(pt[peaks[0]])
    return base_centr, spikes_centr


class AdaptiveDecomp:
    """Adaptive decomposition implementation with stateful online learning."""

    def __init__(
        self,
        emg: np.ndarray | StreamedExtender,
        whitening: np.ndarray,
        sep_vectors: np.ndarray,
        base_centr: np.ndarray | None,
        spikes_centr: np.ndarray | None,
        emg_calib: np.ndarray | tuple[int, int] | Calibration,
        config: Config,
        artifact_mask: np.ndarray | None = None,
    ) -> None:
        """Set up one pass; ``emg_calib`` is raw samples, a sample range of ``emg``, or a ``Calibration``."""
        # Centroids left None come from the calibration: fitted on its window, or the ones it carries.
        self.config = config
        self.whitening = whitening.astype(np.float32, copy=True)
        self.sep_vectors = sep_vectors.astype(np.float32, copy=True)
        if base_centr is not None and spikes_centr is not None:
            self._set_centroids(base_centr, spikes_centr)
        fit_centroids = base_centr is None or spikes_centr is None
        self.n_motor_units = sep_vectors.shape[0]
        self.n_extended = whitening.shape[0]
        self.identity = np.eye(self.n_extended, dtype=np.float32)

        # A samples-first float32 extender carries its own artifact mask.
        if isinstance(emg, StreamedExtender):
            if emg.ex_factor != max(1, config.ex_factor):
                raise ValueError("extender ex_factor differs from config.ex_factor")
            self.source = emg
        else:
            self.source = StreamedExtender(
                np.asarray(emg).T,
                config.ex_factor,
                dtype=np.float32,
                samples_first=True,
                artifact_mask=artifact_mask,
            )
        self.n_samples = self.source.n_samples

        # The centroids are projected with the filters as given (float64 from the decomposition).
        centroid_filters = np.asarray(sep_vectors, dtype=np.float64) if fit_centroids else None
        if isinstance(emg_calib, Calibration):
            self.whitening_covariance = emg_calib.whitening_covariance.copy()
            self.kl_div_calib_mean = emg_calib.kl_div_calib_mean
            self.kl_div_calib_std = emg_calib.kl_div_calib_std
            if config.compute_loss:
                assert emg_calib.contrast_calib_mean is not None
                assert emg_calib.contrast_calib_std is not None
                self.contrast_calib_mean = emg_calib.contrast_calib_mean.copy()
                self.contrast_calib_std = emg_calib.contrast_calib_std.copy()
            if fit_centroids:
                if emg_calib.base_centr is None or emg_calib.spikes_centr is None:
                    raise ValueError("a Calibration without centroids needs them passed in")
                self._set_centroids(emg_calib.base_centr, emg_calib.spikes_centr)
        elif isinstance(emg_calib, tuple):
            self._calibrate(self.source, *emg_calib, centroid_filters=centroid_filters)
        else:
            calib = np.asarray(emg_calib, dtype=np.float32)
            source = StreamedExtender(
                calib.T, config.ex_factor, dtype=np.float32, samples_first=True
            )
            self._calibrate(source, 0, source.n_samples, centroid_filters=centroid_filters)
        self.calibration = Calibration(
            whitening_covariance=self.whitening_covariance.copy(),
            kl_div_calib_mean=self.kl_div_calib_mean,
            kl_div_calib_std=self.kl_div_calib_std,
            contrast_calib_mean=getattr(self, "contrast_calib_mean", None),
            contrast_calib_std=getattr(self, "contrast_calib_std", None),
            base_centr=self.base_centr.copy(),
            spikes_centr=self.spikes_centr.copy(),
        )

        logger.info(
            "AdaptiveDecomp initialized: %d motor units, %d extended channels",
            self.n_motor_units,
            self.n_extended,
        )

    def _set_centroids(self, base_centr: np.ndarray, spikes_centr: np.ndarray) -> None:
        """Start from these baseline and spike centroids, and the threshold between them."""
        self.base_centr = np.array(base_centr, dtype=np.float32)
        self.spikes_centr = np.array(spikes_centr, dtype=np.float32)
        self.height = self.spikes_centr - (self.spikes_centr - self.base_centr) / 2

    def _calibrate(
        self,
        source: StreamedExtender,
        start: int,
        stop: int,
        centroid_filters: np.ndarray | None = None,
    ) -> None:
        """Fit the whitening covariance, loss baselines and, given ``centroid_filters``, the centroids."""
        config = self.config
        start = max(start, source.first_complete)
        n = stop - start

        # Whitened batches are summed in float64 rather than stacked, so the
        # calibration never holds more than one chunk of the extended window.
        total = np.zeros(self.n_extended)
        outer = np.zeros((self.n_extended, self.n_extended))
        ipts_sq = None if centroid_filters is None else np.empty((self.n_motor_units, n))
        for lo in range(start, stop, _CALIB_CHUNK):
            hi = min(lo + _CALIB_CHUNK, stop)
            whitened = (source.read(lo, hi) @ self.whitening.T).astype(np.float64)
            total += whitened.sum(axis=0)
            outer += whitened.T @ whitened
            if centroid_filters is not None and ipts_sq is not None:
                # The centroids come from the same read, not a pass of their own.
                ipts_sq[:, lo - start : hi - start] = signed_square(centroid_filters @ whitened.T)
        mean = total / n
        self.whitening_covariance = ((outer - n * np.outer(mean, mean)) / max(n - 1, 1)).astype(
            np.float32
        )
        if ipts_sq is not None:
            self._set_centroids(*calibration_centroids(ipts_sq, config.fsamp))

        kl_divs: list[float] = []
        contrast_values: list[np.ndarray] = []
        batch_size = config.batch_size
        for lo in range(start, stop - batch_size + 1, batch_size):
            whitened = self.whitening @ source.read(lo, lo + batch_size).T
            batch_cov = np.cov(whitened).astype(np.float32)
            self.whitening_covariance = (
                1 - config.cov_alpha
            ) * self.whitening_covariance + config.cov_alpha * batch_cov
            if config.compute_loss:
                kl = self._kl_divergence()
                if not np.isnan(kl):
                    kl_divs.append(kl)
                ipts_batch = self._separate(whitened)
                spikes_batch = self._detect_spikes(
                    signed_square(ipts_batch), update_centroids=False
                )
                contrast_values.append(self._contrast_value(ipts_batch, spikes_batch))

        if config.compute_loss and kl_divs:
            self.kl_div_calib_mean = float(np.mean(kl_divs))
            self.kl_div_calib_std = float(np.std(kl_divs)) or 1.0
        else:
            self.kl_div_calib_mean = 0.0
            self.kl_div_calib_std = 1.0

        if not config.compute_loss:
            return
        if contrast_values:
            arr = np.stack(contrast_values, axis=0)  # (n_batches, n_mu)
            self.contrast_calib_mean = np.nanmean(arr, axis=0).astype(np.float32)
            self.contrast_calib_std = np.nanstd(arr, axis=0).astype(np.float32)
            self.contrast_calib_std[self.contrast_calib_std == 0] = 1.0
        else:
            self.contrast_calib_mean = np.zeros(self.n_motor_units, dtype=np.float32)
            self.contrast_calib_std = np.ones(self.n_motor_units, dtype=np.float32)

    def _batch_bounds(self, start: int, stop: int, reverse: bool) -> list[tuple[int, int]]:
        """Batches of ``[start, stop)`` in processing order; the partial one is processed last."""
        bs = self.config.batch_size
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
    ) -> dict[str, Any]:
        """Adapt over ``[start, stop)`` forward or backward; returns per-batch losses in processing order."""
        stop = self.n_samples if stop is None else stop
        bounds = self._batch_bounds(start, stop, reverse)
        n_batches = len(bounds)

        wh_losses: np.ndarray | None = None
        sv_losses: np.ndarray | None = None
        total_losses: np.ndarray | None = None
        if self.config.compute_loss:
            wh_losses = np.full(n_batches, np.nan, dtype=np.float32)
            sv_losses = np.full((n_batches, self.n_motor_units), np.nan, dtype=np.float32)
            total_losses = np.full(n_batches, np.nan, dtype=np.float32)

        for batch_idx, (batch_start, seg_end) in enumerate(bounds):
            full_batch = seg_end - batch_start == self.config.batch_size
            # The recording start has no look-back: it is never processed (zero output,
            # no adaptation), and the partial batch never has a loss.
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

            if not full_batch:
                whitened_batch = self.whitening @ batch.T
                ipts_batch = self._separate(whitened_batch)
                spikes_batch = self._detect_spikes_with_context(
                    ipts_batch, edges, update_centroids=True
                )
                if batch_mask is not None:
                    spikes_batch[batch_mask] = 0
                if self.config.adapt_sv and not batch_artifact:
                    self._update_separation_vectors(whitened_batch, ipts_batch, spikes_batch)
            elif batch_artifact:
                whitened_batch = self.whitening @ batch.T
                ipts_batch = self._separate(whitened_batch)
                spikes_batch = self._detect_spikes_with_context(
                    ipts_batch, edges, update_centroids=False
                )
                spikes_batch[batch_mask] = 0
            else:
                whitened_batch = self._whiten(batch)
                ipts_batch = self._separate(whitened_batch)
                spikes_batch = self._detect_spikes_with_context(
                    ipts_batch, edges, update_centroids=True
                )

                if wh_losses is not None and sv_losses is not None and total_losses is not None:
                    kl = self._kl_divergence()
                    whl = self._wh_loss(kl)
                    svl = self._sv_loss(self._contrast_value(ipts_batch, spikes_batch))
                    wh_losses[batch_idx] = whl
                    sv_losses[batch_idx] = svl
                    total_losses[batch_idx] = (0.0 if np.isnan(whl) else whl) + float(
                        np.nansum(svl)
                    )

                if self.config.adapt_sv:
                    self._update_separation_vectors(whitened_batch, ipts_batch, spikes_batch)

            if sink is not None:
                sink(seg_start, ipts_batch, spikes_batch)

        if not self.config.compute_loss:
            return {}
        return {"wh_loss": wh_losses, "sv_loss": sv_losses, "total_loss": total_losses}

    def _whiten(self, emg_batch: np.ndarray) -> np.ndarray:
        """Apply whitening and optionally update the whitening matrix."""
        whitened_signal = self.whitening @ emg_batch.T

        if self.config.adapt_wh or self.config.compute_loss:
            current_covariance = np.cov(whitened_signal).astype(np.float32)
            self.whitening_covariance = (
                1 - self.config.cov_alpha
            ) * self.whitening_covariance + self.config.cov_alpha * current_covariance

        if self.config.adapt_wh:
            self.whitening -= self.config.wh_learning_rate * (
                (self.whitening_covariance - self.identity) @ self.whitening
            )

        return whitened_signal

    def _separate(self, whitened_signal: np.ndarray) -> np.ndarray:
        """Project whitened signal through separation vectors (one MU is a one-row product)."""
        return zeroed_matmul(self.sep_vectors, whitened_signal).T

    def _project(self, emg_rows: np.ndarray) -> np.ndarray:
        """Project extended samples with the current whitening, without adapting."""
        return self._separate(self.whitening @ emg_rows.T)

    def _detect_spikes_with_context(
        self,
        ipts_batch: np.ndarray,
        edges: tuple[np.ndarray | None, np.ndarray | None],
        update_centroids: bool = True,
    ) -> np.ndarray:
        """Detect spikes on a batch padded with its flanking samples."""
        lo, hi = edges
        parts = [part for part in (lo, ipts_batch, hi) if part is not None]
        ipts_sq = signed_square(np.concatenate(parts, axis=0))
        start = 0 if lo is None else lo.shape[0]
        stop = start + ipts_batch.shape[0]
        spikes = self._detect_spikes(ipts_sq, update_centroids=update_centroids, core=(start, stop))
        return spikes[start:stop]

    def _detect_spikes(
        self,
        ipts_squared: np.ndarray,
        update_centroids: bool = True,
        core: tuple[int, int] | None = None,
    ) -> np.ndarray:
        """Spike detection with amplitude-bounded peak finding and midpoint threshold."""
        spikes = np.zeros(ipts_squared.shape, dtype=np.int32)
        core_lo, core_hi = core if core is not None else (0, ipts_squared.shape[0])

        for unit_idx in range(self.n_motor_units):
            min_h = float(self.base_centr[unit_idx] / self.config.spike_height_mult)
            max_h = float(self.config.spike_height_mult * self.spikes_centr[unit_idx])
            peak_indices = find_refractory_peaks(
                ipts_squared[:, unit_idx],
                self.config.fsamp,
                min_isi_sec=self.config.spike_dist_ms / 1000.0,
                height=(min_h, max_h),
            )

            if len(peak_indices) == 0:
                continue

            peak_values = ipts_squared[peak_indices, unit_idx]
            labels = (peak_values > self.height[unit_idx]).astype(np.int32)
            spikes[peak_indices, unit_idx] = labels

            if update_centroids and self.config.adapt_sd:
                # Padding peaks steer the refractory gate, but skewing the
                # centroids with them would change the adaptation itself.
                in_core = (peak_indices >= core_lo) & (peak_indices < core_hi)
                peak_values = peak_values[in_core]
                labels = labels[in_core]
                n_spikes = int(labels.sum())
                n_non_spikes = len(peak_values) - n_spikes
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

                self.height[unit_idx] = (
                    self.spikes_centr[unit_idx]
                    - (self.spikes_centr[unit_idx] - self.base_centr[unit_idx]) / 2
                )

        return spikes

    def _kl_divergence(self) -> float:
        """KL divergence between the estimated whitened covariance and the identity."""
        cov = self.whitening_covariance
        n = cov.shape[0]
        sign, logdet = np.linalg.slogdet(cov)
        if sign <= 0:
            return np.nan
        return float(0.5 * (-logdet + np.trace(cov) - n))

    def _wh_loss(self, kl_div: float) -> float:
        """Normalised whitening loss (squared z-score against calibration KL divergences)."""
        if np.isnan(kl_div):
            return np.nan
        return float(((kl_div - self.kl_div_calib_mean) / (self.kl_div_calib_std + 1e-12)) ** 2)

    def _contrast_value(self, ipts_batch: np.ndarray, spikes_batch: np.ndarray) -> np.ndarray:
        """Mean logcosh contrast value at spike positions, per MU (nan when no spikes)."""
        n_spikes = spikes_batch.sum(axis=0).astype(float)
        mean_ipts = (ipts_batch * spikes_batch).sum(axis=0) / (n_spikes + 1e-6)
        mean_ipts = mean_ipts.copy()
        mean_ipts[n_spikes == 0] = np.nan
        return np.log(np.cosh(mean_ipts))

    def _sv_loss(self, contrast: np.ndarray) -> np.ndarray:
        """Normalised separation vector loss (squared z-score against calibration contrast)."""
        return ((contrast - self.contrast_calib_mean) / (self.contrast_calib_std + 1e-12)) ** 2

    def _update_separation_vectors(
        self,
        whitened_signal: np.ndarray,
        ipts: np.ndarray,
        spikes: np.ndarray,
    ) -> None:
        """Gradient ascent on the logcosh contrast with Gram-Schmidt deflation."""
        gradients_all = np.tanh(ipts) * spikes

        spike_counts = spikes.sum(axis=0)
        gradients = whitened_signal @ gradients_all / np.maximum(spike_counts, 1)

        separation_vectors_new = self.sep_vectors.copy()

        active = spike_counts > 0
        if active.any():
            separation_vectors_new[active] += self.config.sv_learning_rate * gradients[:, active].T
            norms = np.linalg.norm(separation_vectors_new[active], axis=1, keepdims=True)
            separation_vectors_new[active] /= np.where(norms > 1e-8, norms, 1.0)
            for unit_idx in range(1, self.n_motor_units):
                if not active[unit_idx]:
                    continue
                separation_vectors_new[unit_idx] -= separation_vectors_new[:unit_idx].T @ (
                    separation_vectors_new[:unit_idx] @ separation_vectors_new[unit_idx]
                )
                norm = np.linalg.norm(separation_vectors_new[unit_idx])
                if norm > 1e-8:
                    separation_vectors_new[unit_idx] /= norm
            self.sep_vectors[active] = separation_vectors_new[active]


def run_adaptive_decomposition(
    emg: np.ndarray,
    whitening: np.ndarray,
    sep_vectors: np.ndarray,
    base_centr: np.ndarray,
    spikes_centr: np.ndarray,
    emg_calib: np.ndarray,
    config: Config,
    artifact_mask: np.ndarray | None = None,
) -> tuple[np.ndarray, np.ndarray, dict[str, Any]]:
    """Functional entry point: one forward pass over an in-memory ``emg``, dense outputs."""
    model = AdaptiveDecomp(
        emg=emg,
        whitening=whitening,
        sep_vectors=sep_vectors,
        base_centr=base_centr,
        spikes_centr=spikes_centr,
        emg_calib=emg_calib,
        config=config,
        artifact_mask=artifact_mask,
    )
    ipts = np.zeros((model.n_samples, model.n_motor_units), dtype=np.float32)
    spikes = np.zeros((model.n_samples, model.n_motor_units), dtype=np.int32)

    def _dense(start: int, ipts_batch: np.ndarray, spikes_batch: np.ndarray) -> None:
        ipts[start : start + len(ipts_batch)] = ipts_batch
        spikes[start : start + len(spikes_batch)] = spikes_batch

    losses = model.run(sink=_dense)
    if losses and model.n_samples % config.batch_size:
        # The trailing partial batch never has a loss; keep one entry per full batch.
        losses = {k: v[:-1] for k, v in losses.items()}
    return ipts, spikes, losses
