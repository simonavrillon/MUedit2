"""Kinds of auxiliary channel: trigger lines, the force target, the performed trace, other analog."""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Literal

# What an auxiliary channel carries: a trigger line, the force target shown to the
# participant, the participant's own trace, or any other analog signal.
AuxKind = Literal["analog", "digital", "target", "performed"]

# OTB4 feedback tracks name the target "<track> - Original Path" and the
# participant's own trace "<track> - Performed Path".
_TARGET_NAME = re.compile(r"target|reference|requested|original", re.IGNORECASE)
_PERFORMED_NAME = re.compile(r"performed|actual", re.IGNORECASE)


def aux_kinds(names: list[str], digital: list[bool] | None = None) -> list[AuxKind]:
    """Kind of each auxiliary channel: digital when ``digital`` says so, else target or performed by name."""
    kinds: list[AuxKind] = []
    for idx, name in enumerate(names):
        if digital is not None and idx < len(digital) and digital[idx]:
            kinds.append("digital")
        elif _PERFORMED_NAME.search(name):
            kinds.append("performed")
        elif _TARGET_NAME.search(name):
            kinds.append("target")
        else:
            kinds.append("analog")
    return kinds


def find_target_channel(names: list[str], kinds: Sequence[str] | None = None) -> int | None:
    """Index of the first auxiliary channel that is a target, or ``None``."""
    if kinds is None or len(kinds) != len(names):
        kinds = list(aux_kinds(names))
    return next((idx for idx, kind in enumerate(kinds) if kind == "target"), None)
