"""File I/O sub-package: loaders for .mat, .otb+, .otb4 formats."""

from muedit.io.factory import LoaderFactory, get_loader, register_loader, supported_extensions
from muedit.io.loaders import load_mat, load_otb4, load_otb_plus

__all__ = [
    "LoaderFactory",
    "get_loader",
    "register_loader",
    "supported_extensions",
    "load_mat",
    "load_otb4",
    "load_otb_plus",
]
