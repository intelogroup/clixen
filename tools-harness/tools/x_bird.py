"""X (Twitter) search backed by twscrape 0.19.2+ and browser cookies."""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

os.environ.setdefault("TWS_HTTP_BACKEND", "curl")

from twscrape import API

_log = logging.getLogger("x_bird")

_DB_PATH = os.environ.get("TWSCRAPE_ACCOUNTS_DB") or str(
    Path.home() / ".clixen" / "twscrape_accounts.db"
)
Path(_DB_PATH).parent.mkdir(parents=True, exist_ok=True)

_api: API | None = None


def _get_api() -> API:
    global _api
    if _api is None:
        _api = API(_DB_PATH)
    return _api


def _run(coro):
    return asyncio.run(coro)


SCHEMA_SEARCH = {
    "type": "function",
    "function": {
        "name": "x_search",
        "description": "Search X (Twitter) for recent tweets matching a query.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "X search query."},
                "count": {
                    "type": "integer",
                    "description": "Number of results (maximum 50).",
                    "default": 10,
                    "minimum": 1,
                    "maximum": 50,
                },
            },
            "required": ["query"],
        },
    },
}

SCHEMA_READ = {
    "type": "function",
    "function": {
        "name": "x_read_tweet",
        "description": "Read an X post by URL or numeric post ID.",
        "parameters": {
            "type": "object",
            "properties": {
                "tweet_id": {"type": "string", "description": "Post ID or full X URL."},
            },
            "required": ["tweet_id"],
        },
    },
}

SCHEMA_USER = {
    "type": "function",
    "function": {
        "name": "x_user_tweets",
        "description": "Get recent posts from an X account.",
        "parameters": {
            "type": "object",
            "properties": {
                "handle": {"type": "string", "description": "X username or @handle."},
                "count": {
                    "type": "integer",
                    "description": "Number of results (maximum 50).",
                    "default": 10,
                    "minimum": 1,
                    "maximum": 50,
                },
            },
            "required": ["handle"],
        },
    },
}


def _no_accounts_msg() -> str:
    return (
        "[x error] no active twscrape X accounts configured. Run once: "
        'twscrape --db "$HOME/.clixen/twscrape_accounts.db" add_cookie <X_USERNAME> '
        "(the cookie prompt is hidden; paste auth_token and ct0 there, never in chat)"
    )


async def _has_active_accounts() -> bool:
    accounts = await _get_api().pool.get_all()
    return any(account.active for account in accounts)


def _tweet_id_from_input(tweet_id: str) -> str:
    if "/" in tweet_id:
        return tweet_id.rstrip("/").split("/")[-1].split("?")[0]
    return tweet_id


def x_search(query: str, count: int = 10) -> str:
    async def _do():
        if not await _has_active_accounts():
            return _no_accounts_msg()
        results = [tweet async for tweet in _get_api().search(query, limit=count)]
        if not results:
            return f"No results for '{query}'."
        lines = [f"Found {len(results)} posts for '{query}':", ""]
        for tweet in results:
            lines.extend(
                (f"@{tweet.user.username}: {tweet.rawContent[:300]}", f"  {tweet.url}", "")
            )
        return "\n".join(lines)

    try:
        return _run(_do())
    except Exception as exc:
        _log.warning("x_search failed: %s", exc)
        return f"[x error] search failed: {exc}"


def x_read_tweet(tweet_id: str) -> str:
    post_id = _tweet_id_from_input(tweet_id)

    async def _do():
        if not await _has_active_accounts():
            return _no_accounts_msg()
        tweet = await _get_api().tweet_details(int(post_id))
        if not tweet:
            return f"Post {post_id} not found."
        return f"@{tweet.user.username}\n{tweet.rawContent}\n\n{tweet.url}"

    try:
        return _run(_do())
    except Exception as exc:
        _log.warning("x_read_tweet failed: %s", exc)
        return f"[x error] read failed: {exc}"


def x_user_tweets(handle: str, count: int = 10) -> str:
    username = handle.lstrip("@")

    async def _do():
        if not await _has_active_accounts():
            return _no_accounts_msg()
        user = await _get_api().user_by_login(username)
        if not user:
            return f"User @{username} not found."
        results = [
            tweet async for tweet in _get_api().user_tweets(user.id, limit=count)
        ]
        if not results:
            return f"No posts found for @{username}."
        lines = [f"Recent posts from @{username} (showing {len(results)}):", ""]
        for tweet in results:
            lines.extend((tweet.rawContent[:300], f"  {tweet.url}", ""))
        return "\n".join(lines)

    try:
        return _run(_do())
    except Exception as exc:
        _log.warning("x_user_tweets failed: %s", exc)
        return f"[x error] user posts failed: {exc}"
