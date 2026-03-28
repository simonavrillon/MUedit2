"""Adaptive decomposition runtime using PyTorch-based batch updates."""


import time
import torch
import numpy as np
from torch.utils.data import DataLoader
from typing import Tuple, Optional
from scipy import signal
from adapt_decomp.config import Config
from adapt_decomp.data_structures import Data, Decomposition
from adapt_decomp.io import H5PraramsBatchWriter

class AdaptDecomp():

    def __init__(
            self,
            emg: torch.Tensor,
            whitening: torch.Tensor,
            sep_vectors: torch.Tensor,
            base_centr: torch.Tensor,
            spikes_centr: torch.Tensor,
            emg_calib: torch.Tensor,
            ipts_calib: torch.Tensor,
            spikes_calib: torch.Tensor,
            preprocess: Optional[bool] = True,
            config: Optional[Config] = Config(),
            store_init: Optional[bool] = False,
            save_path: Optional[str] = None,
    ) -> None:
        
                                                          
        self.config = config
        if self.config.device is None:
            if torch.cuda.is_available():
                self.config.device = "cuda"
            else:
                self.config.device = "cpu"
    
        self.decomp = Decomposition(whitening, sep_vectors, base_centr, spikes_centr, emg_calib, ipts_calib, spikes_calib, self.config)
        self.data = Data(emg, preprocess, config)
        self.save_path = save_path
    
    def init_exe_time(self, batches:int) -> None:
        self.time_sv_ms = torch.zeros(batches, dtype=torch.float32)
        self.time_wh_ms = torch.zeros(batches, dtype=torch.float32)
        self.time_sd_ms = torch.zeros(batches, dtype=torch.float32)

    def init_outputs(self, samples:int, units:int) -> None:
        self.units = units
        self.samples = samples
        self.spikes = torch.zeros(samples, units, dtype=torch.int32, device=self.config.device)
        self.ipts = torch.zeros(samples, units, dtype=torch.float32, device=self.config.device)

    def init_losses(self, batches:int) -> None:
        self.wh_loss = torch.zeros(batches, dtype=torch.float32, device=self.config.device)
        self.sv_loss = torch.zeros((batches, self.units), dtype=torch.float32, device=self.config.device)
        self.total_loss = torch.zeros(batches, dtype=torch.float32, device=self.config.device)

    def fortmat_outputs(self) -> None:
        outputs = {
            'spikes': self.spikes.detach().cpu().clone(),
            'ipts': self.ipts.detach().cpu().clone(),
            'wh_loss': self.wh_loss.detach().cpu().clone(),
            'sv_loss': self.sv_loss.detach().cpu().clone(),
            'total_loss': self.total_loss.detach().cpu().clone(),
            'wh_time_ms': self.time_wh_ms,
            'sv_time_ms': self.time_sv_ms,
            'sd_time_ms': self.time_sd_ms,
            'total_time_ms': self.time_wh_ms + self.time_sv_ms + self.time_sd_ms,
        }
        return outputs

    def run_decomp(self, emg_batch: torch.Tensor, batch_idx = Optional[int]) -> None:

                   
        t0 = time.time()
        emg_wh = self.whiten(emg_batch, batch_idx)
        self.time_wh_ms[batch_idx] = (time.time() - t0) * 1000
        
                           
        t0 = time.time()
        ipts = self.source_sep(emg_wh, batch_idx)
        self.time_sv_ms[batch_idx] = (time.time() - t0) * 1000
        
                         
        t0 = time.time()
        spikes = self.spike_det(ipts)
        self.time_sd_ms[batch_idx] = (time.time() - t0) * 1000

        return spikes, ipts

    def whiten(self, emg_batch: torch.Tensor, batch_idx: Optional[int]) -> torch.Tensor:

                         
        emg_wh = self._apply_whitening(emg_batch)

                                      
        if self.config.adapt_wh or self.config.compute_loss:
            self._recursive_update_wh_cov(emg_wh)

                      
        if self.config.compute_loss:       
                                                                               
            kl_div_est = self._kl_divergence()
            wh_loss = self._wh_loss(kl_div_est)
            self.wh_loss[batch_idx] = wh_loss.item()
            self.total_loss[batch_idx] += wh_loss.item() if ~torch.isnan(wh_loss) else 1e10

                                     
        if self.config.adapt_wh:
            self._update_whitening()

        return emg_wh
    
    def _apply_whitening(self, emg_batch: torch.Tensor) -> torch.Tensor:
        emg_wh = self.decomp.whitening @ emg_batch.T
        return emg_wh

    def _recursive_update_wh_cov(self, emg_batch: torch.Tensor) -> None:
        curr_cov_est = torch.cov(emg_batch)
        self.decomp.wh_cov_est = (1 - self.config.cov_alpha) * self.decomp.wh_cov_est + self.config.cov_alpha * curr_cov_est
    
    def _kl_divergence(self) -> torch.Tensor:
        logdet_wh_cov_est = torch.linalg.slogdet(self.decomp.wh_cov_est)[1]
        trace_cov = torch.trace(self.decomp.wh_cov_est)
        kl_div = 0.5 * (- logdet_wh_cov_est + trace_cov - self.decomp.n)
        return kl_div
    
    def _wh_loss(self, kl_div_est: torch.Tensor) -> torch.Tensor:
        return ((kl_div_est - self.decomp.kl_div_calib_mean)/self.decomp.kl_div_calib_std) ** 2
    
    def _update_whitening(self) -> None:
        grad_wh = self.decomp.wh_cov_est - self.decomp.I
        self.decomp.whitening -= self.config.wh_learning_rate * grad_wh @ self.decomp.whitening

    def source_sep(self, emg_wh: torch.Tensor, batch_idx: Optional[int]) -> torch.Tensor:
        
        ipts = self._apply_sep_vectors(emg_wh)

        if self.config.compute_loss or self.config.adapt_sv:
                                                                
            self.config.adapt_sd = False
            spikes = self.spike_det(ipts)
            self.config.adapt_sd = True

        if self.config.compute_loss:
                                       
            ipts_spikes = (ipts * spikes).sum(0) / (spikes.sum(0) + 1e-6)

                                               
            if self.config.contrast_fun == 'logcosh':
                contrast_est = torch.log(torch.cosh(ipts_spikes))
            elif self.config.contrast_fun == 'cube':
                contrast_est = ipts_spikes ** 3 / 6

                                                     
            contrast_est[contrast_est == 0] = torch.nan

            sv_loss = self._sv_loss(contrast_est)
            self.sv_loss[batch_idx] = sv_loss
            self.total_loss[batch_idx] += sv_loss.nansum()
            
        if self.config.adapt_sv:
                                                                                 
            self._update_sep_vectors(emg_wh, ipts, spikes)
            
        return ipts
    
    def _sv_loss(self, contrast_est: torch.Tensor) -> torch.Tensor:
        return ((contrast_est - self.decomp.contrast_calib_mean)/self.decomp.contrast_calib_std) ** 2
    
    def _apply_sep_vectors(self, emg_wh: torch.Tensor) -> torch.Tensor:
        ipts = self.decomp.sep_vectors @ emg_wh
        return ipts.T

    def _update_sep_vectors(self, emg_wh: torch.Tensor, ipts: torch.Tensor, spikes: torch.Tensor) -> None:
        sep_vectors_new = self.decomp.sep_vectors.clone()
        
        for unit in range(self.units):

                                           
            idxs = torch.nonzero(spikes[:,unit], as_tuple=True)[0]
            if len(idxs) == 0:
                continue
            
                                        
            ipts_spikes_unit = ipts[idxs, unit]
                                                                      

                                                               
            if self.config.contrast_fun == 'logcosh':
                g = torch.tanh(ipts_spikes_unit)
            elif self.config.contrast_fun == 'cube':
                g = ipts_spikes_unit ** 2 / 2

                                  
            sep_vectors_grad = (emg_wh[:, idxs] * g).mean(1)
            
            for i in range(self.config.sv_epochs):
                                                   
                sep_vectors_new[unit] += self.config.sv_learning_rate * sep_vectors_grad
                sep_vectors_new[unit] = self._normalise(sep_vectors_new[unit])
                
                                   
                lim = torch.abs(torch.abs((sep_vectors_new[unit] * self.decomp.sep_vectors[unit]).sum()) - 1)
                self.decomp.sep_vectors[unit] = sep_vectors_new[unit]

                                                                    
                if lim < self.config.sv_tol or i == self.config.sv_epochs - 1:
                    sep_vectors_new[unit] = self._orthonormalise(sep_vectors_new[unit], sep_vectors_new, unit)
                    break

    def _orthonormalise(self, w: torch.Tensor, W: torch.Tensor, j: int) -> torch.Tensor:
        w = self._gs_deflation(w, W, j)
        return self._normalise(w)

    def _gs_deflation(self, w: torch.Tensor, W: torch.Tensor, j: int) -> torch.Tensor:
        return w - torch.linalg.multi_dot([w, W[:j].T, W[:j]])

    def _normalise(self, w: torch.Tensor) -> torch.Tensor:
        return w / torch.sqrt((w**2).sum())

    def spike_det(self, ipts: torch.Tensor) -> torch.Tensor:

        ipts_np = ipts.detach().cpu().numpy()
        ipts2 = ipts_np * np.abs(ipts_np)
        spikes = np.zeros(ipts.shape).astype(int)
        min_height = self.decomp.base_centr / self.config.spike_height_mult
        max_height = self.config.spike_height_mult * self.decomp.spikes_centr

        for unit in range(self.units):

                                        
            peak_idxs, _ = signal.find_peaks(
                ipts2[:,unit], 
                distance = self.config.spike_dist, 
                height = [min_height[unit], max_height[unit]]
            )
            peak_vals = ipts2[peak_idxs,unit]

                                                              
            if len(peak_idxs) == 0:
                continue

                                
            peak_labels = peak_vals > self.decomp.height[unit]
            spikes[peak_idxs,unit] = peak_labels

                              
            if self.config.adapt_sd:
                spike_new_weight = peak_labels.sum()

                if np.any(peak_labels):
                    spike_cent_new = np.mean( peak_vals[peak_labels==1] )
                    self.decomp.spikes_centr[unit] = self._weighted_average(
                        spike_cent_new,
                        self.decomp.spikes_centr[unit],
                        spike_new_weight,
                        self.config.spike_prev_weight,
                        )
                if np.any(~peak_labels):
                    base_cent_new = np.mean( peak_vals[peak_labels==0] )
                    self.decomp.base_centr[unit] = self._weighted_average(
                        base_cent_new,
                        self.decomp.base_centr[unit],
                        spike_new_weight,
                        self.config.spike_prev_weight,
                        )

                               
                self.decomp.height[unit] = self.decomp.spikes_centr[unit] - (self.decomp.spikes_centr[unit] - self.decomp.base_centr[unit])/2  
        
        return torch.from_numpy(spikes).to(device=self.config.device, dtype=torch.int32)
    
    def _weighted_average(
            self,
            x_new: float, 
            x_old: float, 
            w_new: float, 
            w_old: float,
            ) -> float:
        return (w_old * x_old + w_new * x_new) / (w_old + w_new)

    def _check_batch(self,
        emg_batch: torch.Tensor,
        idx_labels: torch.Tensor
        ) -> Tuple[torch.Tensor, torch.Tensor]:
        if torch.any(idx_labels < self.config.ext_fact):
            emg_batch = emg_batch[self.config.ext_fact:]
            idx_labels = idx_labels[self.config.ext_fact:]
        return emg_batch, idx_labels

    def run(self) -> Tuple[torch.Tensor, torch.Tensor]:

                                                            
        dataset = DataLoader(self.data, batch_size=self.config.batch_size, shuffle=False, drop_last=False)

                                                        
        self.init_outputs(
            samples = len(self.data),
            units = self.decomp.sep_vectors.shape[0],
        )
        self.init_losses(len(dataset))
        self.init_exe_time(len(dataset))

                                      
        if self.config.save_params and self.save_path is not None:
            self.saver = H5PraramsBatchWriter(
                path = self.save_path,
                wh_shape = self.decomp.whitening.shape,
                sv_shape = self.decomp.sep_vectors.shape,
                sd_shape = self.decomp.spikes_centr.shape,
                batches = len(dataset),
                dtype = 'float32',
                )

                                                 
        for i, (emg_batch, idx_labels) in enumerate(dataset):
            i = torch.tensor(i, device=self.config.device)
            emg_batch, idx_labels = self._check_batch(emg_batch, idx_labels)
            if self.config.save_params:
                self.saver._append({
                    'whitening': self.decomp.whitening.cpu().numpy(),
                    'sep_vectors': self.decomp.sep_vectors.cpu().numpy(),
                    'base_centr': self.decomp.base_centr,
                    'spikes_centr': self.decomp.spikes_centr,
                })
            spikes, ipts = self.run_decomp(emg_batch, i)
            self.spikes[idx_labels, :] = spikes
            self.ipts[idx_labels, :] = ipts

                            
        outputs = self.fortmat_outputs()
        if self.config.save_params:
            self.saver._save(outputs)
        return outputs
    
    def run_optimisation(self,
            wh_lr: Optional[float] = None,
            cov_alpha: Optional[float] = None,
            sv_lr: Optional[float] = None,
        ) -> torch.Tensor:

                                               
        if wh_lr is not None:
            self.config.wh_learning_rate = wh_lr
        if cov_alpha is not None:
            self.config.cov_alpha = cov_alpha
        if sv_lr is not None:
            self.config.sv_learning_rate = sv_lr

                                                        
        self.decomp._reset_params()
        self.decomp.init_outputs(
            samples = self.data.emg_ext.shape[0],
            units = self.decomp.sep_vectors.shape[0],
        )

                                                            
        dataset = DataLoader(self.data, batch_size=self.config.batch_size, shuffle=False, drop_last=False)
        self.decomp.init_losses(len(dataset))
        self.decomp.init_exe_time(len(dataset))

                                                 
        for i, (emg_batch, idx_labels) in enumerate(dataset):
            emg_batch, idx_labels = self._check_batch(emg_batch, idx_labels)
            spikes, ipts = self.run_decomp(emg_batch, i)
            self.decomp.spikes[idx_labels, :] = spikes
            self.decomp.ipts[idx_labels, :] = ipts

                                
        tot_loss = 0
        if wh_lr is not None:
            tot_loss += self._compute_total_wh_loss()
        if sv_lr is not None:
            tot_loss += self._compute_total_sv_loss()

        return tot_loss
    
    def _compute_total_wh_loss(self) -> float:
        tot_wh_loss = -self.decomp.wh_loss.median()
        if torch.any(torch.isnan(self.decomp.wh_loss)):
            tot_wh_loss = -1e10
        return tot_wh_loss

    def _compute_total_sv_loss(self) -> float:
                                                                             
        tot_sv_loss = -self.decomp.sv_loss.nanmedian()
        return tot_sv_loss
