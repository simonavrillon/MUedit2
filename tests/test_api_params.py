"""``DecomposePayload`` validates the run parameters at the API boundary."""

from __future__ import annotations

from typing import Any, get_args

import pytest
from pydantic import ValidationError

from muedit.api.schemas import DecomposePayload
from muedit.decomp.types import ContrastFunc, DecompositionParameters


def _payload(**params: Any) -> DecomposePayload:
    return DecomposePayload.model_validate({"upload_token": "t", "params": params})


@pytest.mark.parametrize("name", get_args(ContrastFunc))
def test_known_contrast_func_accepted(name: str) -> None:
    assert _payload(contrast_func=name).params.contrast_func == name


def test_unknown_contrast_func_rejected() -> None:
    with pytest.raises(ValidationError) as exc_info:
        _payload(contrast_func="skwe")
    assert exc_info.value.errors()[0]["loc"] == ("params", "contrast_func")


def test_frontend_values_are_coerced() -> None:
    params = _payload(niter=5, covfilter=1, initialization=0, unknown_key=3).params
    assert isinstance(params, DecompositionParameters)
    assert (params.niter, params.covfilter, params.initialization) == (5, True, False)


def test_missing_params_are_the_defaults() -> None:
    payload = DecomposePayload.model_validate({"upload_token": "t"})
    assert payload.params == DecompositionParameters()


@pytest.mark.parametrize(
    "params",
    [{"adapt_batch_ms": 0}, {"use_adaptive": True, "edges_sec": 0.05, "adapt_batch_ms": 100}],
    ids=["non-positive", "longer-than-edges"],
)
def test_adaptive_batch_must_fit(params: dict[str, Any]) -> None:
    with pytest.raises(ValidationError, match="adapt_batch_ms"):
        _payload(**params)
