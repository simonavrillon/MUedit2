"""Plotting helpers for adaptive decomposition diagnostics."""


import numpy as np
from typing import Optional
import matplotlib.pyplot as plt
from mpl_toolkits.axes_grid1 import make_axes_locatable
import matplotlib.colors as colors

def plot_whitening_comp(
    wh1: np.ndarray,
    wh2: np.ndarray,
    palette: Optional[str] = 'magma',
    ax: Optional[plt.Axes] = None
    ) -> plt.Axes:

    if ax is None:
        fig, ax = plt.subplots(1, 3, figsize=(12, 5), layout='tight')

    vmin = np.min([wh1, wh2])
    vmax = np.max([wh1, wh2])

    im0 = ax[0].imshow(wh1, cmap=palette, vmin=vmin, vmax=vmax)
    ax[0].set(title='Whitening 1', xticks=[], yticks=[])
    divider0 = make_axes_locatable(ax[0])
    cax0 = divider0.append_axes("right", size="5%", pad=0.05)

    im1 = ax[1].imshow(wh2, cmap=palette, vmin=vmin, vmax=vmax)
    ax[1].set(title='Whitening 2', xticks=[], yticks=[])
    divider1 = make_axes_locatable(ax[1])
    cax1 = divider1.append_axes("right", size="5%", pad=0.05)

    im2 = ax[2].imshow(wh1 - wh2, cmap='coolwarm', norm=colors.CenteredNorm())
    ax[2].set(title='Difference', xticks=[], yticks=[])
    divider2 = make_axes_locatable(ax[2])
    cax2 = divider2.append_axes("right", size="5%", pad=0.05)

    plt.colorbar(im0, cax=cax0, orientation='vertical')
    plt.colorbar(im1, cax=cax1, orientation='vertical')
    plt.colorbar(im2, cax=cax2, orientation='vertical')
    
    return ax

def plot_sep_vectors_comp(
    sv1: np.ndarray,
    sv2: np.ndarray,
    palette: Optional[str] = 'magma',
    ax: Optional[plt.Axes] = None
    ) -> plt.Axes:

    if ax is None:
        fig, ax = plt.subplots(3, 1, figsize=(12, 5), layout='tight')

    vmin = np.amin([sv1, sv2])
    vmax = np.amax([sv1, sv2])

    ax[0].imshow(sv1, cmap=palette, vmin=vmin, vmax=vmax, aspect='auto')
    ax[0].set(title='Separation vectors 1', xticks=[], yticks=[])
    divider0 = make_axes_locatable(ax[0])
    cax0 = divider0.append_axes("right", size="5%", pad=0.05)

    im = ax[1].imshow(sv2, cmap=palette, vmin=vmin, vmax=vmax, aspect='auto')
    ax[1].set(title='Separation vectors 2', xticks=[], yticks=[])
    divider1 = make_axes_locatable(ax[1])
    cax1 = divider1.append_axes("right", size="5%", pad=0.05)

    im2 = ax[2].imshow(sv1 - sv2, cmap='coolwarm', aspect='auto', norm=colors.CenteredNorm())
    ax[2].set(title='Difference', xticks=[], yticks=[])
    divider2 = make_axes_locatable(ax[2])
    cax2 = divider2.append_axes("right", size="5%", pad=0.05)

    plt.colorbar(im, cax=cax0, orientation='vertical')
    plt.colorbar(im, cax=cax1, orientation='vertical')
    plt.colorbar(im2, cax=cax2, orientation='vertical')
    
    return ax

def plot_sep_vectors_diff(
    sv: np.ndarray,
    ch_map: Optional[np.ndarray],
    palette: Optional[str] = 'coolwarm',
    ax: Optional[plt.Axes] = None
    ) -> plt.Axes:
    
    if ax is None:
        units = sv.shape[0]
        cols = 3
        rows = int(units/cols)
        fig, ax = plt.subplots(rows, cols, figsize=(12, 2 * rows), layout='tight')
        ax = np.ravel(ax)

    if ch_map is None:
        ch_map = np.arange(sv.shape[1])

    v = np.amax(np.abs(sv))

    for unit in range(units):

        im = ax[unit].imshow(sv[unit, ch_map], cmap=palette, aspect='auto', vmin=-v, vmax=v)
        ax[unit].set(title=f'Unit {unit}', xticks=[], yticks=[])
        divider = make_axes_locatable(ax[unit])
        cax = divider.append_axes("right", size="5%", pad=0.05)
        plt.colorbar(im, cax=cax, orientation='vertical')
    
    return ax
