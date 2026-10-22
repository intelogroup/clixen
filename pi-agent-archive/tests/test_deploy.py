"""Integration checks against the deployed /root/pi-agent: script syntax,
.env key presence, cron install, exit codes. These are the "verify, don't
assume" guardrails.""" 
import os
import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path("/root/pi-agent")
SCRIPTS = ROOT / "scripts"
PY = "/root/pi-agent/.venv/bin/python"

REQUIRED_ENV = {
    "EXA_API_KEY", "TAVILY_API_KEY", "BRAVE_SEARCH_API_KEY", "BRAVE_API_KEY",
    "FIRECRAWL_API_KEY", "OPENROUTER_API_KEY", "PI_MODEL",
    "TELEGRAM_BOT_TOKEN", "TELEGRAM_OWNER_CHAT_ID",
}


class TestDeployment(unittest.TestCase):
    def test_scripts_exist_executable(self):
        for f in ["search_store.py", "embed_index.py", "recall.py", "memory.py",
                  "notify.py", "run.sh", "run_agent.sh", "backup.sh", "restore.sh"]:
            p = SCRIPTS / f
            assert p.exists(), f"missing {f}"
            if p.suffix == ".sh":
                assert os.access(p, os.X_OK), f"not executable: {f}"

    def test_python_scripts_syntax(self):
        for f in ["search_store.py", "embed_index.py", "recall.py", "memory.py", "notify.py"]:
            r = subprocess.run([PY, "-m", "py_compile", str(SCRIPTS / f)],
                               capture_output=True, text=True)
            assert r.returncode == 0, f"{f} failed py_compile: {r.stderr}"

    def test_shell_scripts_syntax(self):
        for f in ["run.sh", "run_agent.sh", "backup.sh", "restore.sh"]:
            r = subprocess.run(["bash", "-n", str(SCRIPTS / f)], capture_output=True, text=True)
            assert r.returncode == 0, f"{f} failed bash -n: {r.stderr}"

    def test_env_keys_present(self):
        env = {}
        p = ROOT / ".env"
        assert p.exists()
        for line in p.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"')
        missing = REQUIRED_ENV - set(env)
        assert not missing, f"missing env keys: {sorted(missing)}"
        empty = {k for k in REQUIRED_ENV if not env.get(k)}
        assert not empty, f"empty env keys: {sorted(empty)}"

    def test_cron_lines(self):
        r = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
        cron = r.stdout
        assert re.search(r"run\.sh.*cron\.log", cron), "tracker cron line missing"
        assert re.search(r"backup\.sh", cron), "backup cron line missing"

    @unittest.skipUnless(os.environ.get("RUN_INTEGRATION"), "set RUN_INTEGRATION=1")
    def test_embed_exit_code_zero(self):
        """Regression gate: embed_index.py must exit 0 (segfault fix).
        Runs the real embed against the live store (~2-5 min) and asserts
        SCRIPT_EXIT=0 — the exact failure mode that used to return 139."""
        r = subprocess.run([PY, str(SCRIPTS / "embed_index.py")],
                           capture_output=True, text=True, timeout=900)
        assert r.returncode == 0, (
            f"embed_index.py exited {r.returncode} (expected 0); stderr tail:\n"
            f"{r.stderr[-800:]}")

    def test_venv_pi_present(self):
        assert Path(PY).exists(), "venv python missing"


if __name__ == "__main__":
    unittest.main()