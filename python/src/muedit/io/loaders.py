"""Signal file loaders for .mat/.otb/.rhd formats used by MUedit."""

from __future__ import annotations

from muedit.io._bids_reader import load_bids_signal
from muedit.io._intan import load_intan
from muedit.io._mat import load_mat
from muedit.io._otb import load_otb4, load_otb_plus

__all__ = ["load_mat", "load_otb_plus", "load_otb4", "load_bids_signal", "load_intan"]
