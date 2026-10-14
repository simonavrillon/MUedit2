"""Where recordings and derivatives sit in a BIDS dataset."""

from __future__ import annotations

from pathlib import Path

import pytest

from muedit.io._bids_layout import BidsEntities, dataset_root

LABEL = "sub-01_ses-02_task-ramp_acq-hd_run-3_recording-emg"


def test_a_label_reads_back_unchanged() -> None:
    entities = BidsEntities.parse(f"{LABEL}_emg")
    assert entities.label == LABEL
    assert (entities.subject, entities.session, entities.task) == ("01", "02", "ramp")


def test_a_label_without_subject_raises() -> None:
    with pytest.raises(ValueError, match="sub-"):
        BidsEntities.parse("task-ramp_run-1")
    with pytest.raises(ValueError, match="sub-"):
        BidsEntities.parse("sub-_task-ramp")


def test_folders_follow_subject_and_session() -> None:
    root = Path("/data/study")
    assert BidsEntities.parse(LABEL).emg_dir(root) == root / "sub-01/ses-02/emg"
    assert BidsEntities(subject="01").derivative_dir(root, "decomp") == (
        root / "derivatives/muedit/sub-01/decomp"
    )


@pytest.mark.parametrize(
    ("path", "root"),
    [
        ("/data/study/sub-01/ses-02/emg/x_emg.edf", "/data/study"),
        ("/data/study/derivatives/muedit/sub-01/decomp/x_decomp.npz", "/data/study"),
        # A folder above the dataset whose name starts with sub- is not the subject's.
        ("/sub-lab/study/sub-01/emg/x_emg.edf", "/sub-lab/study"),
        ("/data/study/x_emg.edf", None),
    ],
)
def test_dataset_root(path: str, root: str | None) -> None:
    assert dataset_root(Path(path)) == (None if root is None else Path(root))
