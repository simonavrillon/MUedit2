"""Raw BIDS events: trigger pulses and the segmented target, and how a re-export merges them."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from muedit.api.schemas import BidsSaveFields
from muedit.api.services.editing_service import _save, _SaveRequest
from muedit.decomp.decomposition_file import load_decomposition
from muedit.decomp.preprocess import _target_segments, bids_export_step, preprocess_step
from muedit.decomp.types import DecompositionParameters, LoadStepOutput
from muedit.io._bids_reader import load_bids_signal
from muedit.io.bids import export_bids_emg, export_bids_events
from muedit.models import EditSignalContext, SignalImport
from muedit.signal.grid import format_hdemg_signal
from muedit.storage import RamStore

FSAMP = 2000.0
GRIDS = ["HD08MM1305"]
N_EMG = len(format_hdemg_signal(GRIDS)[2][0])
PLACEMENT = "Grid over the muscle belly, located by palpation."


def _trapezoid(n_samples: int, level: float = 30.0, *, start_s: float = 2.0) -> np.ndarray:
    """Rest, a 5 s ramp up, a 10 s plateau, a 5 s ramp down, rest."""
    t = np.arange(n_samples) / FSAMP
    up = np.clip((t - start_s) / 5.0, 0, 1)
    down = np.clip((t - start_s - 15.0) / 5.0, 0, 1)
    return (level * (up - down)).astype(np.float32)


def _pulses(n_samples: int, onsets_s: list[float], width_s: float = 0.1) -> np.ndarray:
    line = np.zeros(n_samples, np.float32)
    for onset in onsets_s:
        a = int(onset * FSAMP)
        line[a : a + int(width_s * FSAMP)] = 1.0
    return line


def _signal(aux: np.ndarray, names: list[str], units: str | list[str] | None) -> SignalImport:
    data = np.random.default_rng(0).normal(size=(N_EMG, aux.shape[1])).astype(np.float32)
    return SignalImport.build(
        data=data,
        fsamp=FSAMP,
        gridname=GRIDS,
        auxiliary=aux,
        auxiliaryname=names,
        metadata={"aux_units": units},
    )


def _export_emg(root: Path, sig: SignalImport, kinds: list[str]) -> Path:
    coordinates, ied, discard, _ = format_hdemg_signal(GRIDS)
    out = export_bids_emg(
        sig.data,
        sig.fsamp,
        GRIDS,
        coordinates,
        discard,
        root,
        ied=ied,
        placement_scheme_description=PLACEMENT,
        aux_data=sig.auxiliary,
        aux_names=sig.auxiliaryname,
        aux_units=sig.metadata["aux_units"],
        aux_kinds=kinds,
    )
    return out["edf"]


def _write_events(edf: Path, **kwargs: Any) -> Path:
    """``export_bids_events`` where the test expects a file."""
    out = export_bids_events(edf, FSAMP, **kwargs)
    assert out is not None
    return out


def _segmented(sig: SignalImport) -> dict[str, Any]:
    """The segments record of the target on aux row 0."""
    record = _target_segments(sig, 0)
    assert record is not None
    return record


def _rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8") as f:
        return list(csv.DictReader(f, delimiter="\t"))


def _events(rows: list[dict[str, str]]) -> list[str]:
    return [r["event_type"] for r in rows]


@pytest.fixture
def trapezoid_recording(tmp_path: Path) -> tuple[Path, SignalImport]:
    n = int(24 * FSAMP)
    aux = np.vstack([_trapezoid(n), _pulses(n, [1.0, 22.5])])
    sig = _signal(aux, ["Force - Original Path", "TTL1"], ["%(MVC)", "V"])
    return _export_emg(tmp_path, sig, ["target", "digital"]), sig


def test_a_trapezoid_and_its_trigger_pulses(trapezoid_recording: tuple[Path, SignalImport]) -> None:
    edf, sig = trapezoid_recording
    out = _write_events(edf, aux_data=sig.auxiliary, target_segments=_segmented(sig))
    assert out.name.endswith("_events.tsv")
    rows = _rows(out)
    assert _events(rows) == [
        "ttl",
        "muscle_on",
        "linear_isometric_ramp",
        "steady_isometric",
        "linear_isometric_ramp",
        "muscle_off",
        "ttl",
    ]
    assert [float(r["onset"]) for r in rows] == sorted(float(r["onset"]) for r in rows)
    header = out.read_text(encoding="utf-8").splitlines()[0].split("\t")
    assert header[:4] == ["onset", "duration", "sample", "event_type"]

    first_pulse = rows[0]
    assert (first_pulse["onset"], first_pulse["duration"], first_pulse["sample"]) == (
        "1",
        "0.1",
        "2000",
    )
    assert (first_pulse["value"], first_pulse["channel"], first_pulse["source"]) == (
        "1",
        "TTL1",
        "ttl",
    )

    up, plateau, down = rows[2], rows[3], rows[4]
    assert up["direction"] == "up" and down["direction"] == "down"
    assert float(up["onset"]) == pytest.approx(2.0, abs=0.15)
    assert float(plateau["mvc_level"]) == pytest.approx(30.0, abs=0.5)
    assert float(up["mvc_rate"]) == pytest.approx(6.0, abs=0.5)  # 30 % MVC over 5 s
    assert float(down["mvc_rate"]) == pytest.approx(-6.0, abs=0.5)
    assert {r["source"] for r in rows[1:6]} == {"target_segmentation"}

    described = json.loads(out.with_suffix(".json").read_text(encoding="utf-8"))
    assert set(header) <= set(described)
    assert described["level_start"]["Units"] == "%(MVC)"
    assert "'Force - Original Path'" in described["source"]["Levels"]["target_segmentation"]
    assert set(described["event_type"]["Levels"]) == set(_events(rows))


def test_rest_between_contractions_closes_each(tmp_path: Path) -> None:
    n = int(48 * FSAMP)
    target = _trapezoid(n) + _trapezoid(n, start_s=26.0)
    sig = _signal(target[None, :], ["Force - Original Path"], "N")
    edf = _export_emg(tmp_path, sig, ["target"])
    rows = _rows(_write_events(edf, target_segments=_segmented(sig)))
    events = _events(rows)
    assert events.count("muscle_on") == 2 and events.count("muscle_off") == 2
    assert "mvc_level" not in rows[0]  # the target is in N, not % MVC


def test_a_sinusoid_lists_its_cycles(tmp_path: Path) -> None:
    n = int(20 * FSAMP)
    t = np.arange(n) / FSAMP
    target = np.where((t > 2) & (t < 18), 20 + 10 * np.sin(2 * np.pi * 0.5 * (t - 2)), 0.0)
    sig = _signal(target[None, :].astype(np.float32), ["Force - Original Path"], "%MVC")
    edf = _export_emg(tmp_path, sig, ["target"])
    rows = _rows(_write_events(edf, target_segments=_segmented(sig)))
    sine = [r for r in rows if r["event_type"] == "sinusoid"]
    assert len(sine) == 1
    assert float(sine[0]["frequency_hz"]) == pytest.approx(0.5, rel=0.05)
    assert float(sine[0]["amplitude"]) == pytest.approx(10.0, rel=0.1)
    assert len([r for r in rows if r["event_type"] == "cycle"]) >= 6


def test_a_clock_line_is_left_out(tmp_path: Path) -> None:
    n = int(10 * FSAMP)
    clock = (np.arange(n) % 20 < 10).astype(np.float32)  # 100 Hz
    sig = _signal(np.vstack([clock, _pulses(n, [3.0])]), ["SYNC1", "TTL2"], None)
    edf = _export_emg(tmp_path, sig, ["digital", "digital"])
    out = _write_events(edf, aux_data=sig.auxiliary)
    rows = _rows(out)
    assert [(r["channel"], r["value"]) for r in rows] == [("TTL2", "2")]
    note = json.loads(out.with_suffix(".json").read_text())["source"]["Levels"]["ttl"]
    assert "SYNC1" in note


def test_a_re_export_replaces_only_the_sources_it_was_given(
    trapezoid_recording: tuple[Path, SignalImport],
) -> None:
    edf, sig = trapezoid_recording
    segments = _segmented(sig)
    out = _write_events(edf, aux_data=sig.auxiliary, target_segments=segments)
    with out.open("a", encoding="utf-8") as f:
        header = out.read_text(encoding="utf-8").splitlines()[0].split("\t")
        manual = {"onset": "12", "duration": "0", "event_type": "cue", "source": "manual"}
        f.write("\t".join(manual.get(c, "n/a") for c in header) + "\n")

    # An edit save knows the segments but not the aux rows: the pulses stay.
    export_bids_events(edf, FSAMP, target_segments=segments)
    events = _events(_rows(out))
    assert events.count("ttl") == 2 and events.count("cue") == 1
    assert events.count("muscle_on") == 1

    # Aux without the segments: the segmentation rows stay, the pulses are rewritten.
    export_bids_events(edf, FSAMP, aux_data=sig.auxiliary)
    assert _events(_rows(out)) == events


def test_foreign_cells_and_source_spellings_survive_a_merge(
    trapezoid_recording: tuple[Path, SignalImport],
) -> None:
    edf, sig = trapezoid_recording
    events_tsv = edf.with_name(edf.name.replace("_emg.edf", "_events.tsv"))
    events_tsv.write_text(
        "onset\tduration\tevent_type\tnote\tsource\n"
        '0.5\t0\tcue\t"quoted\tmanual\n'
        "9.0\t0\tttl\tstale\tTTL\n",
        encoding="utf-8",
    )
    export_bids_events(edf, FSAMP, aux_data=sig.auxiliary)
    header, *body = (line.split("\t") for line in events_tsv.read_text("utf-8").splitlines())
    notes = [row[header.index("note")] for row in body]
    # BIDS TSV has no quoting; a source spelled TTL is MUedit's and is replaced.
    assert '"quoted' in notes
    assert "stale" not in notes


def test_phases_from_another_tool_are_kept_and_not_repeated(
    trapezoid_recording: tuple[Path, SignalImport],
) -> None:
    edf, sig = trapezoid_recording
    events_tsv = edf.with_name(edf.name.replace("_emg.edf", "_events.tsv"))
    events_tsv.write_text(
        "onset\tduration\tsample\tmvc_rate\tmvc_level\tevent_type\tevent_description\n"
        "2.0\t5.0\t4000\t6.0\t0.0\tlinear_isometric_ramp\tRamp up\n"
        "7.0\t10.0\t14000\t0.0\t30.0\tsteady_isometric\tHold\n",
        encoding="utf-8",
    )
    events_tsv.with_suffix(".json").write_text(
        json.dumps(
            {
                "event_type": {
                    "Description": "MUniverse phase",
                    "Levels": {"steady_isometric": "Hold, as MUniverse puts it"},
                },
                "mvc_level": {"Description": "Plateau level", "Units": "%MVC"},
                "event_description": {"Description": "Free text"},
            }
        )
    )
    export_bids_events(edf, FSAMP, aux_data=sig.auxiliary, target_segments=_segmented(sig))
    rows = _rows(events_tsv)
    assert _events(rows) == ["ttl", "linear_isometric_ramp", "steady_isometric", "ttl"]
    assert rows[1]["event_description"] == "Ramp up"
    described = json.loads(events_tsv.with_suffix(".json").read_text())
    assert described["event_description"] == {"Description": "Free text"}
    assert described["event_type"]["Levels"]["steady_isometric"] == "Hold, as MUniverse puts it"
    assert "ttl" in described["event_type"]["Levels"]
    # Their columns keep their words; only the columns MUedit added get MUedit's.
    assert described["event_type"]["Description"] == "MUniverse phase"
    assert described["mvc_level"] == {"Description": "Plateau level", "Units": "%MVC"}
    assert "sample" not in described
    assert {"value", "channel", "source"} <= described.keys()


def test_nothing_to_write_writes_no_file(tmp_path: Path) -> None:
    n = int(5 * FSAMP)
    sig = _signal(np.zeros((1, n), np.float32), ["Torque"], "Nm")
    edf = _export_emg(tmp_path, sig, ["analog"])
    assert export_bids_events(edf, FSAMP, aux_data=sig.auxiliary) is None
    assert not list(tmp_path.rglob("*_events.tsv"))


def test_muedit_events_read_back_as_their_segments(
    trapezoid_recording: tuple[Path, SignalImport],
) -> None:
    edf, sig = trapezoid_recording
    written = _segmented(sig)["segments"]
    export_bids_events(edf, FSAMP, aux_data=sig.auxiliary, target_segments=_segmented(sig))
    read = load_bids_signal(str(edf)).metadata["segments"]
    assert [(s["kind"], s["direction"], s["start"], s["end"]) for s in read] == [
        (s["kind"], s["direction"], s["start"], s["end"]) for s in written
    ]
    for got, want in zip(read, written, strict=True):
        assert got["level_start"] == pytest.approx(want["level_start"], abs=1e-3)
        assert got["level_end"] == pytest.approx(want["level_end"], abs=1e-3)


def test_muniverse_phases_read_back_with_their_directions(
    trapezoid_recording: tuple[Path, SignalImport],
) -> None:
    edf, _ = trapezoid_recording
    edf.with_name(edf.name.replace("_emg.edf", "_events.tsv")).write_text(
        "onset\tduration\tsample\tmvc_rate\tmvc_level\tevent_type\tevent_description\n"
        "2.0\t0.0\t4000\tn/a\tn/a\tmuscle_on\tOn\n"
        "2.0\t5.0\t4000\t6.0\t0.0\tlinear_isometric_ramp\tUp\n"
        "7.0\t10.0\t14000\t0.0\t30.0\tsteady_isometric\tHold\n"
        "17.0\t5.0\t34000\t-6.0\t30.0\tlinear_isometric_ramp\tDown\n",
        encoding="utf-8",
    )
    segments = load_bids_signal(str(edf)).metadata["segments"]
    assert [(s["kind"], s["direction"], s["start"], s["end"]) for s in segments] == [
        ("ramp", "up", 4000, 14000),
        ("plateau", None, 14000, 34000),
        ("ramp", "down", 34000, 44000),
    ]
    assert segments[0]["level_end"] == pytest.approx(30.0)


def test_sinusoid_cycles_read_back(tmp_path: Path) -> None:
    n = int(20 * FSAMP)
    t = np.arange(n) / FSAMP
    target = np.where((t > 2) & (t < 18), 20 + 10 * np.sin(2 * np.pi * 0.5 * (t - 2)), 0.0)
    sig = _signal(target[None, :].astype(np.float32), ["Force - Original Path"], "%MVC")
    edf = _export_emg(tmp_path, sig, ["target"])
    written = _segmented(sig)["segments"]
    export_bids_events(edf, FSAMP, target_segments=_segmented(sig))
    (sine,) = [
        s for s in load_bids_signal(str(edf)).metadata["segments"] if s["kind"] == "sinusoid"
    ]
    (want,) = [s for s in written if s["kind"] == "sinusoid"]
    assert sine["cycles"] == want["cycles"]
    assert sine["frequency_hz"] == pytest.approx(want["frequency_hz"], rel=1e-3)


def test_events_without_phases_give_no_segments(
    trapezoid_recording: tuple[Path, SignalImport],
) -> None:
    edf, sig = trapezoid_recording
    export_bids_events(edf, FSAMP, aux_data=sig.auxiliary)
    assert load_bids_signal(str(edf)).metadata["segments"] == []


def test_events_inherited_from_the_subject_folder(
    trapezoid_recording: tuple[Path, SignalImport],
) -> None:
    edf, _ = trapezoid_recording
    entity = edf.name.replace("_emg.edf", "")
    (edf.parent.parent / f"{entity}_events.tsv").write_text(
        "onset\tduration\tevent_type\n7.0\t10.0\tsteady_hold\n", encoding="utf-8"
    )
    segments = load_bids_signal(str(edf)).metadata["segments"]
    assert [(s["kind"], s["start"], s["end"]) for s in segments] == [("plateau", 14000, 34000)]


def test_a_run_writes_the_events_and_keeps_the_segments(tmp_path: Path) -> None:
    n = int(24 * FSAMP)
    sig = _signal(
        np.vstack([_trapezoid(n), _pulses(n, [1.0])]),
        ["Force - Original Path", "TTL1"],
        ["%MVC", "V"],
    )
    sig.metadata["aux_kinds"] = ["target", "digital"]
    loaded = LoadStepOutput(
        full_path="synthetic", filename="synthetic", signal=sig, data=sig.data, fsamp=FSAMP
    )
    prep = preprocess_step(
        loaded=loaded,
        duration=None,
        manual_roi=False,
        roi=None,
        rois=None,
        params=DecompositionParameters(),
        discard_overrides=None,
        target_index=0,
    )
    entities = {"subject": "01", "task": "ramp", "placement_scheme_description": PLACEMENT}
    bids_export_step(loaded, prep, str(tmp_path), entities, None)
    assert prep.target_segments is not None
    assert prep.target_segments["target_name"] == "Force - Original Path"
    assert [s["kind"] for s in prep.target_segments["segments"]] == ["ramp", "plateau", "ramp"]
    (events,) = tmp_path.rglob("*_events.tsv")
    assert _events(_rows(events)).count("ttl") == 1
    assert _events(_rows(events)).count("steady_isometric") == 1


def test_a_run_without_segmenting_writes_no_target_rows(tmp_path: Path) -> None:
    n = int(24 * FSAMP)
    sig = _signal(_trapezoid(n)[None, :], ["Force - Original Path"], "%MVC")
    assert _target_segments(sig, None) is None
    assert _target_segments(sig, 5) is None


def test_an_edit_save_writes_the_events_and_carries_the_segments(tmp_path: Path) -> None:
    n = int(24 * FSAMP)
    sig = _signal(_trapezoid(n)[None, :], ["Force - Original Path"], "%MVC")
    segments = _segmented(sig)
    coordinates, ied, discard, _ = format_hdemg_signal(GRIDS)
    ctx = EditSignalContext(
        data=sig.data,
        fsamp=FSAMP,
        grid_names=GRIDS,
        emgmask=discard,
        coordinates=coordinates,
        ied=ied,
        target_segments=segments,
    )
    req = _SaveRequest(
        distimes=[np.array([100, 5000, 9000])],
        flagged=[False],
        mu_grid_index=[0],
        mu_uids=["g0_mu0"],
        edit_history=[],
        artifact_times=[],
        fsamp=FSAMP,
        total_samples=n,
        grid_names=GRIDS,
        parameters={},
        form=BidsSaveFields(
            entity_label="sub-01_task-ramp", placement_scheme_description=PLACEMENT
        ),
        pulse=lambda kept: None,
        signal=ctx,
        bids_root=tmp_path,
    )
    result: dict[str, Any] = _save(req)[0]
    events = Path(result["bids_emg_paths"]["events_tsv"])
    assert _events(_rows(events)).count("steady_isometric") == 1

    _, saved_ctx = load_decomposition(result["path"], store=RamStore())
    assert saved_ctx is not None
    assert saved_ctx.target_segments == segments
