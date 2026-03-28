"""Configuration dataclass for adaptive decomposition routines."""


import yaml
from dataclasses import dataclass, field
from typing import Dict, Literal

@dataclass
class Config:
    
                        
    fs: int = 2048
    device: Literal['cpu','cuda','mps', None] = None

                              
    lowcut: float = 20                                                       
    highcut: float = 500                                                   
    powerline: bool = True                                      
    powerline_freq: float = 50                                   

                              
    ext_fact: int = 10                                            

                              
    batch_ms: int = 100                           
    adapt_wh: bool = True                                           
    adapt_sv: bool = True                                             
    adapt_sd: bool = True                                          
    compute_loss: bool = True                                                      
    save_params: bool = False                                                                
    
                    
    wh_learning_rate: float = 7e-3                                                   
    sv_learning_rate: float = 3e-3                                               

                                  
    sv_epochs: int = 1                                                                            
    sv_tol: float = 1e-4                                                                      
    contrast_fun: Literal['logcosh', 'cube'] = 'logcosh'                                                              

                          
    cov_alpha: float = 0.1                                                            

                                 
    spike_height_mult: int = 3                                                                  
    spike_prev_weight: int = 5                                                              
    spike_dist_ms: int = 20                                                                          
    spike_dist: int = field(init=False)                                              

    def __post_init__(self) -> None:
        self.spike_dist = int(self.spike_dist_ms * self.fs / 1000)
        self.batch_size = int(self.batch_ms * self.fs / 1000)

def load_yaml(file_path: str) -> Dict:
    with open(file_path, "r") as file:
        return yaml.safe_load(file)

def load_config(
    defaults_path="configs/model_configs/default_neuromotion.yml", 
    wandb_config=None
    ) -> Config:
                         
    defaults = load_yaml(defaults_path)
                                       
    if wandb_config:
        for key, value in wandb_config.items():
            if key in defaults:
                defaults[key] = value

    return Config(**defaults)
