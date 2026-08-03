"""Regression guard: the site-specific browser auto-action block (removed June 2026) must stay
removed from the tool-result handler.

That block hardcoded a personal quiz-site workflow and silently mutated `browser_snapshot`
results inside `clients/ollama_client.py`. It was excised for launch. Re-introducing any of it
would both leak a personal integration into the public core and resurrect hidden result
mutation — this asserts it's gone.
"""
from pathlib import Path

_SRC = Path(__file__).resolve().parent.parent / "clients" / "ollama_client.py"


def test_no_site_specific_browser_automation():
    src = _SRC.read_text(encoding="utf-8").lower()
    for needle in ("medicospira", "_loaded_exam_ids", "resum_rout", "paypal/index.php", "exam.php"):
        assert needle not in src, f"dead site-specific automation re-introduced: {needle!r}"
