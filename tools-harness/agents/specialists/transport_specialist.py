"""
Transport specialist subagent.

Mission: answer transportation questions — bus/subway transit predictions,
Uber ride estimates/trip history. Fast path uses a direct connector call
(no LLM). Falls back to LLM tool loop for ambiguous queries. Uber runs on
agent-browser (--auto-connect to the user's real, logged-in Chrome) since
the BrowserOS backend was removed.

Public API:
    run_transport_specialist(query, model="deepseek/deepseek-v4-flash") -> TransportResult
"""

from __future__ import annotations

import logging
import re
import time

from pydantic import BaseModel, Field

from clients import cloud_client
from tools.registry import ALL_TOOLS, tools_with_tags

_log = logging.getLogger("transport_specialist")

TRANSPORT_TOOL_NAMES = tools_with_tags("spec_transport")

# Fast-path patterns
_RE_BUS = re.compile(
    r"\b(?:bus|subway|mbta|metro|transit)\b",
    re.I,
)
_RE_DIRECTIONS = re.compile(
    r"\bdirections?\s+(?:from|to|between)\b",
    re.I,
)
_RE_UBER_ESTIMATE = re.compile(r"\buber\b.*\b(?:price|cost|estimate|fare|how much)\b", re.I)
_RE_UBER_TRIPS = re.compile(r"\buber\b.*\b(?:trip|ride)s?\b.*\b(?:history|past|recent|my)\b|\bmy\s+uber\s+trips\b", re.I)

_RE_FROM_TO = re.compile(r"(?:from\s+)(.+?)(?:\s+to\s+)(.+)", re.I)
_RE_TO_FROM = re.compile(r"(?:to\s+)(.+?)(?:\s+from\s+)(.+)", re.I)
_RE_TO = re.compile(r"\b(?:to\s+)(.+)", re.I)


class TransportResult(BaseModel):
    text: str = ""
    services: list[str] = Field(default_factory=list)
    elapsed_s: float = 0.0
    error: str | None = None


def _extract_locations(query: str) -> tuple[str, str]:
    """Extract (destination, pickup) from query string."""
    m = _RE_FROM_TO.search(query)
    if m:
        return m.group(2).strip(), m.group(1).strip()
    m = _RE_TO_FROM.search(query)
    if m:
        return m.group(1).strip(), m.group(2).strip()
    m = _RE_TO.search(query)
    if m:
        return m.group(1).strip(), ""
    return "", ""


_SYSTEM_PROMPT = """You are TRANSPORT. Your only job: answer transportation questions.

TOOLS:
  bus_eta(origin, destination="") — Bus/subway transit predictions and directions.
  estimate_uber_ride(destination, pickup="") — Uber price estimates. NEVER books a ride.
  get_my_uber_trips() — Uber trip history.

RULES:
- Call only ONE tool.
- Transit: call bus_eta() with origin and destination.
- Uber price/estimate: call estimate_uber_ride().
- Uber trip history: call get_my_uber_trips().
- Stop after ONE tool call - return the result to the user."""


def _tool_schemas() -> list[dict]:
    return [t for t in ALL_TOOLS if t["function"]["name"] in TRANSPORT_TOOL_NAMES]


def _fast_transit(origin: str, destination: str) -> str:
    from tools.bus_eta import bus_eta
    return bus_eta(origin=origin, destination=destination)


def _fast_uber_estimate(destination: str, pickup: str) -> str:
    from tools.connector_uber import estimate_uber_ride
    return estimate_uber_ride(destination=destination or "3 Manning Terrace, Everett MA", pickup=pickup)


def _fast_uber_trips() -> str:
    from tools.connector_uber import get_my_uber_trips
    return get_my_uber_trips()


def run_transport_specialist(
    query: str,
    model: str = "deepseek/deepseek-v4-flash",
    timeout_s: float = 60.0,
) -> TransportResult:
    """Answer a transportation query. Fast path if pattern matches, else LLM."""
    t0 = time.time()

    destination, pickup = _extract_locations(query)

    # ── Fast path: regex → direct connector call ──
    if _RE_BUS.search(query) or _RE_DIRECTIONS.search(query):
        origin = pickup or ""
        dest = destination
        _log.info("[transport] fast: bus_eta origin=%s dest=%s", origin, dest)
        try:
            text = _fast_transit(origin, dest)
            return TransportResult(text=text, services=["transit"], elapsed_s=time.time() - t0)
        except Exception as e:
            return TransportResult(error=f"bus_eta failed: {e}", elapsed_s=time.time() - t0)

    if _RE_UBER_TRIPS.search(query):
        _log.info("[transport] fast: get_my_uber_trips")
        try:
            text = _fast_uber_trips()
            return TransportResult(text=text, services=["uber"], elapsed_s=time.time() - t0)
        except Exception as e:
            return TransportResult(error=f"get_my_uber_trips failed: {e}", elapsed_s=time.time() - t0)

    if _RE_UBER_ESTIMATE.search(query):
        _log.info("[transport] fast: estimate_uber_ride dest=%s pickup=%s", destination, pickup)
        try:
            text = _fast_uber_estimate(destination, pickup)
            return TransportResult(text=text, services=["uber"], elapsed_s=time.time() - t0)
        except Exception as e:
            return TransportResult(error=f"estimate_uber_ride failed: {e}", elapsed_s=time.time() - t0)

    # ── Fallback: LLM tool loop (cloud, runs the tool loop internally) ──
    _log.info("[transport] fallback: LLM tool loop")
    tools = _tool_schemas()

    try:
        from tools.memory_tools import mem_block_for
        _mem = mem_block_for(query)
        _sys = (_mem + "\n" + _SYSTEM_PROMPT) if _mem else _SYSTEM_PROMPT
        text = cloud_client.chat(
            user_message=query,
            tools=tools,
            model=model,
            system_prompt=_sys,
            max_rounds=2,
        )
    except Exception as e:
        return TransportResult(error=f"cloud_client error: {e}", elapsed_s=time.time() - t0)

    return TransportResult(text=text, elapsed_s=time.time() - t0)
