"""macOS file-access awareness — who is reading files right now, and which
apps are permitted to.

Three probes, all read-only, stdlib-only:

1. `who_is_reading(path)`   — `lsof +D <path>` snapshot → per-PID open handles.
2. `apps_with_grants()`     — read the user-level TCC.db (sqlite) → which apps
                             hold FDA / Desktop / Documents / Downloads /
                             RemovableVolumes grants.
3. `tcc_denial_stream(...)` — stream TCC grant/deny decisions from unified
                             logging (`log stream/show`) to surface when an
                             app just gained/attempted access.

Honest ceiling (see THREAT_MODEL.md): lsof catches processes *holding* files
open — a fast open→read→close can slip the poll window, and reads by an app
with Full Disk Access are only fully visible under root. This is the
"who's touching your files right now" layer, not a full audit log.
"""
from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
import time
from pathlib import Path

_TIMEOUT_S = 8


def _run(cmd: list[str], timeout: int = _TIMEOUT_S) -> tuple[str, str, int]:
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (subprocess.TimeoutExpired, OSError):
        return "", "", -1
    return (result.stdout or ""), (result.stderr or ""), result.returncode


def _platform_ok() -> bool:
    return __import__("sys").platform == "darwin"


# =========================================================================
# 1. lsof snapshot
# =========================================================================

def who_is_reading(path: str, timeout: int = _TIMEOUT_S) -> dict:
    """Return processes with files open under `path` right now.

    Returns {ok, error?, processes:[{pid, process, user, files:[...]}]}.
    """
    if not _platform_ok():
        return {"ok": False, "error": "macOS-only (lsof file-access probe)."}
    if shutil.which("lsof") is None:
        return {"ok": False, "error": "lsof not found on PATH."}
    if not path or not Path(path).exists():
        return {"ok": False, "error": f"path does not exist: {path}"}

    out, err, code = _run(["lsof", "+D", path, "-F", "pcn"], timeout=timeout)
    if code != 0 and not out:
        # lsof exits 1 with no output when nothing under the path is open —
        # that's an empty result, not an error.
        if not err.strip() or "WARNING" in err:
            return {"ok": True, "processes": [], "path": path}
        return {"ok": False, "error": err.strip() or "lsof failed"}

    processes: dict[int, dict] = {}
    cur_pid: int | None = None
    for line in out.splitlines():
        if line.startswith("p"):
            cur_pid = int(line[1:])
            processes.setdefault(cur_pid, {"pid": cur_pid, "process": "", "user": "", "files": []})
        elif line.startswith("c") and cur_pid is not None:
            processes[cur_pid]["process"] = line[1:]
        elif line.startswith("n") and cur_pid is not None:
            name = line[1:]
            if name and name not in processes[cur_pid]["files"]:
                processes[cur_pid]["files"].append(name)

    result = [
        {
            "pid": p["pid"],
            "process": p["process"] or f"pid-{p['pid']}",
            "user": p["user"],
            "files": p["files"][:50],
        }
        for p in sorted(processes.values(), key=lambda x: x["process"].lower())
    ]
    return {"ok": True, "processes": result, "path": path}


# =========================================================================
# 2. TCC grant map
# =========================================================================

_TCC_SERVICES = {
    "kTCCServiceSystemPolicyAllFiles": "Full Disk Access",
    "kTCCServiceSystemPolicyAppData": "Files & Folders",
    "kTCCServiceSystemPolicyDesktopFolder": "Desktop",
    "kTCCServiceSystemPolicyDocumentsFolder": "Documents",
    "kTCCServiceSystemPolicyDownloadsFolder": "Downloads",
    "kTCCServiceSystemPolicyRemovableVolumes": "Removable Volumes",
    "kTCCServiceSystemPolicyNetworkVolumes": "Network Volumes",
    "kTCCServiceFileProviderDomain": "iCloud Drive",
}

_TCC_DB_CANDIDATES = [
    Path.home() / "Library/Application Support/com.apple.TCC/TCC.db",
    Path("/Library/Application Support/com.apple.TCC/TCC.db"),
]


def apps_with_grants() -> dict:
    """Read user + system TCC.db (read-only) → apps with folder/FDA grants.

    FDA (Full Disk Access) rows live in the system db; folder grants live in
    the user db. Both are world-readable sqlite, opened mode=ro. auth_value 0
    = denied; any other value = granted (2 = explicit allow, 4/5 = variant
    states seen for Calendar/AppData).

    Returns {ok, grants:[{app, access:[...]}], dbs:[...], error?}.
    """
    if not _platform_ok():
        return {"ok": False, "error": "macOS-only (TCC grant map)."}

    dbs = [p for p in _TCC_DB_CANDIDATES if p.exists()]
    if not dbs:
        return {"ok": False, "error": "TCC.db not found."}

    rows: list[tuple[str, str, int]] = []
    db_paths: list[str] = []
    for db_path in dbs:
        db_paths.append(str(db_path))
        try:
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=3)
        except sqlite3.Error as exc:
            return {"ok": False, "error": f"cannot open TCC.db read-only: {exc}"}
        try:
            rows.extend(
                conn.execute(
                    "SELECT service, client, auth_value FROM access "
                    "WHERE service IN ('" + "','".join(_TCC_SERVICES) + "')"
                ).fetchall()
            )
        except sqlite3.Error as exc:
            return {"ok": False, "error": f"TCC.db query failed: {exc}"}
        finally:
            conn.close()

    by_client: dict[str, dict] = {}
    for service, client, auth_value in rows:
        if auth_value == 0:  # 0 = denied
            continue
        entry = by_client.setdefault(client, {"app": client, "access": []})
        label = _TCC_SERVICES.get(service, service)
        if label not in entry["access"]:
            entry["access"].append(label)

    grants = sorted(by_client.values(), key=lambda x: (len(x["access"]), x["app"].lower()))
    return {"ok": True, "grants": grants, "dbs": db_paths}


# =========================================================================
# 3. TCC live decision watcher (Tier-0.5)
# =========================================================================

_TCC_PREDICATE = 'subsystem == "com.apple.TCC"'


def tcc_denial_stream(seconds: int = 10, level: str = "debug") -> dict:
    """Stream TCC grant/deny decisions for a window.

    Uses `log stream` (live) — may require admin rights to see TCC subsystem
    events; degrades to `log show` over the window if the stream is unusable.

    Returns {ok, events:[{app, decision, service?, reason?}], error?}.
    """
    if not _platform_ok():
        return {"ok": False, "error": "macOS-only (TCC decision watcher)."}
    if shutil.which("log") is None:
        return {"ok": False, "error": "log(1) not found."}

    seconds = max(1, min(int(seconds), 120))

    # `log stream` for `seconds` and capture; killed by our own timeout.
    try:
        proc = subprocess.Popen(
            ["log", "stream", "--predicate", _TCC_PREDICATE, "--level", level],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        try:
            out, err = proc.communicate(timeout=seconds + 1)
        except subprocess.TimeoutExpired:
            proc.terminate()
            try:
                out, err = proc.communicate(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
                out, err = proc.communicate()
    except OSError as exc:
        return {"ok": False, "error": f"log stream failed: {exc}"}

    if not out.strip():
        # Fall back to `log show` over the window (works without an active stream).
        out, _err, code = _run(
            ["log", "show", "--last", f"{seconds}s", "--predicate", _TCC_PREDICATE],
            timeout=seconds + 5,
        )
        if code != 0 and not out:
            return {"ok": False, "error": "log stream produced no events and log show failed."}

    events = []
    for line in out.splitlines():
        evt = _parse_tcc_line(line)
        if evt:
            events.append(evt)

    return {"ok": True, "events": events[:100], "window_s": seconds}


def _parse_tcc_line(line: str) -> dict | None:
    """Best-effort parse of a TCC log line into {app, decision, service}.

    Real line shapes (macOS 13+):
      Granting TCCDProcess: identifier=com.apple.x, pid=N, binary_path=/p ...
      Platform binary prompting is 'Deny' because: is Platform Binary  (noise)
    """
    low = line.lower()
    if "tccd" not in low and "tcc" not in low:
        return None
    if "platform binary prompting" in low:
        return None  # system-binary noise, no app decision
    evt = {}
    for key, label in (("granting", "granted"), ("denying", "denied")):
        if key in low:
            evt["decision"] = label
            break
    if "decision" not in evt and "deny" in low and "because" in low:
        evt["decision"] = "denied"
    if "decision" not in evt:
        evt["decision"] = "observed"
    for svc_key, label in _TCC_SERVICES.items():
        if svc_key.lower() in low or label.lower() in low:
            evt["service"] = label
            break
    if not evt:
        return None
    # App extraction: prefer the explicit identifier= bundle id, then binary_path.
    import re
    m = re.search(r"identifier=([A-Za-z0-9\-_.]+)", line)
    if m:
        evt["app"] = m.group(1)
    elif m := re.search(r"binary_path=([^\s,]+)", line):
        evt["app"] = m.group(1)
    # Fallback .app token, but require a boundary after ".app" so "com.apple.TCC"
    # (which contains ".app" + "le") never matches.
    else:
        for m in re.finditer(r"([A-Za-z0-9\-_.]+\.app)(?:[/\"\s]|$)", line):
            token = m.group(1)
            if token.lower().endswith(".app"):
                evt["app"] = token
                break
    evt.setdefault("app", "unknown")
    return evt


# =========================================================================
# Watch loop (poll + diff)
# =========================================================================

def watch(path: str, interval: float = 2.0, window: float = 30.0) -> dict:
    """Poll `who_is_reading` over a window, diff snapshots, return everything.

    Returns {ok, path, samples:[...], appeared:[{process,pid,first_seen}], error?}.
    """
    if not _platform_ok():
        return {"ok": False, "error": "macOS-only (file-access watch)."}
    interval = max(0.5, float(interval))
    window = max(1.0, float(window))

    first = who_is_reading(path)
    if not first.get("ok"):
        return first

    samples = [{"t": 0.0, "processes": first["processes"]}]
    appeared: list[dict] = []
    seen: dict[int, float] = {p["pid"]: 0.0 for p in first["processes"]}

    deadline = time.monotonic() + window
    while time.monotonic() < deadline:
        time.sleep(interval)
        snap = who_is_reading(path)
        if not snap.get("ok"):
            break
        now = time.monotonic()
        elapsed = round(now - (deadline - window), 1)
        samples.append({"t": elapsed, "processes": snap["processes"]})
        for p in snap["processes"]:
            pid = p["pid"]
            if pid not in seen:
                seen[pid] = elapsed
                appeared.append({"pid": pid, "process": p["process"], "first_seen_s": elapsed})

    return {
        "ok": True,
        "path": path,
        "samples": samples[-20:],
        "appeared": appeared,
    }


# =========================================================================
# Tool schema + entry point
# =========================================================================

SCHEMA = {
    "type": "function",
    "function": {
        "name": "file_access_watch",
        "description": (
            "Inspect which processes are currently reading files under a path (macOS). "
            "Returns the current lsof snapshot, any processes that appeared during the "
            "watch window, and the map of apps holding Full Disk Access / Desktop / "
            "Documents / Downloads / Removable Volumes grants in TCC. Use for "
            "'what's reading my files?', 'who has access to my documents?', "
            "'is anything touching my vault?'. NOTE: this catches processes holding "
            "files open — quick open/read/close can be missed, and it is not an "
            "audit log for Full-Disk-Access apps."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Directory to inspect (recursive). Defaults to the clixen workspace.",
                },
                "seconds": {
                    "type": "number",
                    "description": "Watch window in seconds (default 5, max 120).",
                },
                "interval": {
                    "type": "number",
                    "description": "Poll interval in seconds (default 2.0, min 0.5).",
                },
            },
            "required": [],
        },
    },
}


def execute(args: dict) -> str:
    """Tool entry point. Mirrors system_status.execute() shape."""
    path = (args.get("path") or "").strip()
    seconds = args.get("seconds", 5)
    interval = args.get("interval", 2.0)

    if not path:
        try:
            from tools.path_policy import CLIXEN_DATA_DIR
            path = str(CLIXEN_DATA_DIR)
        except Exception:
            pass

    snapshot = who_is_reading(path) if path else {"ok": True, "processes": []}
    if path and not snapshot.get("ok"):
        return f"[file_access_watch error] {snapshot['error']}"

    appeared: list[dict] = []
    if path and seconds and seconds > 0:
        watch_result = watch(path, interval=interval, window=min(float(seconds), 120))
        appeared = watch_result.get("appeared", [])

    grants = apps_with_grants()

    lines = ["FILE ACCESS"]
    if path:
        lines.append(f"path: {path}")
        procs = snapshot.get("processes", [])
        if not procs:
            lines.append("  currently reading: (none)")
        else:
            lines.append("  currently reading:")
            for p in procs[:15]:
                files = ", ".join(p["files"][:3])
                lines.append(f"    pid {p['pid']} {p['process']} — {files}")
        if appeared:
            lines.append("  newly appeared during watch:")
            for a in appeared[:10]:
                lines.append(f"    {a['process']} (pid {a['pid']}) at +{a['first_seen_s']}s")
    else:
        lines.append("  path: (none given — grant map only)")

    lines.append("  apps with TCC grants:")
    if not grants.get("ok"):
        lines.append(f"    (unavailable: {grants.get('error')})")
    else:
        grant_list = grants["grants"]
        if not grant_list:
            lines.append("    (no apps have folder/FDA grants)")
        for g in grant_list[:20]:
            lines.append(f"    {g['app']} → {', '.join(g['access'])}")

    return "\n".join(lines)


if __name__ == "__main__":
    # Standalone dry-run: python tools/file_access.py [path] [seconds]
    import sys

    test_path = sys.argv[1] if len(sys.argv) > 1 else ""
    print(json.dumps(execute({"path": test_path, "seconds": 3, "interval": 1.0}), indent=2))
