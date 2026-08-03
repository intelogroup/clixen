"""macOS system status — battery, disk, memory pressure, uptime, network.

All probes are read-only and stdlib-only (no PyObjC). Each section degrades
gracefully if the underlying CLI isn't available, so the tool always returns
*something* useful even on a stripped-down host.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


_TIMEOUT_S = 5


SCHEMA = {
    "type": "function",
    "function": {
        "name": "system_status",
        "description": (
            "Report this Mac's current system health: battery percentage / charging state, "
            "disk free on the boot volume, memory pressure, uptime, active Wi-Fi SSID, "
            "and load average. Use for 'is my mac low on battery?', 'how much disk do I have left?', "
            "'is my system overloaded?'. No arguments — returns a single multi-line summary."
        ),
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
}


def _run(cmd: list[str]) -> str:
    """Run a command, return stdout or empty string on any failure."""
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=_TIMEOUT_S)
    except (subprocess.TimeoutExpired, OSError):
        return ""
    return (result.stdout or "").strip()


def _battery() -> str:
    """Parse `pmset -g batt` → 'NN% (charging|discharging|charged); ~Hh remaining'."""
    out = _run(["pmset", "-g", "batt"])
    if not out:
        return "battery: unavailable"
    line = next((ln for ln in out.splitlines() if "%" in ln), "")
    if not line:
        return "battery: no internal battery"
    # Sample: " -InternalBattery-0 (id=...)\t73%; discharging; 4:21 remaining present: true"
    pct = ""
    state = ""
    remain = ""
    for chunk in line.split(";"):
        chunk = chunk.strip()
        if "%" in chunk and not pct:
            pct = chunk.split()[-1].rstrip(";")
        elif chunk in ("charging", "discharging", "charged", "AC attached", "finishing charge"):
            state = chunk
        elif "remaining" in chunk or "to full charge" in chunk:
            remain = chunk
    parts = [pct] if pct else []
    if state:
        parts.append(state)
    if remain:
        parts.append(remain)
    return "battery: " + (" · ".join(parts) if parts else line)


def _disk() -> str:
    """Free space on `/`. Falls back to df -h."""
    out = _run(["df", "-h", "/"])
    if not out:
        return "disk: unavailable"
    rows = out.splitlines()
    if len(rows) < 2:
        return f"disk: {out}"
    # Filesystem  Size  Used  Avail  Capacity  ...
    cols = rows[1].split()
    if len(cols) < 5:
        return f"disk: {rows[1]}"
    size, _used, avail, cap = cols[1], cols[2], cols[3], cols[4]
    return f"disk: {avail} free of {size} ({cap} used)"


def _memory() -> str:
    """Memory pressure (top -l 1 has 'PhysMem' line)."""
    out = _run(["top", "-l", "1", "-n", "0"])
    if not out:
        return "memory: unavailable"
    phys = next((ln for ln in out.splitlines() if ln.startswith("PhysMem:")), "")
    pressure = next(
        (ln for ln in out.splitlines() if "Memory pressure" in ln or "MemoryPressure" in ln),
        "",
    )
    parts = []
    if phys:
        # PhysMem: 18G used (1234M wired, ...), 6G unused.
        parts.append(phys.replace("PhysMem:", "").strip())
    if pressure:
        parts.append(pressure.strip())
    return "memory: " + (" | ".join(parts) if parts else "(top output unrecognized)")


def _uptime() -> str:
    out = _run(["uptime"])
    if not out:
        return "uptime: unavailable"
    # Sample: " 8:42  up 3 days,  2:14, 4 users, load averages: 1.23 2.34 3.45"
    return "uptime: " + out.split("up ", 1)[-1].strip() if "up " in out else f"uptime: {out}"


def _wifi_ssid() -> str:
    """Active SSID via `networksetup -getairportnetwork en0` (works on most Macs)."""
    # Try common Wi-Fi service names.
    for iface in ("en0", "en1"):
        out = _run(["networksetup", "-getairportnetwork", iface])
        if out and "Current Wi-Fi Network" in out:
            ssid = out.split(":", 1)[-1].strip()
            return f"wifi: {ssid} (on {iface})"
        if out and "not associated" in out.lower():
            return f"wifi: not connected ({iface})"
    return "wifi: unavailable"


def _hostname() -> str:
    out = _run(["scutil", "--get", "ComputerName"])
    return f"host: {out}" if out else f"host: {Path('/etc/hostname').read_text().strip() if Path('/etc/hostname').exists() else 'unknown'}"


def execute() -> str:
    if shutil.which("pmset") is None and shutil.which("df") is None:
        return "[system_status error] macOS-only (pmset/df not found)."
    return "\n".join([
        _hostname(),
        _battery(),
        _disk(),
        _memory(),
        _uptime(),
        _wifi_ssid(),
    ])
