"""Dataset loaders and adapters for adaptive decomposition experiments."""


import h5py
import torch
from scipy.io import loadmat
from typing import Dict
from adapt_decomp.utils import firings_to_spikes

def load_neuromotion(path_file:str) -> Dict:

    data = dict.fromkeys([
        'emg', 'rms', 'ch_map', 'ch_cols', 'bad_ch', 
        'fs', 'timestamps', 'angle_profile', 'force_profile', 'staircase_phases', 
        'spikes', 'muaps', 'muap_muscle_labels', 'muap_angle_labels', 
        'paired_units', 'roa_0deg', 'lags_0deg'
    ])

    with h5py.File(path_file, 'r') as h5:

        for key in data.keys():
            if key in ['staircase_phases', 'paired_units']:                   
                data[key] = dict.fromkeys( h5[key].keys() )
                for subkey in h5[key].keys():
                    data[key][subkey] = h5[key][subkey][()]
            else:
                data[key] = h5[key][()]

    return data

def load_example(path_emg:str, path_decomp:str, preprocess:bool) -> Dict:
               
    sim_data = load_neuromotion(path_emg)

                        
    decomp = loadmat(path_decomp, simplify_cells=True)
    decomp['spikes'] = firings_to_spikes(decomp['firings'], decomp['IPTs'], matlab_index=True)

                 
    data = {
        'emg': torch.from_numpy(sim_data['emg']),
        'timestamps': torch.from_numpy(sim_data['timestamps']),
        'fs': int(sim_data['fs']),

        'force_profile': torch.from_numpy(sim_data['force_profile']),
        'angle_profile': torch.from_numpy(sim_data['angle_profile']),
        'ch_map': torch.from_numpy(sim_data['ch_map']),

        'whitening': torch.from_numpy(decomp['WH']),
        'sep_vectors': torch.from_numpy(decomp['BRT'].T),
        'base_centr': torch.from_numpy(decomp['N_CENT']),
        'spikes_centr': torch.from_numpy(decomp['SIG_CENT']),
        'ext_fact': int(decomp['EXT_FACT']),

        'emg_calib': torch.from_numpy(decomp['EMG'].T),
        'ipts_calib': torch.from_numpy(decomp['IPTs'].T),
        'spikes_calib': torch.from_numpy(decomp['spikes'].T),      

        'spikes_gt': torch.from_numpy(sim_data['spikes']), 

        'preprocess': preprocess,   
    }
    return data


def load_data(data_config:Dict) -> Dict:
    if data_config['loader'] == 'load_example':
        data = load_example(
            data_config['path_emg'],
            data_config['path_decomp'],
            data_config['preprocess']
        )
    else:
        raise ValueError(f"Unknown data loader: {data_config['loader']}")
    return data
