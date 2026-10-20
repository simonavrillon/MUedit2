"""The command line (``cli``): the decomposition command, and the server ``serve_api`` starts."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from muedit.cli import main, run_decomposition_cli
from tests._api import API, error


def test_cli_reports_a_file_it_cannot_load_without_a_traceback(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    notes = tmp_path / "notes.txt"
    notes.write_text("not a recording")
    with pytest.raises(SystemExit) as exited:
        run_decomposition_cli([str(notes)])
    assert exited.value.code == 1
    err = capsys.readouterr().err
    assert err.startswith("muedit: Unsupported file format: .txt")
    assert "Traceback" not in err


def test_cli_rejects_through_the_same_checks(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        run_decomposition_cli(["recording.otb4", "--niter", "0"])
    assert "niter must be >= 1" in capsys.readouterr().err


@pytest.mark.parametrize(("args", "code"), [(["--help"], 0), ([], 2), (["serve"], 2)])
def test_main_exits_without_a_known_command(
    monkeypatch: pytest.MonkeyPatch, args: list[str], code: int
) -> None:
    monkeypatch.setattr(sys, "argv", ["muedit", *args])
    with pytest.raises(SystemExit) as exited:
        main()
    assert exited.value.code == code


# ── serve_api ───────────────────────────────────────────────────────────────


def _served_app(monkeypatch: pytest.MonkeyPatch, **env: str) -> tuple[FastAPI, str]:
    """The app and host ``serve_api`` would run with ``env``, without starting uvicorn."""
    import muedit.cli as cli

    served: dict[str, Any] = {}
    monkeypatch.setattr(
        cli.uvicorn, "run", lambda app, host, **_: served.update(app=app, host=host)
    )
    monkeypatch.setattr(cli, "log_to_file", lambda _path: None)
    monkeypatch.delenv("MUEDIT_HOST", raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    cli.serve_api()
    return served["app"], served["host"]


class TestOriginAndHost:
    def test_binds_loopback_by_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _, host = _served_app(monkeypatch)
        assert host == "127.0.0.1"

    def test_no_other_origin_is_allowed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        app, _ = _served_app(monkeypatch)
        with TestClient(app, base_url="http://127.0.0.1:8000") as c:
            for origin in ("http://localhost:8080", "https://evil.example"):
                r = c.options(
                    f"{API}/preview-by-path",
                    headers={"Origin": origin, "Access-Control-Request-Method": "POST"},
                )
                assert "access-control-allow-origin" not in r.headers

    def test_the_frontend_is_served_on_the_api_origin(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        app, _ = _served_app(monkeypatch)
        with TestClient(app, base_url="http://127.0.0.1:8000") as c:
            page = c.get("/")
            assert page.status_code == 200
            assert page.headers["content-type"].startswith("text/html")
            assert page.headers["cache-control"] == "no-cache"
            assert c.get("/app.js").status_code == 200
            # The API routes come first; an unknown API path is still the JSON error envelope.
            assert c.get(f"{API}/health").json()["data"]["status"] == "ok"
            error(c.get(f"{API}/no-such-route"), 404)

    def test_rebound_host_is_rejected_on_loopback(self, monkeypatch: pytest.MonkeyPatch) -> None:
        app, _ = _served_app(monkeypatch)
        with TestClient(app, base_url="http://127.0.0.1:8000") as c:
            assert c.get(f"{API}/health").status_code == 200
            assert c.get(f"{API}/health", headers={"Host": "localhost:8000"}).status_code == 200
            assert c.get(f"{API}/health", headers={"Host": "evil.example:8000"}).status_code == 400

    def test_explicit_all_interfaces_skips_the_host_check(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        app, host = _served_app(monkeypatch, MUEDIT_HOST="0.0.0.0")  # noqa: S104
        assert host == "0.0.0.0"  # noqa: S104
        with TestClient(app, base_url="http://192.168.1.20:8000") as c:
            assert c.get(f"{API}/health").status_code == 200
