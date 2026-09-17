"""``build_params`` rejects option values outside the typed vocabulary."""

from __future__ import annotations

import json
from typing import get_args

import pytest
from fastapi import HTTPException

from muedit.api.common import build_params
from muedit.decomp.types import ContrastFunc


@pytest.mark.parametrize("name", get_args(ContrastFunc))
def test_known_contrast_func_accepted(name: str) -> None:
    assert build_params(json.dumps({"contrast_func": name})).contrast_func == name


def test_unknown_contrast_func_rejected() -> None:
    with pytest.raises(HTTPException) as exc_info:
        build_params(json.dumps({"contrast_func": "skwe"}))
    assert exc_info.value.status_code == 400
    assert exc_info.value.detail["field"] == "contrast_func"
