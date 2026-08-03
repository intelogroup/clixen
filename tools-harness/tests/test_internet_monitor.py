"""Verify internet_monitor detects connectivity state and logs transitions."""

import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from unittest.mock import AsyncMock

import pytest
from internet_monitor import InternetMonitor


class _FakeWriter:
    """Pretend to be an asyncio.StreamWriter — has close() + async wait_closed()."""
    def close(self):
        pass
    async def wait_closed(self):
        pass


@pytest.mark.asyncio
async def test_check_true_when_any_endpoint_reachable(monkeypatch):
    tried = []

    async def fake_connect(host, port):
        tried.append((host, port))
        if host == "1.1.1.1":
            writer = _FakeWriter()
            return (object(), writer)
        raise OSError("unreachable")

    monkeypatch.setattr(asyncio, "open_connection", fake_connect)
    m = InternetMonitor()
    assert await m.check() is True
    assert ("1.1.1.1", 443) in tried


@pytest.mark.asyncio
async def test_check_false_when_all_endpoints_fail(monkeypatch):
    async def fake_connect(host, port):
        raise OSError("unreachable")

    monkeypatch.setattr(asyncio, "open_connection", fake_connect)
    m = InternetMonitor()
    assert await m.check() is False


@pytest.mark.asyncio
async def test_run_logs_up_on_startup(monkeypatch, caplog):
    async def fake_connect(host, port):
        writer = _FakeWriter()
        return (object(), writer)

    monkeypatch.setattr(asyncio, "open_connection", fake_connect)
    m = InternetMonitor(interval=999)

    with caplog.at_level(logging.INFO):
        task = asyncio.create_task(m.run())
        await asyncio.sleep(0)
        task.cancel()

    assert any("Internet: UP" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_run_logs_warning_on_startup_down(monkeypatch, caplog):
    async def fake_connect(host, port):
        raise OSError("unreachable")

    monkeypatch.setattr(asyncio, "open_connection", fake_connect)
    m = InternetMonitor(interval=999)

    with caplog.at_level(logging.WARNING):
        task = asyncio.create_task(m.run())
        await asyncio.sleep(0)
        task.cancel()

    assert any("DOWN" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_run_logs_transition_up_to_down(monkeypatch, caplog):
    state = {"ok": True}

    async def fake_connect(host, port):
        if state["ok"]:
            writer = _FakeWriter()
            return (object(), writer)
        raise OSError("unreachable")

    monkeypatch.setattr(asyncio, "open_connection", fake_connect)
    m = InternetMonitor(interval=0.05)

    with caplog.at_level(logging.WARNING):
        task = asyncio.create_task(m.run())
        await asyncio.sleep(0.02)
        state["ok"] = False
        await asyncio.sleep(0.1)
        task.cancel()

    assert any("DOWN" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_run_logs_transition_down_to_up(monkeypatch, caplog):
    state = {"ok": False}

    async def fake_connect(host, port):
        if state["ok"]:
            writer = _FakeWriter()
            return (object(), writer)
        raise OSError("unreachable")

    monkeypatch.setattr(asyncio, "open_connection", fake_connect)
    m = InternetMonitor(interval=0.05)

    state["ok"] = False
    with caplog.at_level(logging.INFO):
        task = asyncio.create_task(m.run())
        await asyncio.sleep(0.02)
        assert any("DOWN" in r.message for r in caplog.records)
        caplog.clear()
        state["ok"] = True
        await asyncio.sleep(0.1)
        task.cancel()

    assert any("restored" in r.message.lower() for r in caplog.records)
