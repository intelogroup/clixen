"""Apify Actors tool — run any Apify Actor (web scraping / automation) via the Apify API.

One tool to run an actor and fetch its dataset output, plus a helper to list the
user's own actors. Auth is the personal API token in APIFY_API_TOKEN (migrated to
Keychain by tools/env_secrets.py like every other provider key).
"""

from __future__ import annotations

import json
import logging
import os
import time

import requests

log = logging.getLogger(__name__)

API_BASE = "https://api.apify.com/v2"
_POLL_INTERVAL_S = 5.0

_TERMINAL_STATUSES = {"SUCCEEDED", "FAILED", "ABORTED", "TIMED-OUT"}

# The user's own private actor (username joyful_bind), exposed as a named shortcut.
MY_ACTOR_ID = "cX2jKt7CYWGgpzR32"

RUN_ACTOR_SCHEMA = {
    "type": "function",
    "function": {
        "name": "apify_run_actor",
        "description": (
            "Run an Apify Actor (web scraping / automation task in the cloud) and return its "
            "output dataset. Pass the actor ID (e.g. 'apify/website-content-crawler', "
            "'apify/google-search-scraper', 'apify/instagram-scraper', or your own "
            "'username~actor-name') and its input JSON. Each actor documents its own input "
            "schema on apify.com. Use this for scraping sites that block plain HTTP fetches, "
            "search-engine result scraping, social-media extraction, or any Apify marketplace "
            "actor. For a simple page read prefer web_fetch/scrapling_fetch first."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "actor_id": {
                    "type": "string",
                    "description": "Apify actor ID, e.g. 'apify/website-content-crawler' or 'username~actor-name'.",
                },
                "input": {
                    "type": "object",
                    "description": "Actor input payload (JSON object). Check the actor's docs for its required keys.",
                    "default": {},
                },
                "max_results": {
                    "type": "integer",
                    "description": "Maximum number of dataset items to return (default 20).",
                    "default": 20,
                },
                "wait_seconds": {
                    "type": "integer",
                    "description": "Maximum time in seconds to wait for the run to finish (default 300, max 900).",
                    "default": 300,
                },
            },
            "required": ["actor_id"],
        },
    },
}

LIST_ACTORS_SCHEMA = {
    "type": "function",
    "function": {
        "name": "apify_list_actors",
        "description": (
            "List your own Apify actors (name, ID, description). Use before apify_run_actor "
            "when you need to discover the exact actor ID of an actor you built or use often."
        ),
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
}

RUN_MY_ACTOR_SCHEMA = {
    "type": "function",
    "function": {
        "name": "apify_run_my_actor",
        "description": (
            "Run your own private Apify actor 'my-actor' (id cX2jKt7CYWGgpzR32, username "
            "joyful_bind). This is the starter actor — it echoes whatever you pass in `input` "
            "straight into its output dataset, so use it as a scratch/test actor or replace its "
            "code on apify.com to give it real behavior. Pass any JSON object as `input`."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "input": {
                    "type": "object",
                    "description": "Actor input payload (any JSON object). The starter actor echoes it back.",
                    "default": {},
                },
                "max_results": {
                    "type": "integer",
                    "description": "Maximum number of dataset items to return (default 20).",
                    "default": 20,
                },
                "wait_seconds": {
                    "type": "integer",
                    "description": "Maximum time in seconds to wait for the run to finish (default 300, max 900).",
                    "default": 300,
                },
            },
            "required": [],
        },
    },
}


def _token() -> str:
    return os.environ.get("APIFY_API_TOKEN", "").strip()


def _run_actor(actor_id: str, input_data: dict, max_results: int, wait_seconds: int) -> str:
    token = _token()
    if not token:
        return (
            "[apify error] APIFY_API_TOKEN not set in environment — add it to tools-harness/.env "
            "and restart, or set it via the Keychain."
        )

    wait_seconds = max(1, min(wait_seconds, 900))
    max_results = max(1, min(max_results, 200))

    try:
        start = requests.post(
            f"{API_BASE}/acts/{actor_id}/runs",
            params={"token": token},
            json={"input": input_data or {}, "maxResults": max_results},
            timeout=30,
        )
        if start.status_code not in (200, 201):
            return (
                f"[apify error] starting actor {actor_id!r} failed: HTTP {start.status_code} "
                f"{start.text[:300]}"
            )
        run = start.json().get("data", {})
    except Exception as e:
        return f"[apify error] starting actor {actor_id!r} failed: {e}"

    run_id = run.get("id")
    dataset_id = run.get("defaultDatasetId")
    status = run.get("status")

    deadline = time.time() + wait_seconds
    while status not in _TERMINAL_STATUSES and time.time() < deadline:
        time.sleep(_POLL_INTERVAL_S)
        try:
            r = requests.get(
                f"{API_BASE}/actor-runs/{run_id}", params={"token": token}, timeout=30
            )
            if r.status_code == 200:
                status = r.json().get("data", {}).get("status", status)
            else:
                break
        except Exception:
            break

    if status not in ("SUCCEEDED",):
        return (
            f"[apify error] actor {actor_id!r} run {run_id} ended with status {status!r} "
            f"(after ~{int(wait_seconds - (deadline - time.time()))}s). Check the run in the "
            f"Apify console; the actor may need different input."
        )

    items = []
    if dataset_id:
        try:
            d = requests.get(
                f"{API_BASE}/datasets/{dataset_id}/items",
                params={"token": token, "format": "json", "limit": max_results},
                timeout=30,
            )
            if d.status_code == 200:
                items = d.json()
        except Exception as e:
            return f"[apify error] fetching dataset {dataset_id} failed: {e}"

    if not items:
        return (
            f"apify actor {actor_id!r} run {run_id} succeeded but returned no dataset items "
            f"(status={status})."
        )

    parts = [f"apify actor {actor_id!r} (run {run_id}) — {len(items)} result(s):"]
    for i, item in enumerate(items, 1):
        parts.append(f"--- item {i} ---")
        parts.append(json.dumps(item, ensure_ascii=False, default=str)[:4000])
    return "\n".join(parts)


def _list_actors() -> str:
    token = _token()
    if not token:
        return (
            "[apify error] APIFY_API_TOKEN not set in environment — add it to tools-harness/.env "
            "and restart, or set it via the Keychain."
        )
    try:
        r = requests.get(
            f"{API_BASE}/acts",
            params={"token": token, "my": 1, "desc": 1},
            timeout=30,
        )
        if r.status_code != 200:
            return f"[apify error] listing actors failed: HTTP {r.status_code} {r.text[:300]}"
        actors = r.json().get("data", {}).get("items", [])
    except Exception as e:
        return f"[apify error] listing actors failed: {e}"

    if not actors:
        return "No Apify actors found on this account."

    parts = [f"{len(actors)} actor(s) on this Apify account:"]
    for a in actors:
        parts.append(
            f"- {a.get('id', '')}  {a.get('name', '')}  ({a.get('username', '')})  "
            f"{a.get('description', '') or a.get('title', '')}"
        )
    return "\n".join(parts)


def apify_run_actor(actor_id: str, input: dict | None = None, max_results: int = 20, wait_seconds: int = 300) -> str:
    return _run_actor(actor_id, input or {}, max_results, wait_seconds)


def apify_list_actors() -> str:
    return _list_actors()


def apify_run_my_actor(input: dict | None = None, max_results: int = 20, wait_seconds: int = 300) -> str:
    return _run_actor(MY_ACTOR_ID, input or {}, max_results, wait_seconds)
