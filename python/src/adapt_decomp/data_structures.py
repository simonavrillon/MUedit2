"""Torch datasets and tensor structures used by adaptive decomposition."""


from typing import Tuple, Optional
import numpy as np
import torch
from torch.utils.data import Dataset, DataLoader
from adapt_decomp.preprocessing import bandpass_filter, remove_powerline
from adapt_decomp.config import Config

def _extend_data_v(data, ext_fact, device=None):
    if device is None:
        device = data.device

    samples, chs = data.shape
    data_ext = torch.zeros((samples, int(chs * ext_fact))).to(device=device)

    for i in range(ext_fact):
        data_ext[i:samples, chs*i: chs*(i+1)] = data[0:(samples-i),:]
    return data_ext

class Data(Dataset):

    def __init__(self,
        emg: torch.Tensor,
        preprocess: Optional[bool] = True,
        config: Optional[Config] = Config
        ) -> None:

                                 
        if preprocess:
            emg, offset = self.preprocess_emg(emg, config)
        else:
            offset = emg.mean(axis=0)
        self.extend_data(emg, config.ext_fact)

                                                              
        self.emg_ext = self.emg_ext.to(device=config.device, dtype=torch.float32)
        self.labels = torch.arange(emg.shape[0]).to(device=config.device)
        self.offset = offset.repeat(config.ext_fact).to(device=config.device, dtype=torch.float32)

    def __len__(self) -> int:
        return self.emg_ext.shape[0]
    
    def __getitem__(self, idx: int) -> torch.Tensor:
        return self.emg_ext[idx,:], self.labels[idx]

    def preprocess_emg(
        self,
        emg: torch.Tensor,
        config: Config
    ) -> Tuple[torch.Tensor, torch.Tensor]:

                             
        emg = bandpass_filter(emg.numpy(), config.fs, cutoff=[config.lowcut, config.highcut], filtfilt=False)

                                
        if config.powerline:
            emg = remove_powerline(emg, config.fs, cutoff=config.powerline_freq, filtfilt=False)

                          
        offset = np.mean(emg, axis=0)
        emg -= offset

        return torch.from_numpy(emg), torch.from_numpy(offset)
    
    def extend_data(self, emg: torch.Tensor, ext_fact: int) -> None:
        self.emg_ext = _extend_data_v(emg, ext_fact)
    

class Decomposition:

    def __init__(self,
        whitening: torch.Tensor,
        sep_vectors: torch.Tensor,
        base_centr: torch.Tensor,
        spikes_centr: torch.Tensor,
        emg_calib: torch.Tensor,
        ipts_calib: torch.Tensor,
        spikes_calib: torch.Tensor,
        config: Optional[Config] = Config,
        ) -> None:

                                      
        self.device = config.device
        self.ext_fact = config.ext_fact
        self.batch_size = config.batch_size
        self.cov_alpha = config.cov_alpha
        self.contrast_fun = config.contrast_fun

                                                                
        self.whitening = whitening.to(dtype=torch.float32, device=self.device)
        self.sep_vectors = sep_vectors.to(dtype=torch.float32, device=self.device)
                                                            
        self.spikes_centr = spikes_centr.cpu().numpy()
        self.base_centr = base_centr.cpu().numpy()

        self.emg_calib = emg_calib.to(dtype=torch.float32)
        self.ipts_calib = ipts_calib.to(dtype=torch.float32)
        self.spikes_calib = spikes_calib.to(dtype=torch.int32)

                                                                 
        self.init_wh_update()

                                                                         
        self.init_sv_update()
    
                                                                       
        self.init_sd_update()

    def init_wh_update(self) -> None:

                             
        emg_ext = _extend_data_v(self.emg_calib, self.ext_fact)

                                       
        wh_emg_calib = emg_ext @ self.whitening.cpu().T
        self.wh_cov_est = torch.cov(wh_emg_calib.T).to(self.device)

                                                                          
        self.n = self.whitening.shape[0]
        self.I = torch.eye(self.n, dtype=torch.float32, device=self.device)
        calib_dataset = DataLoader(wh_emg_calib, batch_size=self.batch_size, shuffle=False, drop_last=True)

        wh_cov_calib = self.wh_cov_est.clone().cpu()
        kl_div_calib = torch.zeros(len(calib_dataset), dtype=torch.float32, device=self.device)

        for i, wh_emg_batch in enumerate(calib_dataset):
            i = torch.tensor(i, device=self.device)

                                           
            wh_cov_batch = torch.cov(wh_emg_batch.T)
            wh_cov_calib = (1 - self.cov_alpha) * wh_cov_calib + self.cov_alpha * wh_cov_batch

                                                       
            logdet_wh_cov_calib = torch.linalg.slogdet(wh_cov_calib)[1]
            trace_cov_calib = torch.trace(wh_cov_calib)

            kl_div_calib[i] = 0.5 * (trace_cov_calib - self.n - logdet_wh_cov_calib)

                                                       
        self.kl_div_calib_mean = torch.mean(kl_div_calib[1:-1]).to(self.device)
        self.kl_div_calib_std = torch.std(kl_div_calib[1:-1]).to(self.device)

    def init_sv_update(self) -> None:

        units = self.ipts_calib.shape[1]
        self.contrast_calib_mean = torch.zeros(units).to(device=self.device)
        self.contrast_calib_std = torch.zeros(units).to(device=self.device)
        for unit in range(units):
            ipts_spike = self.ipts_calib[self.spikes_calib[:,unit]==1, unit]
            if self.contrast_fun == 'logcosh':
                self.contrast_calib_mean[unit] = torch.log(torch.cosh(ipts_spike)).mean().to(self.device)
                self.contrast_calib_std[unit] = torch.log(torch.cosh(ipts_spike)).std().to(self.device)
            elif self.contrast_fun == 'cube':
                self.contrast_calib_mean[unit] = torch.mean(ipts_spike ** 3 / 6).to(self.device)
                self.contrast_calib_std[unit] = torch.std(ipts_spike ** 3 / 6).to(self.device)
            else:
                raise NotImplementedError(f"Not implemented contrast function: {self.contrast_fun}, chose 'logcosh' or 'cube'")

    def init_sd_update(self) -> None:
        self.height = self.spikes_centr - (self.spikes_centr - self.base_centr)/2
