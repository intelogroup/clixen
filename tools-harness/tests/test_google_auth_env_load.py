"""Regression test: tools/google_auth.py must resolve GOOGLE_TOKEN_PATH from
.env at import time, regardless of whether the caller loaded .env first.

Confirmed live 2026-09-17: a caller that does `from tools.google_auth import
get_service` without going through harness.py's import chain (which loads
.env itself) got GOOGLE_TOKEN_PATH unset, silently fell back to the module's
hardcoded _DEFAULT_TOKEN (google-mcp/token.json — a stale file, dead since
2026-08-15, at a similarly-named but wrong path vs the real gmail-mcp
project), and failed with the exact same confusing invalid_grant error a
genuinely expired token would produce. Fixed by calling load_dotenv() at
module import time in google_auth.py itself.

Run in a fresh subprocess (not just `importlib.reload` in-process) so no
earlier test in the same pytest run can have already populated os.environ
and mask a regression.
"""
import os
import subprocess
import sys
from pathlib import Path

_HARNESS_DIR = Path(__file__).resolve().parent.parent


def test_google_token_path_resolved_without_caller_loading_dotenv():
    env_path = _HARNESS_DIR / ".env"
    if not env_path.exists():
        import pytest
        pytest.skip(".env not present in this environment")

    # Only proceed if .env actually sets GOOGLE_TOKEN_PATH — otherwise this
    # assertion is meaningless (there'd be nothing for import-time load_dotenv
    # to resolve).
    if "GOOGLE_TOKEN_PATH=" not in env_path.read_text():
        import pytest
        pytest.skip(".env has no GOOGLE_TOKEN_PATH configured")

    code = (
        "import sys, os; sys.path.insert(0, %r)\n"
        "assert os.environ.get('GOOGLE_TOKEN_PATH') is None, "
        "'test invalid: GOOGLE_TOKEN_PATH already set in parent env'\n"
        "from tools.google_auth import _DEFAULT_TOKEN\n"
        "resolved = os.environ.get('GOOGLE_TOKEN_PATH', _DEFAULT_TOKEN)\n"
        "print(resolved)\n"
    ) % str(_HARNESS_DIR)

    clean_env = {k: v for k, v in os.environ.items() if k != "GOOGLE_TOKEN_PATH"}
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(_HARNESS_DIR),
        capture_output=True,
        text=True,
        env=clean_env,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    resolved_path = result.stdout.strip()
    assert resolved_path != _DEFAULT_TOKEN_UNRESOLVED_MARKER(env_path), (
        "GOOGLE_TOKEN_PATH resolved to the hardcoded _DEFAULT_TOKEN — "
        ".env's GOOGLE_TOKEN_PATH was not loaded at import time"
    )


def _DEFAULT_TOKEN_UNRESOLVED_MARKER(env_path: Path) -> str:
    # The hardcoded fallback google_auth.py uses when GOOGLE_TOKEN_PATH is unset.
    repo_root = env_path.parent.parent  # tools-harness/.. == clixen
    return str(repo_root / "google-mcp" / "token.json")


if __name__ == "__main__":
    test_google_token_path_resolved_without_caller_loading_dotenv()
    print("OK — google_auth.py resolves GOOGLE_TOKEN_PATH from .env at import time.")
