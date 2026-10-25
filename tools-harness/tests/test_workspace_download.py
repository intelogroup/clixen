"""workspace_download: the orb can save a file a page offers.

Before this existed the orb had no way to save bytes at all — write_text takes
strings, so a PDF was out of reach. The tests below pin the bounds, because the
URL is model- and page-supplied input.
"""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from tools import workspace

PDF_BYTES = b"%PDF-1.7\n" + b"x" * 5000


@pytest.fixture()
def vault(tmp_path, monkeypatch):
    (tmp_path / ".clixen-vault").write_text("")
    ws = tmp_path / "workspace"
    ws.mkdir()
    monkeypatch.setenv("CLIXEN_WORKSPACE", str(ws))
    return ws


@pytest.fixture()
def server():
    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/big":
                self.send_response(200)
                self.send_header("Content-Type", "application/pdf")
                self.send_header("Content-Length", str(80_000_000))
                self.end_headers()
                return
            body = PDF_BYTES if self.path.endswith(".pdf") else b"hello"
            self.send_response(200)
            self.send_header("Content-Type", "application/pdf" if body is PDF_BYTES else "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


def test_saves_real_bytes_into_the_workspace(vault, server):
    out = workspace.download(f"{server}/IGM_vol_03_no_44.pdf")
    assert "Downloaded" in out, out
    saved = vault / "downloads" / "IGM_vol_03_no_44.pdf"
    assert saved.is_file()
    assert saved.read_bytes() == PDF_BYTES, "bytes were altered in transit"
    assert saved.stat().st_mode & 0o077 == 0, "saved file is readable by other accounts"


def test_honours_an_explicit_path_and_overwrites_recoverably(vault, server):
    assert "Downloaded" in workspace.download(f"{server}/a.pdf", rel="igm/jan2025.pdf")
    first = (vault / "igm" / "jan2025.pdf").read_bytes()
    assert first == PDF_BYTES
    # a second download must snapshot the old version, not silently replace it
    assert "Downloaded" in workspace.download(f"{server}/a.pdf", rel="igm/jan2025.pdf")
    trash = list((vault / ".trash").iterdir())
    assert trash, "overwriting a downloaded file left no recoverable copy"


@pytest.mark.parametrize("bad", ["file:///etc/passwd", "data:text/plain,hi", "/etc/hosts", "ftp://x/y"])
def test_only_http_and_https_are_allowed(vault, bad):
    out = workspace.download(bad)
    assert out.startswith("[error]"), out
    assert "http" in out.lower()
    assert not any((vault / "downloads").glob("*")), "something was written for a rejected URL"


def test_a_path_cannot_escape_the_workspace(vault, server):
    """Through the guard the registry actually uses: an error string, no file."""
    out = workspace.guarded(workspace.download, url=f"{server}/a.pdf", rel="../../escape.pdf")
    assert out.startswith("[error]"), out
    assert "cannot be left" in out or ".." in out
    assert not (vault.parent / "escape.pdf").exists()


def test_the_raw_function_refuses_rather_than_writing(vault, server):
    """Belt and braces: even called directly, nothing lands outside the tree."""
    with pytest.raises(workspace.WorkspaceError):
        workspace.download(f"{server}/a.pdf", rel="../../escape2.pdf")
    assert not (vault.parent / "escape2.pdf").exists()


def test_a_declared_oversize_download_is_refused_before_writing(vault, server):
    out = workspace.download(f"{server}/big")
    assert out.startswith("[error]")
    assert "limit" in out
    assert not list((vault / "downloads").glob("big*"))


def test_a_stream_over_the_cap_stops_and_leaves_no_partial_file(vault, server):
    out = workspace.download(f"{server}/a.pdf", rel="small.pdf之外", max_bytes=100)
    assert out == "[error]" or out.startswith("[error]")
    leftovers = [p for p in (vault / "downloads").glob("*")] if (vault / "downloads").exists() else []
    assert not leftovers, f"partial file left behind: {leftovers}"


def test_a_connection_error_is_reported_not_raised(vault):
    out = workspace.download("http://127.0.0.1:1/nope.pdf")
    assert out.startswith("[error]")
    assert "could not download" in out


def test_the_orb_is_granted_the_tool():
    import orb_agent

    from tools.registry import ALL_TOOLS

    assert "workspace_download" in orb_agent.TOOL_NAMES
    voiced = {t["function"]["name"] for t in orb_agent.voice_tools(ALL_TOOLS)}
    assert "workspace_download" in voiced, "the orb could not save a file it was told to get"


def test_the_prompt_tells_it_that_empty_pages_need_the_api():
    import orb_agent

    assert "scrapling_api_capture" in orb_agent.PROMPT
    assert "client-side" in orb_agent.PROMPT or "shell" in orb_agent.PROMPT
    assert len(orb_agent.PROMPT) < 3_200, "prompt crept back over its guard"
