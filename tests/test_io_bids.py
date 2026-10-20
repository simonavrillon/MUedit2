"""Importing a BIDS EMG recording (EDF/BDF and its sidecars): MUedit's own and other tools'."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from muedit.errors import SignalLoadError
from muedit.io import bids, load_signal
from muedit.io.bids import (
    BidsFieldError,
    bids_emg_fields,
    check_run_export,
    dataset_root,
    export_bids_emg,
    form_over_loader_meta,
    read_bids_emg_grid,
    run_decomp_path,
    write_participant,
)
from muedit.signal import grid
from muedit.signal.grid import format_hdemg_signal
from muedit.storage import RamStore
from tests._io_files import (
    MUNITQUEST_GRIDS,
    MUNITQUEST_SUB01,
    MUNITQUEST_SUB02,
    Expected,
    Write,
    check_signal,
    munitquest_trace,
    tsv_rows,
    write_munitquest,
)

FSAMP = 2048.0
N_SAMPLES = 22 * 2048 + 700  # a ragged last data record: the reader cuts it at RecordingDuration
MUNITQUEST_SAMPLES = 40 * 2048


@pytest.fixture(autouse=True)
def _tutorial_grids(monkeypatch: pytest.MonkeyPatch) -> None:
    for key, spec in MUNITQUEST_GRIDS.items():
        monkeypatch.setitem(grid._GRID_CATALOG, key, spec)


def _muedit_signals() -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(0)
    t = np.arange(N_SAMPLES) / FSAMP
    aux = np.vstack([np.clip(t / 5, 0, 1) * 30, (t % 4 < 0.1).astype(float)])
    return rng.normal(size=(64, N_SAMPLES)) * 0.05, aux


def _muedit_dataset(tmp_path: Path) -> Path:
    """A BDF MUedit exported, with MUniverse's task phases in its events.tsv.

    The dataset sits below a folder named ``sub-lab``, which is not its subject folder.
    """
    data, aux = _muedit_signals()
    coordinates, ied, discard, _ = format_hdemg_signal(["GR08MM1305"])
    out = export_bids_emg(
        data,
        FSAMP,
        ["GR08MM1305"],
        coordinates,
        discard,
        tmp_path / "sub-lab" / "study",
        ied=ied,
        subject="01",
        task="ramp",
        file_format="bdf",
        start_time="2026-03-26T14:31:26",
        units="mV",
        target_muscle="tibialis anterior",
        placement_scheme_description="Grid over the muscle belly, located by palpation.",
        aux_data=aux,
        aux_names=["Torque - Original Path", "TTL1"],
        aux_units=["Nm", "V"],
        aux_kinds=["target", "digital"],
    )
    out["edf"].with_name("sub-01_task-ramp_events.tsv").write_text(
        "onset\tduration\tsample\tmvc_rate\tmvc_level\tevent_type\n"
        "2.0\t0.0\t4096\tn/a\tn/a\tmuscle_on\n"
        "2.0\t5.0\t4096\t6.0\t0.0\tlinear_isometric_ramp\n"
        "7.0\t10.0\t14336\t0.0\t30.0\tsteady_isometric\n"
        "17.0\t5.0\t34816\t-6.0\t30.0\tlinear_isometric_ramp\n",
        encoding="utf-8",
    )
    return out["edf"]


def _munitquest(stem: str, *, reorder: bool = False) -> Callable[[Path], Path]:
    def write(tmp_path: Path) -> Path:
        root = write_munitquest(tmp_path / "munitquest")
        # A root sidecar every recording of the task inherits; the recording's own values win.
        (root / "task-isometric30percentMVC_emg.json").write_text(
            '{"EMGGround": "left patella", "Manufacturer": "from the root"}', encoding="utf-8"
        )
        if reorder:
            path = root / f"{stem}_channels.tsv"
            header, *body = path.read_text(encoding="utf-8").splitlines()
            path.write_text("\n".join([header, *reversed(body)]) + "\n", encoding="utf-8")
        return root / f"{stem}_emg.edf"

    return write


def _traces(rows: list[int]) -> np.ndarray:
    return np.vstack([munitquest_trace(r, MUNITQUEST_SAMPLES) for r in rows]).astype(np.float32)


_TUTORIAL = {
    "manufacturer": "OTBioelettronica",
    "ground_location": "left patella",
    "units": "uV",
    "aux_units": "Nm",
}
_TWO_GRIDS = Expected(
    shape=(18, MUNITQUEST_SAMPLES),
    fsamp=FSAMP,
    grid_names=["MQ8MM0303"] * 2,
    muscle_names=["right tibialis anterior"] * 2,
    aux_names=["Torque"],
    metadata={**_TUTORIAL, "grid_labels": ["grid1", "grid2"]},
    data=_traces(list(range(18))),
    data_atol=0.02,
)
_TUTORIAL_PHASES: list[tuple[object, ...]] = [
    ("ramp", "up", 10240, 20480),
    ("plateau", None, 20480, 61440),
    ("ramp", "down", 61440, 71680),
]

_FILES: dict[str, tuple[Write, Expected, list[tuple[object, ...]] | None]] = {
    "muedit-export": (
        lambda _, tmp: _muedit_dataset(tmp),
        Expected(
            shape=(64, N_SAMPLES),
            fsamp=FSAMP,
            grid_names=["GR08MM1305"],
            muscle_names=["tibialis anterior"],
            # EDF labels are 16 characters; channels.tsv descriptions give the names back.
            aux_names=["Torque - Original Path", "TTL1"],
            metadata={
                "acquisition_date": "2026-03-26T14:31:26",
                "units": "mV",
                "aux_units": ["Nm", "n/a"],
                "aux_kinds": ["target", "digital"],
                "bids_entity_label": "sub-01_task-ramp",
                "grid_labels": ["Grid1"],
            },
            data=_muedit_signals()[0].astype(np.float32),
            data_atol=1e-5,
        ),
        [
            ("ramp", "up", 4096, 14336),
            ("plateau", None, 14336, 34816),
            ("ramp", "down", 34816, 45056),
        ],
    ),
    "tutorial-grid-and-wire": (
        lambda _, tmp: _munitquest(MUNITQUEST_SUB01)(tmp),
        Expected(
            shape=(13, MUNITQUEST_SAMPLES),
            fsamp=FSAMP,
            grid_names=["MQ8MM0304", "MQWIRE"],
            muscle_names=["right vastus lateralis"] * 2,
            aux_names=["Torque"],
            metadata=_TUTORIAL,
            data=_traces(list(range(13))),
            data_atol=0.02,
        ),
        None,
    ),
    "tutorial-two-grids": (
        lambda _, tmp: _munitquest(MUNITQUEST_SUB02)(tmp),
        _TWO_GRIDS,
        _TUTORIAL_PHASES,
    ),
    # Rows are matched to EDF signals by label, not by position.
    "tutorial-rows-reordered": (
        lambda _, tmp: _munitquest(MUNITQUEST_SUB02, reorder=True)(tmp),
        Expected(
            **{**_TWO_GRIDS.__dict__, "data": _traces([*range(8, -1, -1), *range(17, 8, -1)])}
        ),
        _TUTORIAL_PHASES,
    ),
    "sample": (
        lambda request, _: request.getfixturevalue("bdf_file"),
        Expected(
            shape=(384, 57_400),
            fsamp=2000.0,
            grid_names=["HD08MM1305"] * 6,
            aux_names=["AUX 1"],
            metadata={"bids_entity_label": "sub-1_ses-1_task-triangle_run-1"},
        ),
        None,
    ),
}


@pytest.mark.parametrize(
    "case",
    [pytest.param(c, marks=pytest.mark.data) if c == "sample" else c for c in _FILES],
)
def test_loads_bids(request: pytest.FixtureRequest, tmp_path: Path, case: str) -> None:
    write, expected, phases = _FILES[case]
    emg_path = write(request, tmp_path)
    sig = load_signal(str(emg_path))
    check_signal(sig, expected)
    if phases is not None:
        got = [(s["kind"], s["direction"], s["start"], s["end"]) for s in sig.metadata["segments"]]
        assert got == phases

    # The edit refit reads one grid at a time: the same rows as the full load.
    root, label = dataset_root(emg_path), sig.metadata["bids_entity_label"]
    assert root is not None and root.name != "sub-lab"
    first = 0
    for index, mask in enumerate(format_hdemg_signal(sig.grid_names)[2]):
        rows, fsamp, _ = read_bids_emg_grid(root, label, index, RamStore())
        assert fsamp == sig.fsamp
        np.testing.assert_array_equal(rows, sig.data[first : first + len(mask)])
        first += len(mask)


def _unnamed_grids(tmp_path: Path) -> Path:
    root = write_munitquest(tmp_path / "munitquest", named=False)
    return root / f"{MUNITQUEST_SUB02}_emg.edf"


def _replace_in(name: str, old: str, new: str) -> Callable[[Path], Path]:
    def write(tmp_path: Path) -> Path:
        emg = _munitquest(MUNITQUEST_SUB02)(tmp_path)
        path = emg.with_name(emg.name.replace("_emg.edf", name))
        path.write_text(path.read_text(encoding="utf-8").replace(old, new), encoding="utf-8")
        return emg

    return write


def _no_channels_tsv(tmp_path: Path) -> Path:
    emg = _munitquest(MUNITQUEST_SUB02)(tmp_path)
    emg.with_name(emg.name.replace("_emg.edf", "_channels.tsv")).unlink()
    return emg


def _not_edf(tmp_path: Path) -> Path:
    emg = _munitquest(MUNITQUEST_SUB02)(tmp_path)
    emg.write_bytes(b"not an EDF file" * 100)
    return emg


def _two_recordings(tmp_path: Path) -> Path:
    emg = _munitquest(MUNITQUEST_SUB02)(tmp_path)
    emg.with_name("sub-02_ses-01_task-other_emg.edf").write_bytes(emg.read_bytes())
    return emg.parent


_BAD_FILES: dict[str, tuple[Callable[[Path], Path], str]] = {
    "grids-not-in-the-catalogue": (_unnamed_grids, r"group 'grid1'.*_GRID_CATALOG"),
    "unknown-grid-model": (_replace_in("_channels.tsv", "MQ8MM0303", "GRX"), "unknown model 'GRX'"),
    "no-channels-tsv": (_no_channels_tsv, r"Cannot find channels\.tsv"),
    "sidecar-not-json": (_replace_in("_emg.json", "{", "{{"), "is not valid JSON"),
    "not-an-edf": (_not_edf, "as EDF/BDF"),
    "a-folder-of-two-recordings": (_two_recordings, "Multiple BIDS EMG files"),
}


@pytest.mark.parametrize("case", list(_BAD_FILES))
def test_refuses_bad_bids(tmp_path: Path, case: str) -> None:
    write, message = _BAD_FILES[case]
    with pytest.raises(SignalLoadError, match=message):
        load_signal(str(write(tmp_path)))


# ── Writing BIDS ─────────────────────────────────────────────────────────────

HD_GRID = ["HD08MM1305"]
N_HD_EMG = len(format_hdemg_signal(HD_GRID)[2][0])
PLACEMENT = "Grid over the muscle belly, located by palpation."


def test_the_form_fields_override_the_loader_fields_once() -> None:
    meta = form_over_loader_meta(
        {"units": "mV", "device_name": "Quattrocento", "recording_type": "epoched"},
        {"units": "uV", "manufacturers_model_name": "", "manufacturer": "OT"},
    )
    fields = bids_emg_fields(meta, ["TA", "VL"])
    assert fields["units"] == "uV"
    assert fields["manufacturers_model_name"] == "Quattrocento"  # a blank form field
    assert fields["manufacturer"] == "OT"
    assert fields["recording_type"] == "epoched"
    assert fields["target_muscle"] == ["TA", "VL"]
    assert fields["emg_json_extra"] == {"Muscles": ["TA", "VL"]}
    assert bids_emg_fields({}, "TA")["target_muscle"] == "TA"


def test_a_run_with_a_bad_bids_field_is_400_before_it_starts(tmp_path: Path) -> None:
    with pytest.raises(BidsFieldError) as refused:
        check_run_export(tmp_path, {"subject": "01"}, {})
    assert refused.value.field == "bids_entities"  # the API answers it with a 400
    bad_age = {"placement_scheme_description": PLACEMENT, "participant_meta": {"age": "abc"}}
    with pytest.raises(BidsFieldError, match="age"):
        check_run_export(tmp_path, bad_age, {})
    check_run_export(tmp_path, {}, {"placement_scheme_description": PLACEMENT})


def test_a_failed_edf_write_leaves_no_signal_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(*_: Any) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(bids, "_write_records", fail)
    coordinates, ied, discard, _ = format_hdemg_signal(HD_GRID)
    data = np.zeros((N_HD_EMG, N_SAMPLES), dtype=np.float32)
    with pytest.raises(OSError, match="disk full"):
        export_bids_emg(
            data, FSAMP, HD_GRID, coordinates, discard, tmp_path, ied=ied,
            placement_scheme_description=PLACEMENT,
        )  # fmt: skip
    assert not [p for p in tmp_path.rglob("*") if p.suffix in (".edf", ".bdf")]


def test_a_runs_npz_location(tmp_path: Path) -> None:
    source = tmp_path / "rec.mat"
    assert run_decomp_path(source, {}) == (tmp_path / "rec_decomp.npz", False)
    edf = tmp_path / "ds" / "sub-01" / "emg" / "sub-01_task-x_emg.edf"
    meta = {"bids_emg_path": str(edf), "bids_entity_label": "sub-01_task-x"}
    path, in_dataset = run_decomp_path(edf, meta)
    assert in_dataset
    assert (
        path
        == (
            tmp_path
            / "ds"
            / "derivatives"
            / "muedit"
            / "sub-01"
            / "decomp"
            / "sub-01_task-x_decomp.npz"
        ).resolve()
    )
    assert not path.parent.exists()  # created only when the file is written
    odd = {"bids_emg_path": str(tmp_path / "sub-x" / "a.edf"), "bids_entity_label": "nolabel"}
    assert run_decomp_path(source, odd) == (tmp_path / "rec_decomp.npz", False)


def test_auto_detected_bad_channels_are_marked_in_channels_tsv(tmp_path: Path) -> None:
    coordinates, ied, discard, _ = format_hdemg_signal(HD_GRID)
    declared = [np.zeros_like(discard[0])]
    declared[0][0] = 1
    auto = [declared[0].copy()]
    auto[0][[0, 5]] = 1
    out = export_bids_emg(
        np.random.default_rng(0).normal(size=(N_HD_EMG, N_SAMPLES)).astype(np.float32),
        FSAMP, HD_GRID, coordinates, declared, tmp_path, ied=ied,
        placement_scheme_description=PLACEMENT, auto_discard_channels=auto,
    )  # fmt: skip
    rows = [r for r in tsv_rows(out["channels_tsv"]) if r["type"] == "EMG"]
    assert (rows[0]["status"], rows[0]["status_description"]) == ("bad", "manually excluded")
    assert (rows[5]["status"], rows[5]["status_description"]) == ("bad", "auto-detected")
    assert sum(r["status"] == "bad" for r in rows) == 2


def test_a_participant_update_keeps_the_fields_it_does_not_give(tmp_path: Path) -> None:
    write_participant(tmp_path, "01", {"age": 30, "sex": "F", "handedness": "right"})
    write_participant(tmp_path, "01", {"age": "n/a", "sex": "n/a", "handedness": "left"})
    write_participant(tmp_path, "02", None)
    rows = tsv_rows(tmp_path / "participants.tsv")
    assert rows[0] == {"participant_id": "sub-01", "age": "30", "sex": "F", "handedness": "left"}
    assert rows[1] == {"participant_id": "sub-02", "age": "n/a", "sex": "n/a", "handedness": "n/a"}
    with pytest.raises(BidsFieldError, match="age"):
        write_participant(tmp_path, "01", {"age": "abc"})
