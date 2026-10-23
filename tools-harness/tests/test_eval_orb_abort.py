import io

import pytest

from evals import screen_actions as ev


def test_an_interrupted_orb_run_aborts_the_server_side_turn(monkeypatch):
    aborted = []
    monkeypatch.setattr(ev, "_abort_http", lambda chat_id: aborted.append(chat_id))
    monkeypatch.setattr(ev.subprocess, "run", lambda *a, **k: type("R", (), {"stdout": "tok", "returncode": 0})())

    class Boom(io.StringIO):
        def __iter__(self):
            raise KeyboardInterrupt

    class Resp(Boom):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: Resp())
    with pytest.raises(KeyboardInterrupt):
        ev._drive_http("hello", "orb-eval-x-1", 5)
    assert aborted == ["orb-eval-x-1"]  # killing the client must not leave the agent running and sending mail
