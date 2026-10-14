"""Where a recording and MUedit's derivatives sit in a BIDS dataset, named by their entities."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


def entity_pairs(stem: str) -> dict[str, str]:
    """The ``key-value`` entities of a BIDS file stem."""
    pairs = (part.split("-", 1) for part in stem.split("_") if "-" in part)
    return dict(pairs)


@dataclass(frozen=True)
class BidsEntities:
    """The entities that name a recording, ``sub-<subject>[_ses-<session>]_task-<task>…``."""

    subject: str
    session: str | None = None
    task: str | None = None
    acquisition: str | None = None
    run: str | None = None
    recording: str | None = None

    @classmethod
    def parse(cls, label: str) -> BidsEntities:
        """The entities of a label or file stem; a label without ``sub-<label>`` raises ``ValueError``."""
        pairs = {key: value for key, value in entity_pairs(label).items() if value}
        if "sub" not in pairs:
            raise ValueError(f"No 'sub-<label>' entity in {label!r}")
        return cls(
            subject=pairs["sub"],
            session=pairs.get("ses"),
            task=pairs.get("task"),
            acquisition=pairs.get("acq"),
            run=pairs.get("run"),
            recording=pairs.get("recording"),
        )

    @property
    def label(self) -> str:
        """The prefix of the recording's file names, its entities in BIDS order."""
        pairs = (
            ("sub", self.subject),
            ("ses", self.session),
            ("task", self.task),
            ("acq", self.acquisition),
            ("run", self.run),
            ("recording", self.recording),
        )
        return "_".join(f"{key}-{value}" for key, value in pairs if value)

    def folder(self, root: Path) -> Path:
        """``<root>/sub-<subject>[/ses-<session>]``."""
        folder = root / f"sub-{self.subject}"
        return folder / f"ses-{self.session}" if self.session else folder

    def emg_dir(self, root: Path) -> Path:
        """The raw EMG folder of the recording in the dataset at ``root``."""
        return self.folder(root) / "emg"

    def derivative_dir(self, root: Path, datatype: str, pipeline: str = "muedit") -> Path:
        """The recording's ``datatype`` folder in the ``pipeline`` derivatives of ``root``."""
        return self.folder(root / "derivatives" / pipeline) / datatype


def dataset_root(path: Path) -> Path | None:
    """The raw dataset above a file, ``derivatives/<pipeline>`` stepped over, or ``None`` outside one."""
    subject = next((p for p in path.parents if p.name.startswith("sub-")), None)
    if subject is None:
        return None
    root = subject.parent
    return root.parent.parent if root.parent.name == "derivatives" else root
