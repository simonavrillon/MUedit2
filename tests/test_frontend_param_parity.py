"""Frontend decomposition defaults and mode names must match the Python owner."""

from __future__ import annotations

import re

from muedit.decomp.types import POSTPROCESS_MODES, DecompositionParameters
from tests.conftest import REPO_ROOT

FRONTEND = REPO_ROOT / "frontend"


def _js_mode_keys() -> set[str]:
    js = (FRONTEND / "src" / "decomp" / "params.js").read_text()
    block = js[js.index("export const POSTPROCESS_MODES = {") :]
    block = block[: block.index("\n};")]
    return set(re.findall(r'^  "?([\w-]+)"?: \{', block, flags=re.MULTILINE))


def test_params_js_mode_keys_match_python() -> None:
    assert _js_mode_keys() == set(POSTPROCESS_MODES)


def test_html_mode_options_match_python() -> None:
    html = (FRONTEND / "index.html").read_text()
    select = re.search(r'<select id="postprocessMode">(.*?)</select>', html, re.S)
    assert select
    assert set(re.findall(r'<option value="([^"]+)"', select.group(1))) == set(POSTPROCESS_MODES)


def test_html_sil_default_matches_python() -> None:
    html = (FRONTEND / "index.html").read_text()
    match = re.search(r'id="silValue"\s+value="([^"]+)"', html)
    assert match
    assert float(match.group(1)) == DecompositionParameters().sil_thr
