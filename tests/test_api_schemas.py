"""The request models (``schemas``): what the API refuses before any work starts."""

from __future__ import annotations

import re
from typing import Any, get_args

import pytest
from pydantic import ValidationError

from muedit.api.schemas import DecomposePayload
from muedit.decomp.types import ContrastFunc, DecompositionParameters


def _payload(**params: Any) -> DecomposePayload:
    return DecomposePayload.model_validate({"upload_token": "t", "params": params})


class TestContrastFunc:
    """Only the contrast functions the decomposition knows are taken."""

    @pytest.mark.parametrize("name", get_args(ContrastFunc))
    def test_known_contrast_func_accepted(self, name: str) -> None:
        assert _payload(contrast_func=name).params.contrast_func == name

    def test_unknown_contrast_func_rejected(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            _payload(contrast_func="skwe")
        assert exc_info.value.errors()[0]["loc"] == ("params", "contrast_func")


class TestDecomposeParams:
    """The run parameters: coerced from the page's values, defaulted, and range-checked."""

    def test_frontend_values_are_coerced(self) -> None:
        params = _payload(niter=5, covfilter=1, random_init=0).params
        assert isinstance(params, DecompositionParameters)
        assert (params.niter, params.covfilter, params.random_init) == (5, True, False)

    def test_unknown_params_rejected(self) -> None:
        with pytest.raises(ValidationError, match="unknown parameters: initialization, nitre"):
            _payload(nitre=5, initialization=0)

    def test_missing_params_are_the_defaults(self) -> None:
        payload = DecomposePayload.model_validate({"upload_token": "t"})
        assert payload.params == DecompositionParameters()

    @pytest.mark.parametrize(
        ("params", "field"),
        [
            ({"niter": 0}, "niter"),
            ({"nwindows": 0}, "nwindows"),
            ({"peel_off_win": 0}, "peel_off_win"),
            ({"adapt_batch_ms": 0}, "adapt_batch_ms"),
            ({"use_adaptive": True, "edges_sec": 0.05, "adapt_batch_ms": 100}, "adapt_batch_ms"),
            ({"adapt_wh_learning_rate": -1e-3}, "adapt_wh_learning_rate"),
            ({"adapt_sv_learning_rate": 0}, "adapt_sv_learning_rate"),
            ({"adapt_cov_alpha": 0}, "adapt_cov_alpha"),
            ({"adapt_cov_alpha": 1.5}, "adapt_cov_alpha"),
            ({"adapt_spike_prev_weight": -1}, "adapt_spike_prev_weight"),
        ],
    )
    def test_out_of_range_params_rejected(self, params: dict[str, Any], field: str) -> None:
        with pytest.raises(ValidationError, match=field) as exc_info:
            _payload(**params)
        assert exc_info.value.errors()[0]["loc"] == ("params",)


class TestRois:
    """Each analysis window holds samples."""

    @pytest.mark.parametrize(
        ("span", "message"),
        [
            ({"start": 5, "end": 5}, "end (5) must be greater than start (5)"),
            ({"start": 9, "end": 2}, "end (2) must be greater than start (9)"),
            ({"start": -1, "end": 2}, "greater than or equal to 0"),
        ],
        ids=["empty", "reversed", "negative"],
    )
    def test_a_window_must_hold_samples(self, span: dict[str, int], message: str) -> None:
        with pytest.raises(ValidationError, match=re.escape(message)) as exc_info:
            DecomposePayload.model_validate({"upload_token": "t", "rois": [span]})
        assert exc_info.value.errors()[0]["loc"][:2] == ("rois", 0)
