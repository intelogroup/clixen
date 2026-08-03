"""
bus_eta tool — finds nearest MBTA stops, real-time arrivals, and multi-modal
transit routes (bus + subway) using Google Maps Directions + MBTA V3.
"""
import logging
import math
import os
from collections import defaultdict
from datetime import datetime, timedelta
import time

import httpx

_log = logging.getLogger("bus_eta")

MBTA_BASE = "https://api-v3.mbta.com"
NOMINATIM_BASE = "https://nominatim.openstreetmap.org"
GOOGLE_MAPS_BASE = "https://maps.googleapis.com/maps/api"
USER_AGENT = "clixen-agent/1.0"
SEARCH_RADIUS = "0.008"
_GOOGLE_MAPS_KEY = os.environ.get("GOOGLE_MAPS_API_KEY", "")


BUS_ETA_SCHEMA = {
    "type": "function",
    "function": {
        "name": "bus_eta",
        "description": (
            "Find MBTA stops near an address, get real-time bus/subway arrival estimates, "
            "and plan multi-modal transit routes (bus + subway, no commuter rail/Purple line) "
            "between two locations. Supports departure delay via depart_in. "
            "Only works for the Boston/Massachusetts area."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "origin": {
                    "type": "string",
                    "description": (
                        "Street address or place name, e.g. '3 Manning Terrace, Everett MA'"
                    ),
                },
                "destination": {
                    "type": "string",
                    "description": (
                        "Optional destination address or station name. "
                        "When provided, plans a multi-modal transit route "
                        "(bus + subway only, no commuter rail)."
                    ),
                    "default": "",
                },
                "depart_in": {
                    "type": "integer",
                    "description": (
                        "Minutes from now to depart. e.g. 5 means "
                        "'I will leave in 5 minutes'. Defaults to 0 (now)."
                    ),
                    "default": 0,
                },
            },
            "required": ["origin"],
        },
    },
}


# ── Helpers ──────────────────────────────────────────────────────────

def _geocode_google(address: str) -> tuple[float, float] | None:
    if not _GOOGLE_MAPS_KEY:
        return None
    url = f"{GOOGLE_MAPS_BASE}/geocode/json"
    params = {"address": address, "key": _GOOGLE_MAPS_KEY, "region": "us"}
    try:
        resp = httpx.get(url, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        if data.get("status") == "OK" and data.get("results"):
            loc = data["results"][0]["geometry"]["location"]
            return (float(loc["lat"]), float(loc["lng"]))
        return None
    except Exception as e:
        _log.warning("Google geocoding failed: %s", e)
        return None


def _geocode(address: str) -> tuple[float, float] | None:
    result = _geocode_google(address)
    if result:
        return result
    time.sleep(1)
    url = f"{NOMINATIM_BASE}/search"
    params = {"q": address, "format": "json", "limit": 1, "addressdetails": "0"}
    headers = {"User-Agent": USER_AGENT}
    try:
        resp = httpx.get(url, params=params, headers=headers, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        if data:
            return (float(data[0]["lat"]), float(data[0]["lon"]))
        return None
    except Exception as e:
        _log.error("Geocoding failed: %s", e)
        return None


def _directions_google(origin: str, destination: str,
                       arrival_time: datetime | None = None,
                       depart_in: int = 0,
                       ) -> str | None:
    if not _GOOGLE_MAPS_KEY:
        return None
    ref = datetime.now().astimezone() + timedelta(minutes=depart_in)
    url = f"{GOOGLE_MAPS_BASE}/directions/json"
    params = {
        "origin": origin,
        "destination": destination,
        "mode": "transit",
        "region": "us",
        "key": _GOOGLE_MAPS_KEY,
    }
    if arrival_time:
        params["arrival_time"] = int(arrival_time.timestamp())
    else:
        params["departure_time"] = int(ref.timestamp())
    try:
        resp = httpx.get(url, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        if data.get("status") != "OK" or not data.get("routes"):
            return None
        return _format_google_directions(data, ref)
    except Exception as e:
        _log.warning("Google Directions failed: %s", e)
        return None


def _format_google_directions(data: dict, ref_time: datetime) -> str:
    lines = []
    route = data["routes"][0]
    legs = route.get("legs", [])
    total_dur = 0
    for leg in legs:
        total_dur += leg.get("duration", {}).get("value", 0)

    for leg in legs:
        dep_stop = leg.get("departure_time", {})
        arr_stop = leg.get("arrival_time", {})
        dep_str = ""
        if dep_stop.get("text"):
            d = datetime.fromtimestamp(dep_stop["value"], tz=ref_time.tzinfo)
            mins = round((d - ref_time).total_seconds() / 60)
            dep_str = f"depart {dep_stop['text']} (in {mins} min)"
        arr_str = ""
        if arr_stop.get("text"):
            arr_str = f"arrive {arr_stop['text']}"

        for step in leg.get("steps", []):
            travel = step.get("travel_mode", "")
            instr = step.get("html_instructions", "").replace("<b>", "").replace("</b>", "").replace("<wbr/>", "").replace("<div style=\"font-size:0.9em\">", " (").replace("</div>", ")").replace("&nbsp;", " ")
            dur_str = step.get("duration", {}).get("text", "")
            dist_str = step.get("distance", {}).get("text", "")

            if travel == "TRANSIT":
                transit = step.get("transit_details", {})
                line = transit.get("line", {})
                vehicle = line.get("vehicle", {}).get("name", "").lower()
                short_name = line.get("short_name", "")
                long_name = line.get("long_name", "")
                headsign = transit.get("headsign", "")
                dep = transit.get("departure_stop", {}).get("name", "")
                arr = transit.get("arrival_stop", {}).get("name", "")
                num_stops = transit.get("num_stops", "?")

                label = f"{short_name}" if short_name else long_name
                lines.append(f"    {label} ({vehicle})")
                lines.append(f"      Board at {dep} → {arr} ({num_stops} stops)")
                lines.append(f"      Direction: {headsign}")
                lines.append(f"      {dur_str}")
            else:
                lines.append(f"    Walk {dist_str} — {dur_str}")

        if dep_str:
            lines.append(f"  Depart: {dep_str}")
        if arr_str:
            lines.append(f"  Arrive: {arr_str}")

    if total_dur > 0:
        mins = round(total_dur / 60)
        lines.append(f"\n  Total: ~{mins} min")

    lat = route["legs"][0]["end_location"]["lat"]
    lng = route["legs"][0]["end_location"]["lng"]
    lines.append(f"  Google Maps: https://www.google.com/maps/dir/?api=1&travelmode=transit&destination={lat},{lng}")

    return "\n".join(lines)


def _haversine_mi(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 3958.8
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    )
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _mbta_get(path: str, params: dict | None = None) -> dict:
    url = f"{MBTA_BASE}{path}"
    headers = {"Accept": "application/json"}
    for attempt in range(3):
        try:
            resp = httpx.get(url, params=params, headers=headers, timeout=15)
            if resp.status_code == 429:
                wait = 2 ** attempt
                _log.warning("MBTA 429 on %s — retrying in %ds", path, wait)
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPStatusError as e:
            _log.warning("MBTA %s: %s", e.response.status_code, path)
            return {"data": []}
        except Exception as e:
            _log.error("MBTA request failed: %s", e)
            return {"data": []}
    _log.error("MBTA 429 on %s — exhausted retries", path)
    return {"data": []}


def _format_arrival(iso_str: str, ref_time: datetime) -> tuple[int | None, str]:
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        if dt <= ref_time:
            return (0, "Arriving now")
        mins = round((dt - ref_time).total_seconds() / 60)
        return (mins, f"{mins} min ({dt.strftime('%I:%M %p').lstrip('0')})")
    except (ValueError, TypeError):
        return (None, iso_str)


def _get_direction_name(route_data: dict, dir_id: int) -> str:
    dir_dests = route_data.get("attributes", {}).get("direction_destinations", [])
    if dir_id < len(dir_dests) and dir_dests[dir_id]:
        return dir_dests[dir_id]
    return "Inbound" if dir_id == 1 else "Outbound"


def _fetch_predictions(lat: float, lon: float, radius: str = SEARCH_RADIUS) -> dict:
    raw = _mbta_get("/predictions", {
        "filter[latitude]": f"{lat:.6f}",
        "filter[longitude]": f"{lon:.6f}",
        "filter[radius]": radius,
        "include": "route,stop,trip",
        "page[limit]": 50,
    })
    included = raw.get("included", [])
    stop_map = {}
    for item in included:
        if item.get("type") == "stop":
            sid = item["id"]
            attrs = item.get("attributes", {})
            stop_map[sid] = {
                "name": attrs.get("name", "?"),
                "lat": attrs.get("latitude"),
                "lon": attrs.get("longitude"),
            }
    route_map = {}
    for item in included:
        if item.get("type") == "route":
            rid = item["id"]
            route_map[rid] = item
    preds = raw.get("data", [])
    by_route_dir: dict[tuple[str, int], list[str]] = defaultdict(list)
    stop_ids_seen: set[str] = set()
    for p in preds:
        attrs = p.get("attributes", {})
        rels = p.get("relationships", {})
        route_id = rels.get("route", {}).get("data", {}).get("id", "?")
        stop_id = rels.get("stop", {}).get("data", {}).get("id", "?")
        dir_id = attrs.get("direction_id", 0)
        arrival = attrs.get("arrival_time") or attrs.get("departure_time")
        if arrival:
            by_route_dir[(route_id, dir_id)].append(arrival)
            stop_ids_seen.add(stop_id)
    return {"by_route_dir": dict(by_route_dir), "stop_map": stop_map,
            "route_map": route_map, "stop_ids_seen": stop_ids_seen}


def _format_stops_near(lines: list[str], label: str,
                       lat: float, lon: float,
                       stop_map: dict, max_stops: int = 5):
    stops = []
    for sid, sinfo in stop_map.items():
        if sinfo["lat"] and sinfo["lon"]:
            d = _haversine_mi(lat, lon, float(sinfo["lat"]), float(sinfo["lon"]))
            stops.append((d, sid, sinfo["name"]))
    stops.sort(key=lambda x: x[0])
    lines.append(f"  Stops near {label}:")
    for dist, sid, sname in stops[:max_stops]:
        ds = f"{dist:.2f} mi" if dist < 1 else f"{dist:.1f} mi"
        lines.append(f"    {sname} ({ds})")


def _format_predictions(lines: list[str], by_route_dir: dict,
                        route_map: dict, ref_time: datetime):
    for (route_id, dir_id), arrivals in sorted(by_route_dir.items()):
        route_data = route_map.get(route_id, {})
        attrs = route_data.get("attributes", {})
        rname = attrs.get("short_name") or attrs.get("long_name", route_id)
        dir_label = _get_direction_name(route_data, dir_id)
        times = []
        for a in sorted(arrivals):
            _, desc = _format_arrival(a, ref_time)
            times.append(desc)
            if len(times) >= 3:
                break
        if times:
            lines.append(f"    Route {rname} → {dir_label}: next: {', '.join(times)}")





def _format_arrivals_simple(arrivals: list[str], ref_time: datetime) -> str:
    times = []
    for a in sorted(arrivals):
        _, desc = _format_arrival(a, ref_time)
        times.append(desc)
        if len(times) >= 2:
            break
    return ", ".join(times) if times else "No upcoming"


# ── Main entry ──────────────────────────────────────────────────────

def bus_eta(origin: str, destination: str = "", depart_in: int = 0) -> str:
    now = datetime.now().astimezone()
    ref_time = now + timedelta(minutes=depart_in)

    coords = _geocode(origin)
    if not coords:
        return f"Could not geocode '{origin}'."
    o_lat, o_lon = coords

    origin_data = _fetch_predictions(o_lat, o_lon)

    lines = [f"Nearest MBTA stops to '{origin}':"]
    _format_stops_near(lines, "origin", o_lat, o_lon, origin_data["stop_map"])

    if origin_data["by_route_dir"]:
        _format_predictions(lines, origin_data["by_route_dir"],
                            origin_data["route_map"], ref_time)
    else:
        lines.append("  No real-time predictions available.")

    if destination:
        lines.append(f"\n--- Route to '{destination}' ---")
        dest_coords = _geocode(destination)
        if dest_coords:
            d_lat, d_lon = dest_coords
            dest_data = _fetch_predictions(d_lat, d_lon)

            _format_stops_near(lines, "destination", d_lat, d_lon, dest_data["stop_map"])
            if dest_data["by_route_dir"]:
                dest_ref = now
                _format_predictions(lines, dest_data["by_route_dir"],
                                    dest_data["route_map"], dest_ref)

            # Direct bus options — routes with predictions near BOTH origin and destination
            origin_rids = set(rid for rid, _ in origin_data["by_route_dir"])
            dest_rids = set(rid for rid, _ in dest_data["by_route_dir"])
            direct_rids = origin_rids & dest_rids
            if direct_rids:
                lines.append("\n  Direct bus options (serves both areas):")
                for rid in sorted(direct_rids, key=int):
                    attrs = origin_data["route_map"].get(rid, {}).get("attributes", {})
                    rname = attrs.get("short_name") or attrs.get("long_name", rid)
                    o_arrivals = []
                    for (rid2, did2), arrs in origin_data["by_route_dir"].items():
                        if rid2 == rid:
                            o_arrivals.extend(arrs)
                    d_arrivals = []
                    for (rid2, did2), arrs in dest_data["by_route_dir"].items():
                        if rid2 == rid:
                            d_arrivals.extend(arrs)
                    o_str = _format_arrivals_simple(sorted(set(o_arrivals)), ref_time)
                    d_str = _format_arrivals_simple(sorted(set(d_arrivals)), now)
                    lines.append(f"    Route {rname}")
                    lines.append(f"      Board near origin: {o_str}")
                    lines.append(f"      Near destination: {d_str}")

            # Google Maps Directions (transit)
            dir_result = _directions_google(origin, destination, depart_in=depart_in)
            if dir_result:
                lines.append(f"\n  Google Maps transit directions:")
                lines.append(dir_result)
            else:
                dist = _haversine_mi(o_lat, o_lon, d_lat, d_lon)
                if dist < 1:
                    lines.append(f"\n  Destination is {dist:.1f} mi away — may be walkable.")
                else:
                    lines.append(f"\n  No transit directions available from Google Maps.")
                    lines.append(f"  Distance: {dist:.1f} mi straight line.")
        else:
            lines.append(f"  Could not geocode '{destination}'.")

    lines.append(f"\n  Current time: {now.strftime('%I:%M %p').lstrip('0')}  |  Depart in: {depart_in} min")
    lines.append("  Times are MBTA GPS predictions — actual arrival can shift ±5-10 min due to traffic/delays.")
    return "\n".join(lines)
