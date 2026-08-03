"""Resolve macOS Contacts names to phone/email handles and match iMessage.

Read-only. Uses AppleScript (Contacts.app) for the address book and reads
~/Library/Messages/chat.db for iMessage handle coverage. No contact-name
resolution was done by imessage_search.py, so this bridges the two.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
from typing import Optional


def _chat_db() -> str:
    for root, _dirs, files in os.walk(os.path.expanduser("~/Library/Messages")):
        if "chat.db" in files:
            return os.path.join(root, "chat.db")
    raise FileNotFoundError("chat.db not found")


def _run_applescript(script: str) -> str:
    out = subprocess.run(
        ["osascript", "-e", script],
        capture_output=True, text=True, timeout=60,
    )
    if out.returncode != 0:
        raise RuntimeError(out.stderr.strip() or "osascript failed")
    return out.stdout


def _query_contacts(name_query: str) -> str:
    """AppleScript that returns records for people whose name contains query.

    Dumps only matching contacts (fast) as name:::phones|:::emails|||.
    """
    esc = name_query.replace('"', '\\"')
    script = f'''
tell application "Contacts"
    set out to ""
    set matches to every person whose name contains "{esc}"
    repeat with p in matches
        try
            set pname to name of p
        on error
            set pname to ""
        end try
        set phonelist to ""
        repeat with ph in phones of p
            set phonelist to phonelist & "|" & (value of ph)
        end repeat
        set emaillist to ""
        repeat with em in emails of p
            set emaillist to emaillist & "|" & (value of em)
        end repeat
        set out to out & pname & ":::" & phonelist & ":::" & emaillist & "|||"
    end repeat
    return out
end tell
'''
    return _run_applescript(script)


def _normalize_num(raw: str) -> str:
    digits = re.sub(r"\D", "", raw)
    return digits[-10:] if len(digits) >= 10 else digits


def list_contacts(name_query: str = "") -> list[dict]:
    """Return contacts as [{name, phones, emails}]. Empty query = all (slow)."""
    if name_query:
        raw = _query_contacts(name_query)
    else:
        raw = _query_contacts("")  # matches everyone
    contacts = []
    for rec in raw.split("|||"):
        rec = rec.strip()
        if not rec:
            continue
        parts = rec.split(":::")
        name = parts[0].strip() if len(parts) > 0 else ""
        phones = [p for p in (parts[1].split("|") if len(parts) > 1 else []) if p.strip()]
        emails = [e for e in (parts[2].split("|") if len(parts) > 2 else []) if e.strip()]
        contacts.append({"name": name, "phones": phones, "emails": emails})
    return contacts


def find_contact(query: str) -> list[dict]:
    q = query.lower()
    return [c for c in list_contacts(query) if q in c["name"].lower()]


def imessage_handles() -> set[str]:
    db = _chat_db()
    con = sqlite3.connect(db)
    try:
        rows = con.execute("SELECT id FROM handle").fetchall()
    finally:
        con.close()
    return {r[0] for r in rows}


def resolve(name_query: str) -> list[dict]:
    """Find contacts matching name and flag which have an iMessage handle."""
    handles = imessage_handles()
    norm_handles = {_normalize_num(h) for h in handles}
    results = []
    for c in find_contact(name_query):
        matched = []
        for ph in c["phones"]:
            if _normalize_num(ph) in norm_handles:
                matched.append(ph)
        for em in c["emails"]:
            if em in handles:
                matched.append(em)
        c = dict(c)
        c["imessage_handles"] = matched
        c["has_imessage"] = bool(matched)
        results.append(c)
    return results


def main() -> None:
    import sys
    q = sys.argv[1] if len(sys.argv) > 1 else ""
    if q:
        print(json.dumps(resolve(q), indent=2, ensure_ascii=False))
    else:
        cs = list_contacts()
        print(json.dumps({"count": len(cs), "contacts": cs}, indent=2, ensure_ascii=False))


SCHEMA = {
    "type": "function",
    "function": {
        "name": "contacts_resolve",
        "description": (
            "Resolve a macOS Contacts name to phone numbers / emails and report whether each "
            "has an active iMessage handle in ~/Library/Messages/chat.db. Use when the user "
            "refers to a contact by name (e.g. 'Joe DS') and you need the concrete handle to "
            "search or send iMessage, or to confirm a named contact exists on this Mac. "
            "Read-only; queries Contacts.app via AppleScript and chat.db for handle coverage."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "Substring of the contact's name (case-insensitive). "
                        "e.g. 'Joe DS'. Returns all matching contacts."
                    ),
                },
                "resolve_handles": {
                    "type": "boolean",
                    "description": "If true, also flag which phones/emails appear as iMessage handles. Default true.",
                    "default": True,
                },
            },
            "required": ["query"],
        },
    },
}


def execute(query: str, resolve_handles: bool = True) -> str:
    """Tool entry point: returns JSON string of matching contacts."""
    if resolve_handles:
        results = resolve(query)
    else:
        results = find_contact(query)
    return json.dumps(results, indent=2, ensure_ascii=False)


if __name__ == "__main__":
    main()
