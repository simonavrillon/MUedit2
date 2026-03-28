"""Utility conversions and metrics for adaptive decomposition outputs."""


import numpy as np
from typing import Union, Optional, Tuple, List
from scipy import signal
import itertools

def firings_to_spikes(firings, ipts, matlab_index=False):
    """Convert firing index lists to binary spike-train matrix."""
    spikes = np.zeros_like(ipts)
    for i, firing in enumerate(firings):
        if matlab_index:
            firing = firing - 1
        spikes[i, firing.astype(int)] = 1

    return spikes

def _check_mu_format(data: np.ndarray) -> np.ndarray:
    """Ensure MU arrays are 2D with shape (samples, units)."""

    if len(data.shape) == 1:
        data = np.expand_dims(data, axis=-1)
    return data


def get_discharge_rate(
        spike_train: np.ndarray,
        timestamps: Union[list, np.ndarray]
        ) -> np.ndarray:
    """Compute average discharge rate excluding long silent periods."""

                                                 
    spike_train = _check_mu_format(spike_train.astype(bool))
    units = spike_train.shape[-1]
    dr = np.zeros(units)

    for unit in range(units):
                                             
        n_spikes = np.sum(spike_train[:, unit].astype(int))

        if n_spikes == 0:
            continue

                          
        times_spikes = timestamps[spike_train[:, unit]]

                                 
        total_period = times_spikes[-1] - times_spikes[0]

        if total_period == 0:
            continue

                                             
        isi = np.diff(times_spikes)

                                                                            
                                                                               
                                                                      
                                               
        silent_period = np.sum(isi[isi > 0.25])

                                                              
        active_period = total_period - silent_period

                                    
        dr[unit] = n_spikes / active_period

    return dr


def get_number_of_spikes(spike_train: np.ndarray) -> np.ndarray:
    """Count spikes per motor unit."""

                                  
    n_spikes = np.sum(spike_train.astype(int), axis=0)

    return n_spikes


def get_inst_discharge_rate(
        spike_train: np.ndarray,
        fs: Optional[int] = 2048
        ) -> np.ndarray:
    """Estimate instantaneous discharge rate via Hann smoothing."""

                                                      
    spike_train = _check_mu_format(spike_train.astype(bool))
    units = spike_train.shape[-1]
    inst_dr = np.zeros(spike_train.shape)

                           
    dur = 1                              
    hann_win = np.hanning(np.round(dur * fs))

    for unit in range(units):
                                                           
        inst_dr[:, unit] = np.convolve(
            spike_train[:, unit], hann_win, mode='same'
            ) * 2

    return inst_dr


def get_coefficient_of_variation(
        spike_train: np.ndarray,
        timestamps: Union[list, np.ndarray]
        ) -> np.ndarray:
    """Compute coefficient of variation (%) of inter-spike intervals."""
                             
    spike_train = _check_mu_format(spike_train.astype(bool))
    units = spike_train.shape[-1]
    cov = np.zeros(units)
    cov[:] = np.nan

    for unit in range(units):
        if not np.any(spike_train[:, unit]):
            continue

        times_spikes = timestamps[spike_train[:, unit]]
        isi = np.diff(times_spikes)
        isi = isi[isi < 0.25]
        cov[unit] = np.std(isi) / np.mean(isi)

    return cov * 100


def get_pulse_to_noise_ratio(
    spike_train: np.ndarray,
    ips: np.ndarray,
    ext_fact: int = 8
    ) -> np.ndarray:
    """Compute pulse-to-noise ratio (dB) from IPT peak vs baseline amplitude."""

                                                  
    spike_train = _check_mu_format(spike_train.astype(bool))
    ips = _check_mu_format(ips)
    units = spike_train.shape[-1]
    pnr = np.zeros(units)
    pnr[:] = np.nan

                 
    ipts2 = ips ** 2

    for unit in range(units):
                                                                              
        spikes_idx = np.nonzero(spike_train[:, unit])[0]
        spikes_idx = spikes_idx[np.greater_equal(spikes_idx, ext_fact + 1)]

        if not np.any(spikes_idx):
            continue

        spikes = ipts2[spikes_idx, unit]
        min_spikes_amp = np.amin(spikes)

                                                                       
        baseline_peaks_idx, _ = signal.find_peaks(
            ipts2[:, unit], height=(0, min_spikes_amp)
            )
        if not np.any(baseline_peaks_idx):
            baseline_peaks_idx = np.nonzero(
                np.logical_not(spike_train[:, unit].astype(bool))
                )[0]
        baseline_peaks_idx = baseline_peaks_idx[
            np.greater_equal(baseline_peaks_idx, ext_fact + 1)
            ]
        baseline = ipts2[baseline_peaks_idx, unit]

        if len(spikes) == 0:
            continue

                                                 
        spikes_mean = np.mean(spikes)
        baseline_mean = np.mean(baseline)

                     
        pnr[unit] = 20 * np.log10(spikes_mean / baseline_mean)

    return pnr


def get_silhouette_measure(
    spike_train: np.ndarray,
    ipts: np.ndarray,
    ext_fact: int = 8
) -> np.ndarray:
    """Compute silhouette-style separability metric for spike amplitudes."""

                                                  
    spike_train = _check_mu_format(spike_train.astype(bool))
    ipts = _check_mu_format(ipts)
    units = spike_train.shape[-1]
    sil = np.zeros(units)
    sil[:] = np.nan

                 
    ipts2 = ipts ** 2

    for unit in range(units):
                                                                              
        spikes_idx = np.nonzero(spike_train[:, unit])[0]
        spikes_idx = spikes_idx[np.greater_equal(spikes_idx, ext_fact + 1)]

        if not np.any(spikes_idx):
            continue

        spikes_amp = ipts2[spikes_idx, unit]
        min_spikes_amp = np.amin(spikes_amp)

                                                                       
        baseline_peaks_idx, _ = signal.find_peaks(
            ipts2[:, unit], height=(0, min_spikes_amp)
            )
        if not np.any(baseline_peaks_idx):
            baseline_peaks_idx = np.nonzero(
                np.logical_not(spike_train[:, unit].astype(bool))
                )[0]
        baseline_peaks_idx = baseline_peaks_idx[
            np.greater_equal(baseline_peaks_idx, ext_fact + 1)
            ]
        baseline_amp = ipts2[baseline_peaks_idx, unit]

                                                 
        spikes_mean = np.mean(spikes_amp)
        baseline_mean = np.mean(baseline_amp)

                           
        dist_sum_spikes = np.sum(np.power((spikes_amp - spikes_mean), 2))
        dist_sum_baseline = np.sum(np.power((spikes_amp - baseline_mean), 2))

                     
        max_dist = np.amax([dist_sum_spikes, dist_sum_baseline])
        if max_dist == 0:
            sil[unit] = 0
        else:
            sil[unit] = (dist_sum_baseline - dist_sum_spikes) / max_dist

    return sil

def rate_of_agreement_paired(
    spike_trains_ref: np.ndarray,
    spike_trains_test: np.ndarray,
    fs: Optional[int] = 2048,
    tol_spike_ms: Optional[int] = 1,
    tol_train_ms: Optional[int] = 40
) -> Tuple[np.ndarray, List[Tuple[int, int]], np.ndarray]:
    """Compute RoA for already paired spike trains with lag correction."""
                              
    if len(spike_trains_ref.shape) == 1:
        spike_trains_ref = np.expand_dims(spike_trains_ref, axis=-1)

    if len(spike_trains_test.shape) == 1:
        spike_trains_test = np.expand_dims(spike_trains_test, axis=-1)

    if spike_trains_ref.shape != spike_trains_test.shape:
        raise ValueError(f'Dimensionality mismatch between ref {spike_trains_ref.shape} and test {spike_trains_test.shape}.')

                                 
    tol_spike = round(tol_spike_ms / 1000 * fs)
    tol_train = round(tol_train_ms / 1000 * fs)

                               
    n_units = spike_trains_test.shape[1]

                                              
    if (not np.any(spike_trains_ref)) | (not np.any(spike_trains_test)):
        pair_idx = np.arange(n_units)
        pair_lag = np.zeros((n_units))
        roa = np.zeros((n_units))
        return roa, pair_idx, pair_lag

                                      
                                       
                                      
    spikes_corr = np.zeros((n_units))
    roa = np.empty((n_units))
    pair_lag = np.zeros((n_units))
    pair_idx = [(unit, unit) for unit in range(n_units)]

    for unit in range(n_units):
                                                                      
                                                                     
                     
        train_ref = spike_trains_ref[:, unit]
        train_test = spike_trains_test[:, unit]
                               
        train_ref = np.convolve(train_ref, np.ones(tol_spike), mode="same")
        train_test = np.convolve(train_test, np.ones(tol_spike), mode="same")
                                      
        curr_corr = signal.correlate(train_ref, train_test, mode="full")
        curr_lags = signal.correlation_lags(
            len(train_ref), len(train_test), mode="full"
        )
                                     
        train_tol_idxs = np.nonzero(np.abs(curr_lags) == tol_train)[0]
        train_tol_mask = np.arange(train_tol_idxs[0], train_tol_idxs[-1] + 1).astype(
            int
        )
        curr_corr = curr_corr[train_tol_mask]
        curr_lags = curr_lags[train_tol_mask]
                                            
        trains_lag = curr_lags[np.argmax(np.abs(curr_corr))]
        if not np.isscalar(trains_lag):
                                                                        
            trains_lag = np.amin(trains_lag)
                       
        spikes_corr[unit] = np.amax(curr_corr)
        pair_lag[unit] = trains_lag

                                                                     
                                                                    
                            
        firings_ref = np.nonzero(spike_trains_ref[:, unit])[0]
        firings_test = np.nonzero(spike_trains_test[:, unit])[0] + pair_lag[unit]
                              
        firings_common = 0
        firings_ref_only = 0
        firings_test_only = 0
                      
        for firing in firings_ref:
            curr_firing_diff = np.abs(firings_test - firing)
            if np.any(curr_firing_diff <= tol_spike):
                                 
                firings_common += 1
                firings_test = np.delete(firings_test, np.argmin(curr_firing_diff))
            else:
                                           
                firings_ref_only += 1
        firings_test_only = len(firings_test)
                                   
        roa[unit] = firings_common / (
            firings_common + firings_ref_only + firings_test_only
        )

    return roa, pair_idx, pair_lag


def rate_of_agreement(
    spike_trains_ref: Union[np.ndarray, None],
    spike_trains_test: np.ndarray,
    fs: Optional[int] = 2048,
    tol_spike_ms: Optional[int] = 1,
    tol_train_ms: Optional[int] = 40,
) -> Tuple[np.ndarray, List[Tuple[int, int]], np.ndarray]:
    """Pair units by max correlation then compute rate of agreement per pair."""

                              
    if spike_trains_ref is not None:
        if len( spike_trains_ref.shape ) == 1:
            spike_trains_ref = np.expand_dims(spike_trains_ref, axis=-1)

    if spike_trains_test is not None:
        if len( spike_trains_test.shape ) == 1:
            spike_trains_test = np.expand_dims(spike_trains_test, axis=-1)
    
    if spike_trains_ref.shape[0] != spike_trains_test.shape[0]:
        raise ValueError(f'Time dimensionality mismatch between ref {spike_trains_ref.shape} and test {spike_trains_test.shape}.')

                                 
    tol_spike = round(tol_spike_ms/1000 * fs)
    tol_train = round(tol_train_ms/1000 * fs)

                               
    n_units_test = spike_trains_test.shape[1]

                                                                
    if not np.any(spike_trains_test):
        pair_idx = np.arange(n_units_test)
        pair_lag = np.zeros((n_units_test))
        roa = np.zeros((n_units_test))
        return roa, pair_idx, pair_lag

    if spike_trains_ref is None:
                                                           
                                                           
                                          
        spikes_corr = np.zeros((n_units_test, n_units_test))
        spikes_lag = np.zeros((n_units_test, n_units_test))

                                                                      
        pairs = itertools.combinations(range(n_units_test), 2)
        for pair in pairs:
                         
            train_0 = spike_trains_test[:, pair[0]]
            train_1 = spike_trains_test[:, pair[1]]
                                   
            train_0 = np.convolve(train_0, np.ones(tol_spike), mode="same")
            train_1 = np.convolve(train_1, np.ones(tol_spike), mode="same")
                                          
            curr_corr = signal.correlate(train_0, train_1, mode="full")
            curr_lags = signal.correlation_lags(len(train_0), len(train_1), mode="full")
                                                
            trains_lag = curr_lags[np.argmax(np.abs(curr_corr))]
            if not np.isscalar(trains_lag):
                                                                            
                trains_lag = np.amin(trains_lag)
                                                  
            if np.abs(trains_lag) > tol_train:
                trains_lag = 0
                           
            spikes_corr[pair] = np.amax(curr_corr)
            spikes_lag[pair] = int(trains_lag)

    else:
                                          
                                           
                                        
        n_units_ref = spike_trains_ref.shape[-1]

                                          
        spikes_corr = np.zeros((n_units_ref, n_units_test))
        spikes_lag = np.zeros((n_units_ref, n_units_test))

                                                                      
        for unit_ref in range(n_units_ref):
            for unit_test in range(n_units_test):
                             
                train_0 = spike_trains_ref[:, unit_ref]
                train_1 = spike_trains_test[:, unit_test]
                                       
                train_0 = np.convolve(train_0, np.ones(tol_spike), mode="same")
                train_1 = np.convolve(train_1, np.ones(tol_spike), mode="same")
                                              
                curr_corr = signal.correlate(train_0, train_1, mode="full")
                curr_lags = signal.correlation_lags(
                    len(train_0), len(train_1), mode="full"
                )
                                                    
                trains_lag = curr_lags[np.argmax(np.abs(curr_corr))]
                if not np.isscalar(trains_lag):
                                                                                
                    trains_lag = np.amin(trains_lag)
                               
                spikes_corr[unit_ref, unit_test] = np.amax(curr_corr)
                spikes_lag[unit_ref, unit_test] = int(trains_lag)

                                                                 
    pair_idx = []
    pair_lag = []
    while np.any(sum(spikes_corr)):
        idx_max_corr = np.unravel_index(np.argmax(spikes_corr), spikes_corr.shape)
        pair_idx.append(idx_max_corr)
        pair_lag.append(int(spikes_lag[idx_max_corr]))
        spikes_corr[idx_max_corr[0], :] = 0
        spikes_corr[:, idx_max_corr[1]] = 0
        if spike_trains_ref is None:
            spikes_corr[idx_max_corr[1], :] = 0
            spikes_corr[:, idx_max_corr[0]] = 0

                               
    roa = np.empty((len(pair_idx)))
    for i, pair in enumerate(pair_idx):
                                                         
        if spike_trains_ref is None:
            firings_0 = np.nonzero(spike_trains_test[:, pair[0]])[0]
        else:
            firings_0 = np.nonzero(spike_trains_ref[:, pair[0]])[0]
        firings_1 = np.nonzero(spike_trains_test[:, pair[1]])[0] + pair_lag[i]

                              
                                        
        firings_common = 0
        firings_0_only = 0
        firings_1_only = 0
                      
        for firing in firings_0:
            curr_firing_diff = np.abs(firings_1 - firing)
            if np.any(curr_firing_diff <= tol_spike):
                                 
                firings_common += 1
                firings_1 = np.delete(firings_1, np.argmin(curr_firing_diff))
            else:
                                   
                firings_0_only += 1
        firings_1_only = len(firings_1)
                                   
        roa[i] = firings_common / (firings_common + firings_0_only + firings_1_only)

                                         
    first_pair = [pair[1] for pair in pair_idx]
    pairs_sort_idx = np.argsort(first_pair)

    roa_sorted = roa[pairs_sort_idx]
    pair_idx_sorted = [pair_idx[i] for i in pairs_sort_idx]
    pair_lag_sorted = [int(pair_lag[i]) for i in pairs_sort_idx]

    return roa_sorted, pair_idx_sorted, pair_lag_sorted
