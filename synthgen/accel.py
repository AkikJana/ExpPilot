"""Compute-acceleration helpers: cuDF-accelerated pandas and GPU array module.

``cudf.pandas`` must be activated *before* pandas is imported, so we centralize
the pandas import here and activate cuDF on first request when a GPU is present.
"""
from __future__ import annotations

_CUDF_ACTIVATED = False


def get_pandas(use_cudf: bool):
    """Return the pandas module, cuDF-accelerated when requested and possible.

    Falls back silently to stock pandas if cuDF/GPU are unavailable.
    """
    global _CUDF_ACTIVATED
    if use_cudf and not _CUDF_ACTIVATED:
        try:
            import cudf.pandas  # noqa: F401

            cudf.pandas.install()
            _CUDF_ACTIVATED = True
        except Exception:
            pass
    import pandas as pd

    return pd


def get_array_module(prefer_gpu: bool):
    """Return (name, module) for array ops: cupy on GPU if available, else numpy.

    numpy generation is already fully vectorized; cupy moves RNG + arithmetic onto
    the GPU for very large row counts when RAPIDS/cupy is installed.
    """
    if prefer_gpu:
        try:
            import cupy as cp  # type: ignore

            return "cupy", cp
        except Exception:
            pass
    import numpy as np

    return "numpy", np
