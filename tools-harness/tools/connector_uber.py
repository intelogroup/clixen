"""
Uber connector, rebuilt on agent-browser (`--auto-connect` to the user's
real, already-logged-in Chrome) after BrowserOS was ripped out.

No login/OAuth-click automation here (unlike the old BrowserOS version,
which drove a hidden tab + vault credentials + Google account picker) —
--auto-connect attaches to the user's live session, so if they're logged
into Uber in that Chrome, this just works. If not, ask them to log in
there once; we just surface "[uber] log in required" and bail.
"""
from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path

from tools._agent_browser import click, fill, get_text, navigate, snapshot

log = logging.getLogger("connector_uber")
_DATA_DIR = Path.home() / ".config" / "g4l" / "data"

_TRIPS_URL = "https://riders.uber.com/trips"
_MOBILE_URL = "https://m.uber.com/go/home?cmlntqp=optimized-nav-control"

_REF_RE = re.compile(r'(@e\d+)\s+\[(\w+)\]\s+"([^"]*)"')


def _save(data: dict) -> str:
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    p = _DATA_DIR / f"uber_data_{time.strftime('%Y%m%d_%H%M%S')}.json"
    p.write_text(json.dumps(data, indent=2, default=str))
    return str(p)


def _needs_login(snap_or_url: str) -> bool:
    return "auth.uber.com" in snap_or_url or "/login" in snap_or_url


def _find_ref(snap: str, role: str = "", text_contains: str = "") -> str | None:
    for ref, role_found, text in _REF_RE.findall(snap):
        if role and role != role_found:
            continue
        if text_contains and text_contains.lower() not in text.lower():
            continue
        return ref
    return None


def _set_location(label: str, address: str) -> str | None:
    """Fill a pickup/dropoff searchbox by label, then click the first matching
    suggestion. Returns an error string, or None on success."""
    snap = snapshot()
    ref = _find_ref(snap, text_contains=label)
    if not ref:
        return f"[uber] Could not find '{label}' field on page"

    fill(ref, address)
    time.sleep(2)

    snap2 = snapshot()
    words = address.split(",")[0].strip().split()
    for i in range(len(words), 0, -1):
        target = " ".join(words[:i])
        opt_ref = _find_ref(snap2, role="option", text_contains=target)
        if opt_ref:
            click(opt_ref)
            time.sleep(1.5)
            return None

    return f"[uber] Could not select address option for '{address}'"


# ---------------------------------------------------------------------------
# Public API: estimate_uber_ride
# ---------------------------------------------------------------------------

def estimate_uber_ride(destination: str = "3 Manning Terrace, Everett MA", pickup: str = "") -> str:
    """Get Uber ride price estimates to a destination. NEVER books a ride.
    Sets pickup first (if provided), then dropoff, reads prices from the
    pricing page. Requires the user's real Chrome to already be logged
    into Uber (agent-browser --auto-connect)."""
    nav = navigate(_MOBILE_URL)
    if nav.startswith("[agent-browser]"):
        return f"[uber] {nav}"

    if _needs_login(nav):
        return "[uber] Log in required — sign into Uber in your Chrome, then call estimate_uber_ride() again."

    if pickup:
        err = _set_location("Pickup location", pickup)
        if err:
            return err

    err = _set_location("Dropoff location", destination)
    if err:
        return err

    next_ref = _find_ref(snapshot(), text_contains="Next") or _find_ref(snapshot(), text_contains="Pickup now")
    if next_ref:
        click(next_ref)
        time.sleep(4)

    text = get_text()
    pickup_label = pickup or "(current location)"
    _save({"destination": destination, "pickup": pickup_label, "raw_text": text[:3000]})

    ride_options = re.findall(r'(\w+(?:\s+\S+)?)\s+Person\s+\d+.*?\$([\d.]+)', text)

    lines = [
        f"Uber ride estimate from '{pickup_label}' to '{destination}':",
        "",
        f"{'Ride Type':20s} {'Price':>8s}",
        f"{'-'*20} {'-'*8}",
    ]
    seen = set()
    for name, price in ride_options:
        name_clean = re.sub(r'\s+\S+$', '', name)
        if name_clean not in seen:
            seen.add(name_clean)
            lines.append(f"{name_clean:20s} {price:>8s}")

    if not seen:
        return f"Uber ride estimate from '{pickup_label}' to '{destination}':\n\n{text[:1500]}"

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Trip history
# ---------------------------------------------------------------------------

_MONTHS = r"Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec"

_TRIP_HEADER_RE = re.compile(r"^###\s+(.+)$")
_TRIP_DATE_RE = re.compile(
    rf"({_MONTHS})\s+(\d+)\s*•\s*\d{{1,2}}:\d{{2}}\s*(?:AM|PM)\$([\d.]+)", re.I
)
_TRIP_INLINE_RE = re.compile(rf"(\d+\s+\S+(?:\s+\S+)*)\s+•\s+({_MONTHS})\s+\d+", re.I)


def _parse_trips_from_text(text: str) -> list[dict]:
    """Parse trip lines from Uber trips page text. Handles both the markdown
    '### address' header layout and the older single-line inline layout."""
    trips = []
    lines = text.split("\n")

    for i, line in enumerate(lines):
        header_m = _TRIP_HEADER_RE.match(line.strip())
        if not header_m:
            continue
        address = header_m.group(1).strip()
        for j in range(i + 1, min(i + 6, len(lines))):
            date_m = _TRIP_DATE_RE.search(lines[j])
            if date_m:
                status = "canceled" if "canceled" in lines[j].lower() else "completed"
                trips.append({
                    "address": address,
                    "date": f"{date_m.group(1)} {date_m.group(2)}",
                    "price": date_m.group(3),
                    "status": status,
                })
                break

    if trips:
        return trips

    for i, line in enumerate(lines):
        m = _TRIP_INLINE_RE.search(line)
        if m:
            address = m.group(1).strip()
            price_m = re.search(r'\$([\d.]+)', line)
            date_m = re.search(rf"(({_MONTHS})\s+\d+)", line, re.I)
            date = date_m.group(1) if date_m else ""
            status = "canceled" if "canceled" in line.lower() else "completed"
            trips.append({"address": address, "date": date,
                         "price": price_m.group(1) if price_m else "", "status": status})
    return trips


def get_my_uber_trips() -> str:
    """Get your Uber trips. Single call, returns formatted data. Requires
    the user's real Chrome to already be logged into Uber."""
    nav = navigate(_TRIPS_URL)
    if nav.startswith("[agent-browser]"):
        return f"[uber] {nav}"

    if _needs_login(nav):
        return "[uber] Log in required — sign into Uber in your Chrome, then call get_my_uber_trips() again."

    text = get_text()
    trips = _parse_trips_from_text(text)
    saved = _save({"trips": trips, "raw_text": text[:3000]})

    if trips:
        lines = [f"Your Uber Trips ({len(trips)} found):", f"Full data: {saved}", ""]
        for t in trips:
            date = t.get("date", "?")
            price = f"${t['price']}" if t.get("price") else ""
            status = t.get("status", "")
            addr = t.get("address", "")[:40]
            lines.append(f"  {date} | {price:>6s} | {status:10s} | {addr}")
    else:
        log.warning(
            "Uber trip-text parsing found 0 rows (page text: %d chars) — falling "
            "back to raw text dump. Trip layout regex likely stale.", len(text),
        )
        lines = ["Uber trips page (could not parse structured data):",
                 f"Full text saved: {saved}", "", text[:800]]

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

GET_MY_UBER_TRIPS_SCHEMA = {
    "type": "function", "function": {
        "name": "get_my_uber_trips",
        "description": "Get your Uber trip history via your real, already-logged-in Chrome (agent-browser --auto-connect). Extracts trip data from the trips page.",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
}

ESTIMATE_UBER_RIDE_SCHEMA = {
    "type": "function", "function": {
        "name": "estimate_uber_ride",
        "description": "Get Uber ride price estimates. NEVER books a ride — only shows prices. Sets pickup first, then dropoff, reads all ride options with prices.",
        "parameters": {"type": "object", "properties": {
            "destination": {"type": "string", "description": "Destination address", "default": "3 Manning Terrace, Everett MA"},
            "pickup": {"type": "string", "description": "Pickup address (optional — defaults to current location if empty)", "default": ""},
        }, "required": []},
    },
}

SCHEMAS = [GET_MY_UBER_TRIPS_SCHEMA, ESTIMATE_UBER_RIDE_SCHEMA]
