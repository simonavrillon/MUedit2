"""Signal preprocessing utilities used by adaptive decomposition package."""


from typing import Optional
import numpy as np
from scipy import signal


def bandpass_filter(
    data: np.ndarray,
    fs: Optional[int] = 2048,
    cutoff: Optional[list] = None,
    order: Optional[int] = 4,
    filtfilt: Optional[bool] = True,
    ) -> np.ndarray:

                               
    if cutoff is None:
        cutoff = [20, 500]

                   
    sos = signal.butter(order, cutoff, btype='band', fs=fs, output='sos')

                  
    if filtfilt:
        out = signal.sosfiltfilt(sos, data)
    else:
        out = signal.sosfilt(sos, data)

    return out


def highpass_filter(
    data: np.ndarray,
    fs: Optional[int] = 2048,
    cutoff: Optional[float] = 20,
    order: Optional[int] = 2,
    filtfilt: Optional[bool] = True,
    ) -> np.ndarray:

                   
    sos = signal.butter(order, cutoff, btype='high', fs=fs, output='sos')

                  
    if filtfilt:
        out = signal.sosfiltfilt(sos, data)
    else:
        out = signal.sosfilt(sos, data)

    return out


def lowpass_filter(
    data: np.ndarray,
    fs: Optional[int] = 2048,
    cutoff: Optional[float] = 500,
    order: Optional[int] = 2,
    filtfilt: Optional[bool] = True,
    ) -> np.ndarray:

                   
    sos = signal.butter(order, cutoff, btype='low', fs=fs, output='sos')

                  
    if filtfilt:
        out = signal.sosfiltfilt(sos, data)
    else:
        out = signal.sosfilt(sos, data)

    return out

 
def remove_powerline(
    data: np.ndarray,
    fs: Optional[int] = 2048,
    cutoff: Optional[float] = 50,
    width: Optional[float] = 1,
    order: Optional[int] = 2,
    filtfilt: Optional[bool] = True,
    ) -> np.ndarray:


                  
    cutoff = [cutoff - width/2, cutoff + width/2]

                   
    sos = signal.butter(order, cutoff, btype='bandstop', fs=fs, output='sos')

                  
    if filtfilt:
        out = signal.sosfiltfilt(sos, data)
    else:
        out = signal.sosfilt(sos, data)

    return out
