"""BIDS EMG export, checked against the EMG-BIDS specification, its paper and its tutorials.

References (precedence as listed):
- [S] BIDS specification, Electromyography (BIDS 1.11):
  https://bids-specification.readthedocs.io/en/stable/modality-specific-files/electromyography.html
- [P] Shirazi et al. (2026), EMG-BIDS: an extension to the Brain Imaging Data Structure for
  electromyography, bioRxiv 10.64898/2026.07.21.739911.
- [T] MUnitQuest EMG-BIDS tutorials 1 and 2:
  https://github.com/MUnitQuest/MUnitQuest_tutorials/tree/13af32d
"""

from __future__ import annotations

import datetime as dt
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import numpy as np
import pyedflib
import pytest
import scipy.io

from muedit import storage
from muedit.api.schemas import BidsSaveFields
from muedit.api.services.editing_service import _save, _SaveRequest
from muedit.decomp.decomposition_file import LOADER_BIDS_META_KEYS
from muedit.decomp.pipeline import run_decomposition
from muedit.decomp.preprocess import bids_export_step, preprocess_step
from muedit.decomp.types import DecompositionParameters, LoadStepOutput, PreprocessStepOutput
from muedit.errors import MueditError
from muedit.io import bids, load_signal
from muedit.io.bids import export_bids_mu_derivatives, read_channel_groups
from muedit.models import EditSignalContext, SignalImport
from muedit.signal.grid import format_hdemg_signal
from tests._io_files import tsv_rows
from tests._synthetic_emg import motor_unit_emg

# Pinned so a validator release cannot change the outcome unnoticed (latest on JSR, 2026-10-13).
VALIDATOR = "jsr:@bids/validator@3.0.2"

FSAMP = 2048.0
GRIDS = ["GR08MM1305", "HD08MM1305"]
N_EMG = sum(len(mask) for mask in format_hdemg_signal(GRIDS)[2])
N_SAMPLES = 24 * 2048 + 700  # a ragged last data record
START = "2026-03-26T14:31:26"
LABEL = "sub-07_ses-pre_task-stairs_run-2"
PLACEMENT = "Grids centred on the muscle bellies, found by palpation."

#: What the session form sends for a run's export (``collectBidsEntities``).
FORM: dict[str, Any] = {
    "subject": "07",
    "session": "pre",
    "task": "stairs",
    "run": "2",
    "target_muscle": ["soleus", "tibialis anterior"],
    "powerline_freq": 60,
    "manufacturers_model_name": "Quattrocento",
    "placement_scheme": "Other",
    "placement_scheme_description": PLACEMENT,
    "reference_location": "Wet band around the ankle",
    "ground_location": "Wet band around the wrist",
    "task_description": "Stair climbing at a steady pace.",
    "participant_meta": {"age": "34", "sex": "M", "handedness": "right"},
}


@pytest.fixture(autouse=True)
def _small_blocks(monkeypatch: pytest.MonkeyPatch) -> None:
    """Blocks of about two data records, so the EDF writer and reader cross block edges."""
    size = 2 * (N_EMG + 2) * int(FSAMP) * 8
    monkeypatch.setattr(storage, "BLOCK_BYTES", size)
    monkeypatch.setattr(bids, "BLOCK_BYTES", size)


def _recording() -> SignalImport:
    """A loaded recording: two grids, a %MVC target trapezoid and a trigger line."""
    rng = np.random.default_rng(0)
    data = (rng.normal(size=(N_EMG, N_SAMPLES)) * 0.05).astype(np.float32)
    data[2] = 0.07  # a flat channel: its physical range is widened, not zero
    t = np.arange(N_SAMPLES) / FSAMP
    target = 30.0 * (np.clip((t - 2) / 5, 0, 1) - np.clip((t - 17) / 5, 0, 1))
    trigger = ((t > 1.0) & (t < 1.1)) | ((t > 22.5) & (t < 22.6))
    return SignalImport.build(
        data=data,
        fsamp=FSAMP,
        grid_names=GRIDS,
        muscle_names=["soleus", "tibialis anterior"],
        aux_data=np.vstack([target, trigger]).astype(np.float32),
        aux_names=["Force - Original Path", "TTL1"],
        metadata={
            "acquisition_date": START,
            "manufacturer": "OT Bioelettronica",
            "device_name": "QUATTROCENTO",
            "software_versions": "OTBioLab+",
            "hardware_filters": ["HP: 10, LP: 500"],
            "powerline_freq": 50.0,
            "units": "mV",
            "gains": [150.0] * N_EMG,
            "emg_hpf": [10.0] * N_EMG,
            "emg_lpf": [500.0] * N_EMG,
            "aux_units": ["%MVC", "V"],
            "aux_gains": [1.0, 1.0],
            "aux_kinds": ["target", "digital"],
            "recording_type": "continuous",
            "software_filters": "n/a",
            "electrode_impedances": [52.5] + [None] * (N_EMG - 1),
        },
    )


def _run_export(root: Path, sig: SignalImport, form: dict[str, Any]) -> PreprocessStepOutput:
    """A run's export of the raw recording, as ``run_decomposition`` makes it before decomposing."""
    loaded = LoadStepOutput(full_path="rec.otb+", signal=sig)
    prep = preprocess_step(
        loaded=loaded,
        duration=None,
        manual_roi=False,
        rois=None,
        params=DecompositionParameters(),
        discard_overrides=None,
        target_index=0,
    )
    return bids_export_step(loaded, prep, str(root), form, None)


def _json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _header(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines()[0].split("\t")


def _validate(root: Path) -> list[dict[str, Any]]:
    done = subprocess.run(
        ["deno", "run", "-ERN", VALIDATOR, str(root), "--format", "json"],
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )
    try:
        issues: list[dict[str, Any]] = json.loads(done.stdout)["issues"]["issues"]
    except json.JSONDecodeError:
        pytest.fail(f"validator gave no JSON (exit {done.returncode}): {done.stderr[-2000:]}")
    return issues


def test_export_follows_emg_bids(tmp_path: Path) -> None:
    sig = _recording()
    _run_export(tmp_path, sig, FORM)
    emg_dir = tmp_path / "sub-07" / "ses-pre" / "emg"
    channels_tsv = emg_dir / f"{LABEL}_channels.tsv"
    spikes = [np.array([4096, 6144, 9000]), np.array([5000, 12000])]
    export_bids_mu_derivatives(
        spikes,
        FSAMP,
        tmp_path,
        LABEL,
        mu_uids=["g0_mu0", "g1_mu0"],
        mu_grid_index=[0, 1],
        grid_labels=read_channel_groups(channels_tsv),
    )

    # [S] file layout: one recording, its sidecars, and the session-level electrode files.
    assert sorted(p.name for p in emg_dir.iterdir()) == sorted(
        [
            f"{LABEL}_emg.edf",
            f"{LABEL}_emg.json",
            f"{LABEL}_channels.tsv",
            f"{LABEL}_channels.json",
            f"{LABEL}_events.tsv",
            f"{LABEL}_events.json",
            "sub-07_ses-pre_electrodes.tsv",
            "sub-07_ses-pre_space-GR08MM1305_coordsystem.json",
            "sub-07_ses-pre_space-HD08MM1305_coordsystem.json",
        ]
    )

    # [S] _emg.json: every REQUIRED field, with the form over the loader.
    sidecar = _json(emg_dir / f"{LABEL}_emg.json")
    assert {
        key: sidecar[key]
        for key in (
            "TaskName",
            "SamplingFrequency",
            "PowerLineFrequency",
            "SoftwareFilters",
            "EMGReference",
            "EMGPlacementScheme",
            "EMGPlacementSchemeDescription",  # REQUIRED with the scheme Other
        )
    } == {
        "TaskName": "stairs",
        "SamplingFrequency": FSAMP,
        "PowerLineFrequency": 60,
        "SoftwareFilters": "n/a",
        "EMGReference": "Wet band around the ankle",
        "EMGPlacementScheme": "Other",
        "EMGPlacementSchemeDescription": PLACEMENT,
    }
    # [S] RECOMMENDED fields MUedit knows; [P] the per-channel counts tools rely on.
    assert sidecar["Manufacturer"] == "OT Bioelettronica"
    assert sidecar["ManufacturersModelName"] == "Quattrocento"
    assert sidecar["SoftwareVersions"] == "OTBioLab+"
    assert sidecar["TaskDescription"] == FORM["task_description"]
    assert sidecar["EMGGround"] == "Wet band around the wrist"
    assert sidecar["HardwareFilters"] == {
        "HighPassFilter": {"CutOffHz": 10.0},
        "LowPassFilter": {"CutOffHz": 500.0},
    }
    assert sidecar["RecordingType"] == "continuous"
    assert sidecar["RecordingDuration"] == pytest.approx(N_SAMPLES / FSAMP)
    assert (sidecar["EMGChannelCount"], sidecar["TriggerChannelCount"]) == (N_EMG, 1)
    assert "MISCChannelCount" not in sidecar  # not an EMG sidecar field
    assert sidecar["InterelectrodeDistance"] == 8.0
    assert sidecar["Muscles"] == ["soleus", "tibialis anterior"]

    # [S] channels.tsv: name, type, units first; [T] every EMG channel against reference R1.
    rows = tsv_rows(channels_tsv)
    assert _header(channels_tsv)[:3] == ["name", "type", "units"]
    emg_rows, aux_rows = rows[:N_EMG], rows[N_EMG:]
    assert {(r["type"], r["units"], r["reference"], r["sampling_frequency"]) for r in emg_rows} == {
        ("EMG", "mV", "R1", "2048.0")
    }
    assert [r["group"] for r in emg_rows] == ["Grid1"] * 64 + ["Grid2"] * 64
    assert [r["target_muscle"] for r in emg_rows] == ["soleus"] * 64 + ["tibialis anterior"] * 64
    assert {(r["low_cutoff"], r["high_cutoff"], r["gain"], r["status"]) for r in emg_rows} == {
        ("10", "500", "150.0", "good")
    }
    assert [(r["type"], r["units"], r["reference"]) for r in aux_rows] == [
        ("MISC", "%MVC", "n/a"),
        ("TRIG", "n/a", "n/a"),  # a trigger line holds states, not a unit
    ]
    levels = _json(channels_tsv.with_suffix(".json"))["reference"]["Levels"]
    assert levels == {"R1": "Monopolar reference electrode: Wet band around the ankle"}

    # [S] the EDF: channel labels match channels.tsv, the start time is the recording's.
    edf = emg_dir / f"{LABEL}_emg.edf"
    with pyedflib.EdfReader(str(edf)) as reader:
        assert [label.strip() for label in reader.getSignalLabels()] == [r["name"] for r in rows]
        assert reader.getStartdatetime() == dt.datetime.fromisoformat(START)
    # 16-bit samples over each channel's range; the ragged last record is cut on reading.
    back = load_signal(str(edf))
    assert back.data.shape == (N_EMG, N_SAMPLES)
    ranges = np.ptp(sig.data, axis=1)
    steps = np.where(ranges > 0, ranges, 2.0) / 65535  # a flat channel spans [x - 1, x + 1]
    # One step, widened by the 8-character physical range and the float32 read.
    assert np.all(np.abs(back.data - sig.data) <= 1.1 * steps[:, None])

    # [S] electrodes.tsv and coordsystem.json: positions in mm in a named space.
    electrodes_tsv = emg_dir / "sub-07_ses-pre_electrodes.tsv"
    electrodes = tsv_rows(electrodes_tsv)
    assert _header(electrodes_tsv)[:4] == ["name", "x", "y", "z"]
    assert len(electrodes) == N_EMG and "R1" not in {e["name"] for e in electrodes}
    assert {e["coordinate_system"] for e in electrodes} == {"GR08MM1305", "HD08MM1305"}
    assert [e["impedance"] for e in electrodes[:2]] == ["52.5", "n/a"]  # kOhm
    for space in ("GR08MM1305", "HD08MM1305"):
        coordsystem = _json(emg_dir / f"sub-07_ses-pre_space-{space}_coordsystem.json")
        assert (coordsystem["EMGCoordinateSystem"], coordsystem["EMGCoordinateUnits"]) == (
            "Other",
            "mm",
        )

    # [S] events.tsv: onset and duration first, sorted; [P]/[T] the task phases as event_type.
    events_tsv = emg_dir / f"{LABEL}_events.tsv"
    events = tsv_rows(events_tsv)
    assert _header(events_tsv)[:2] == ["onset", "duration"]
    assert [float(e["onset"]) for e in events] == sorted(float(e["onset"]) for e in events)
    assert [e["event_type"] for e in events] == [
        "ttl",
        "muscle_on",
        "linear_isometric_ramp",
        "steady_isometric",
        "linear_isometric_ramp",
        "muscle_off",
        "ttl",
    ]
    described = _json(events_tsv.with_suffix(".json"))
    assert set(_header(events_tsv)) <= set(described)
    assert set(described["event_type"]["Levels"]) == {e["event_type"] for e in events}

    # [S] dataset files: description, and participants with their columns described.
    description = _json(tmp_path / "dataset_description.json")
    assert description["DatasetType"] == "raw" and description["BIDSVersion"] == "1.11.1"
    (participant,) = tsv_rows(tmp_path / "participants.tsv")
    assert participant == {
        "participant_id": "sub-07",
        "age": "34",
        "sex": "M",
        "handedness": "right",
    }
    assert _json(tmp_path / "participants.json")["age"]["Units"] == "years"

    # [P] motor units as a derivative: one event per discharge, tied to its grid's group.
    deriv = tmp_path / "derivatives" / "muedit"
    deriv_events = deriv / "sub-07" / "ses-pre" / "emg" / f"{LABEL}_desc-decomposition_events.tsv"
    assert _header(deriv_events) == [
        "onset",
        "duration",
        "sample",
        "unit_id",
        "event_type",
        "group",
    ]
    discharges = tsv_rows(deriv_events)
    assert [(d["sample"], d["unit_id"], d["group"]) for d in discharges] == [
        ("4096", "g0_mu0", "Grid1"),
        ("5000", "g1_mu0", "Grid2"),
        ("6144", "g0_mu0", "Grid1"),
        ("9000", "g0_mu0", "Grid1"),
        ("12000", "g1_mu0", "Grid2"),
    ]
    assert {d["event_type"] for d in discharges} == {"motor-unit-spike"}
    assert set(_header(deriv_events)) <= set(_json(deriv_events.with_suffix(".json")))
    deriv_description = _json(deriv / "dataset_description.json")
    assert deriv_description["DatasetType"] == "derivative"
    assert deriv_description["GeneratedBy"][0]["Name"] == "MUedit"
    assert deriv_description["SourceDatasets"] == [{"URL": "bids:raw:"}]
    assert deriv_description["DatasetLinks"] == {"raw": "../../"}
    assert "desc-decomposition" in {r["desc_id"] for r in tsv_rows(deriv / "descriptions.tsv")}
    assert (deriv / ".bidsignore").read_text() == "decomp\n"

    # [S] the scheme Other without its REQUIRED description writes nothing.
    elsewhere = tmp_path / "elsewhere"
    with pytest.raises(MueditError, match="placement description"):
        _run_export(elsewhere, sig, {**FORM, "placement_scheme_description": " "})
    assert not list(elsewhere.rglob("*_emg.*"))

    # The official validator, when Deno is installed.
    if shutil.which("deno"):
        issues = _validate(tmp_path)
        errors = [issue for issue in issues if issue.get("severity") == "error"]
        assert not errors, json.dumps(errors[:10], indent=1)


def _edit_save(root: Path, recording: SignalImport, form: BidsSaveFields) -> dict[str, Any]:
    """An edit save of two MUs on ``recording``, into the dataset at ``root``."""
    coordinates, ied, discard, _ = format_hdemg_signal(recording.grid_names)
    loader_meta = {
        k: recording.metadata[k] for k in LOADER_BIDS_META_KEYS if k in recording.metadata
    }
    ctx = EditSignalContext(
        data=recording.data,
        fsamp=recording.fsamp,
        grid_names=recording.grid_names,
        discard_channels=discard,
        coordinates=coordinates,
        ied=ied,
        aux_data=recording.aux_data,
        aux_names=recording.aux_names,
        loader_meta=loader_meta,
    )
    request = _SaveRequest(
        spike_times=[np.array([4096, 6144]), np.array([5000])],
        flagged=[False, False],
        mu_grid_index=[0, 1],
        mu_uids=["g0_mu0", "g1_mu0"],
        history=[],
        artifact_times=[],
        fsamp=recording.fsamp,
        total_samples=recording.data.shape[1],
        grid_names=recording.grid_names,
        parameters={},
        form=form,
        pulse_trains=lambda kept: None,
        signal=ctx,
        bids_root=root,
    )
    return _save(request)[0]


def _events(path: Path) -> list[str]:
    return [row["event_type"] for row in tsv_rows(path)]


def test_re_export_into_an_existing_dataset(tmp_path: Path) -> None:
    _run_export(tmp_path, _recording(), {**FORM, "file_format": "bdf"})
    emg_dir = tmp_path / "sub-07" / "ses-pre" / "emg"
    bdf = emg_dir / f"{LABEL}_emg.bdf"
    sidecars = {
        name: (emg_dir / f"{LABEL}{name}").read_bytes()
        for name in ("_emg.bdf", "_emg.json", "_channels.tsv")
    }
    events_tsv = emg_dir / f"{LABEL}_events.tsv"
    header = _header(events_tsv)
    manual = {"onset": "12", "duration": "0", "event_type": "cue", "source": "manual"}
    with events_tsv.open("a", encoding="utf-8") as f:
        f.write("\t".join(manual.get(c, "n/a") for c in header) + "\n")
    deriv = tmp_path / "derivatives" / "muedit"
    deriv.mkdir(parents=True)
    (deriv / "dataset_description.json").write_text(
        json.dumps(
            {
                "Name": "Stairs, decomposed",
                "Authors": ["A. Person"],
                "GeneratedBy": [{"Name": "Notebook"}],
            }
        )
    )
    (deriv / "descriptions.tsv").write_text("desc_id\tdescription\ndesc-preview\tfirst look\n")

    # A run on the BIDS recording itself writes only what is missing: no placement
    # description is needed, the BDF stays a BDF, and the events are merged.
    recording = load_signal(str(bdf))
    rerun = {k: v for k, v in FORM.items() if k != "placement_scheme_description"}
    _run_export(tmp_path, recording, {**rerun, "participant_meta": {"handedness": "left"}})
    assert sorted(p.name for p in emg_dir.glob("*_emg.*")) == [
        f"{LABEL}_emg.bdf",
        f"{LABEL}_emg.json",
    ]
    for name, before in sidecars.items():
        assert (emg_dir / f"{LABEL}{name}").read_bytes() == before, name
    events = _events(events_tsv)
    assert (events.count("ttl"), events.count("cue"), events.count("muscle_on")) == (2, 1, 1)
    (participant,) = tsv_rows(tmp_path / "participants.tsv")
    assert participant == {
        "participant_id": "sub-07",
        "age": "34",
        "sex": "M",
        "handedness": "left",
    }

    # An edit save into the dataset rewrites the recording's sidecars from what was loaded
    # back: units, placement and reference survive, and others' derivative entries are kept.
    result = _edit_save(tmp_path, recording, BidsSaveFields(entity_label=LABEL))
    assert "bids_emg_error" not in result, result.get("bids_emg_error")
    sidecar = _json(emg_dir / f"{LABEL}_emg.json")
    assert sidecar["EMGPlacementSchemeDescription"] == PLACEMENT
    assert sidecar["EMGReference"] == "Wet band around the ankle"
    assert {
        r["units"] for r in tsv_rows(emg_dir / f"{LABEL}_channels.tsv") if r["type"] == "EMG"
    } == {"mV"}
    assert sorted(p.name for p in emg_dir.glob("*_emg.*")) == [
        f"{LABEL}_emg.bdf",
        f"{LABEL}_emg.json",
    ]
    events = _events(events_tsv)
    assert (events.count("ttl"), events.count("cue")) == (2, 1)
    deriv_description = _json(deriv / "dataset_description.json")
    assert deriv_description["Name"] == "Stairs, decomposed"
    assert deriv_description["Authors"] == ["A. Person"]
    assert [g["Name"] for g in deriv_description["GeneratedBy"]] == ["MUedit", "Notebook"]
    described = {r["desc_id"]: r["description"] for r in tsv_rows(deriv / "descriptions.tsv")}
    assert described["desc-preview"] == "first look"
    assert Path(result["bids_deriv_paths"]["events_tsv"]).exists()

    # Task phases another tool listed are kept, and MUedit adds none of its own.
    events_tsv.write_text(
        "onset\tduration\tsample\tevent_type\tevent_description\n"
        "2.0\t5.0\t4096\tlinear_isometric_ramp\tRamp up\n"
        "7.0\t10.0\t14336\tsteady_isometric\tHold\n",
        encoding="utf-8",
    )
    _run_export(tmp_path, recording, rerun)
    rows = tsv_rows(events_tsv)
    assert [r["event_type"] for r in rows] == [
        "ttl",
        "linear_isometric_ramp",
        "steady_isometric",
        "ttl",
    ]
    assert rows[1]["event_description"] == "Ramp up"


def test_a_bids_run_saves_a_complete_derivative_without_the_emg(tmp_path: Path) -> None:
    path = tmp_path / "motor_units.mat"
    n_samples = 12_000
    emg = motor_unit_emg(seed=3, n_samples=n_samples, fsamp=FSAMP)
    scipy.io.savemat(
        path,
        {"signal": {"data": emg, "fsamp": FSAMP, "gridname": "GR08MM1305", "muscle": "ta"}},
    )
    params = DecompositionParameters(niter=5, nbextchan=200)
    root = tmp_path / "ds"
    entities = {"subject": "01", "task": "mu", "placement_scheme_description": PLACEMENT}
    run = run_decomposition(str(path), params=params, bids_root=str(root), bids_entities=entities)
    assert run.save_path is not None
    deriv = root / "derivatives" / "muedit"
    assert Path(run.save_path).is_relative_to(deriv)
    assert json.loads((deriv / "dataset_description.json").read_text())["DatasetType"] == (
        "derivative"
    )
    with np.load(run.save_path) as z:
        assert "emg_data" not in z.files
    assert run.rois

    # The exported recording, decomposed again without an export, saves into the same
    # derivatives and does not copy the raw EMG there.
    edf = root / "sub-01" / "emg" / "sub-01_task-mu_emg.edf"
    again = run_decomposition(str(edf), params=params)
    assert again.save_path == run.save_path
    with np.load(again.save_path) as z:
        assert "emg_data" not in z.files
