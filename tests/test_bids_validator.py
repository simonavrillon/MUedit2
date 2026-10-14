"""The official BIDS validator on a dataset MUedit writes; skipped when Deno is not installed."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from muedit.decomp.preprocess import _target_segments
from muedit.io.bids import (
    export_bids_emg,
    export_bids_events,
    export_bids_mu_derivatives,
    write_bids_dataset_description,
)
from muedit.models import SignalImport
from muedit.signal.grid import format_hdemg_signal

# Pinned so a validator release cannot change the outcome unnoticed (latest on JSR, 2026-10-13).
VALIDATOR = "jsr:@bids/validator@3.0.2"

pytestmark = pytest.mark.skipif(shutil.which("deno") is None, reason="deno is not installed")

FSAMP = 2048.0
GRIDS = ["GR08MM1305", "HD08MM1305"]


def _recording() -> SignalImport:
    n = int(24 * FSAMP)
    t = np.arange(n) / FSAMP
    target = 30.0 * (np.clip((t - 2) / 5, 0, 1) - np.clip((t - 17) / 5, 0, 1))
    trigger = ((t > 1.0) & (t < 1.1)).astype(np.float32)
    n_emg = sum(len(mask) for mask in format_hdemg_signal(GRIDS)[2])
    return SignalImport.build(
        data=np.random.default_rng(0).normal(size=(n_emg, n)).astype(np.float32),
        fsamp=FSAMP,
        gridname=GRIDS,
        muscle=["tibialis anterior", "tibialis anterior"],
        auxiliary=np.vstack([target, trigger]).astype(np.float32),
        auxiliaryname=["Force - Original Path", "TTL1"],
        metadata={"aux_units": ["%MVC", "V"], "aux_kinds": ["target", "digital"]},
    )


def _validate(root: Path) -> dict[str, Any]:
    done = subprocess.run(
        ["deno", "run", "-ERN", VALIDATOR, str(root), "--format", "json"],
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )
    try:
        result: dict[str, Any] = json.loads(done.stdout)
    except json.JSONDecodeError:
        pytest.fail(f"validator gave no JSON (exit {done.returncode}): {done.stderr[-2000:]}")
    return result


def test_a_muedit_export_passes_the_validator(tmp_path: Path) -> None:
    sig = _recording()
    coordinates, ied, discard, _ = format_hdemg_signal(GRIDS)
    n_emg = sig.data.shape[0]
    out = export_bids_emg(
        sig.data,
        FSAMP,
        GRIDS,
        coordinates,
        discard,
        tmp_path,
        ied=ied,
        subject="01",
        session="1",
        task="trapezoid",
        powerline_freq=50.0,
        placement_scheme_description="Grids centred on the muscle belly, found by palpation.",
        reference_location="Wet band around the ankle",
        ground_location="Wet band around the wrist",
        target_muscle=sig.muscle,
        manufacturer="OT Bioelettronica",
        manufacturers_model_name="Quattrocento",
        task_description="Trapezoidal isometric contraction at 30 % MVC.",
        gain=[150.0] * n_emg,
        low_cutoff=[10.0] * n_emg,
        high_cutoff=[500.0] * n_emg,
        aux_data=sig.auxiliary,
        aux_names=sig.auxiliaryname,
        aux_units=sig.metadata["aux_units"],
        aux_kinds=sig.metadata["aux_kinds"],
    )
    export_bids_events(
        out["edf"], FSAMP, aux_data=sig.auxiliary, target_segments=_target_segments(sig, 0)
    )
    write_bids_dataset_description(tmp_path, subject="01", age=30, sex="M", handedness="right")
    export_bids_mu_derivatives(
        [[4096, 6144], [5000]],
        FSAMP,
        tmp_path,
        out["edf"].name.removesuffix("_emg.edf"),
        mu_grid_index=[0, 1],
    )

    issues = _validate(tmp_path)["issues"]["issues"]
    errors = [issue for issue in issues if issue.get("severity") == "error"]
    assert not errors, json.dumps(errors[:10], indent=1)
    codes = {issue.get("code") for issue in issues}
    assert "EMG_CHANNEL_COUNT_MISMATCH" not in codes
