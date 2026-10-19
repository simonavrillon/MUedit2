"""Tests for the kinds of auxiliary channel (:mod:`muedit.io.aux_channels`)."""

from __future__ import annotations

import pytest

from muedit.io.aux_channels import aux_kinds, find_target_channel


@pytest.mark.parametrize(
    ("names", "expected"),
    [
        (["Acc X", "Feedback - Target", "Feedback - Path"], 1),
        (["requested force"], 0),
        (["Trapezoidal track - Performed Path", "Trapezoidal track - Original Path"], 1),
        (["Actual target"], None),
        ([], None),
    ],
)
def test_find_target_channel(names: list[str], expected: int | None) -> None:
    assert find_target_channel(names) == expected


def test_aux_kinds_from_the_loader_and_the_names() -> None:
    names = ["Force - Original Path", "Force - Performed Path", "Torque", "TTL1"]
    kinds = aux_kinds(names, [False, False, False, True])
    assert kinds == ["target", "performed", "analog", "digital"]
    assert aux_kinds(names) == ["target", "performed", "analog", "analog"]


def test_a_trigger_line_is_never_the_target() -> None:
    names = ["Target TTL", "Force - Original Path"]
    assert find_target_channel(names) == 0
    assert find_target_channel(names, aux_kinds(names, [True, False])) == 1
