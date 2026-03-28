"""HDF5 persistence helpers for adaptive decomposition parameters/results."""


import h5py
import os
import numpy as np
from pathlib import Path
from typing import Dict, Tuple, Literal, Union
from torch.utils.data import Dataset

class H5PraramsBatchWriter:
    
    def __init__(self, 
        path: Union[str, Path],
        wh_shape: Tuple,
        sv_shape: Tuple,
        sd_shape: Tuple,
        batches: int = None,
        dtype: str = 'float32', 
        compression: Literal['gzip', None] = None
    ) -> None:
        self.path = path
        self.shapes = {
            'whitening': wh_shape,
            'sep_vectors': sv_shape,
            'base_centr': sd_shape,
            'spikes_centr': sd_shape
        }
        self.batches = batches
        self.dtype = dtype
        self.compression = compression
        
        if not os.path.exists(self.path):
            self._init_file()

    def _init_file(self) -> None:
        with h5py.File(self.path, 'w') as f:
            for key in ['whitening', 'sep_vectors', 'base_centr', 'spikes_centr']: 
                f.create_dataset(
                    key,
                    shape=(0,) + self.shapes[key],
                    maxshape=(self.batches,) + self.shapes[key],
                    dtype=self.dtype,
                    chunks=True,
                    compression=self.compression
                )
    
    def _append(self, batch_data:Dict) -> None:
        with h5py.File(self.path, 'a') as f:
            for k, v in batch_data.items(): 
                f[k].resize(f[k].shape[0] + 1, axis=0)
                f[k][-1:] = np.asarray(v, dtype=self.dtype)

    def _append_batch(self, batch_data:Dict) -> None:
        with h5py.File(self.path, 'a') as f:
            for k, v in batch_data.items(): 
                batch_len = v.shape[0]
                fk_len = f[k].shape[0]
                f[k].resize(fk_len + batch_len, axis=0)
                f[k][fk_len:fk_len + batch_len] = np.asarray(v, dtype=self.dtype)

    def _save(self, data:Dict) -> None:
        with h5py.File(self.path, 'a') as f:
            for k, v in data.items():
                f.create_dataset(k, data=v)

    def _load(self) -> Dict:
        data = {}
        with h5py.File(self.path, 'r') as f:
            for key in f.keys():
                data[key] = f[key][:]
        return data

class H5PraramsDataset(Dataset):
    
    def __init__(self, path:str) -> None:
        pass

    def __len__(self) -> int:
        pass

    def __getitem__(self, idx:int) -> Dict:
        pass

def save_output(path:str, outputs:Dict) -> None:
    with h5py.File(path, 'w') as f:
        for key in outputs:
            f.create_dataset(key, data=outputs[key])

def load_output(path:str) -> Dict:
    outputs = {}
    with h5py.File(path, 'r') as f:
        for key in f.keys():
            outputs[key] = f[key][:]
    return outputs
