"""The edit log: what it records, and which log ``find_recoverable`` offers back."""

from __future__ import annotations

from pathlib import Path

import pytest

from muedit.editing.edit_log import EditLog, find_recoverable


@pytest.fixture()
def source(tmp_path: Path) -> Path:
    path = tmp_path / "rec_decomp.npz"
    path.write_bytes(b"decomposition")
    return path


def _log(source: Path, token: str, *ops: str) -> EditLog:
    log = EditLog.create(source, token)
    assert log is not None
    for op in ops:
        log.append(op, {"mu": 0})
    return log


def test_a_closed_log_with_edits_is_recoverable(cache_dir: Path, source: Path) -> None:
    log = _log(source, "tab", "flag", "flag", "undo")
    assert log.net == 1
    log.close(keep=True)
    found = find_recoverable(source, set())
    assert found is not None
    assert [r["op"] for r in found.records] == ["flag", "flag", "undo"]
    assert found.edits == 1


def test_a_log_of_another_file_version_is_deleted(cache_dir: Path, source: Path) -> None:
    _log(source, "tab", "flag").close(keep=True)
    source.write_bytes(b"v2, saved elsewhere")
    assert find_recoverable(source, set()) is None
    assert not list((cache_dir / "edit-logs").iterdir())


def test_a_log_without_edits_is_left_for_its_session(cache_dir: Path, source: Path) -> None:
    opening = _log(source, "other-tab")
    assert find_recoverable(source, set()) is None
    assert opening.path.exists()
    opening.close(keep=False)
    assert not opening.path.exists()


def test_a_live_log_is_not_recoverable(cache_dir: Path, source: Path) -> None:
    live = _log(source, "live", "flag")
    try:
        assert find_recoverable(source, {live.path}) is None
        assert find_recoverable(source, set()) is not None
    finally:
        live.close(keep=False)
