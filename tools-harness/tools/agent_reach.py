"""
Chinese-internet search via agent-reach backends.

Zero-config (no login): Bilibili (bili-cli / opencli / public API) + Exa (mcporter).
Login-gated (need one-time Chrome/login): XiaoHongShu / Weibo / Douyin via `opencli`
(`agent-reach install --channels opencli`). These degrade gracefully to an error
line if opencli isn't installed or the platform isn't logged in — the other
channels still return.

Run `agent-reach doctor --json` to see which backends are live right now.
"""
from __future__ import annotations

import json
import re
import subprocess

_LOGIN_GATED = ("xiaohongshu", "weibo", "douyin")


def _run(cmd, timeout=30):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout, r.stderr
    except FileNotFoundError:
        return 127, "", f"not installed: {cmd[0]}"
    except subprocess.TimeoutExpired:
        return 124, "", "timed out"


def _bilibili_search(query: str, limit: int = 5) -> list[dict]:
    """Bilibili search. Prefer bili-cli, then opencli, then zero-config public API (curl)."""
    # 1) bili-cli
    rc, out, err = _run(["bili", "search", query, "--type", "video", "-n", str(limit)])
    if rc == 0 and out.strip():
        try:
            data = json.loads(out)
            items = data if isinstance(data, list) else data.get("results", data.get("data", []))
            return [
                {"title": i.get("title", ""), "author": i.get("author", ""),
                 "url": i.get("url") or i.get("arcurl", ""), "play": i.get("play", 0),
                 "description": (i.get("description") or "")[:200]}
                for i in items[:limit]
            ]
        except (json.JSONDecodeError, AttributeError):
            pass
    # 2) opencli
    rc, out, err = _run(["opencli", "bilibili", "search", query, "-f", "json"], timeout=40)
    if rc == 0 and out.strip():
        try:
            data = json.loads(out)
            return [
                {"title": i.get("title", ""), "author": i.get("author", ""),
                 "url": i.get("url", ""), "play": i.get("play", 0),
                 "description": (i.get("description") or "")[:200]}
                for i in (data if isinstance(data, list) else data.get("results", []))[:limit]
            ]
        except (json.JSONDecodeError, AttributeError):
            pass
    # 3) zero-config public API
    try:
        ua = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
        subprocess.run(["curl", "-s", "-c", "/tmp/bili_ck.txt", "-o", "/dev/null",
                        "-A", ua, "https://www.bilibili.com/"], timeout=10, capture_output=True)
        r = subprocess.run(
            ["curl", "-s", "-b", "/tmp/bili_ck.txt", "-A", ua, "-e", "https://www.bilibili.com/",
             f"https://api.bilibili.com/x/web-interface/search/all/v2?keyword={query}&page=1"],
            timeout=15, capture_output=True, text=True,
        )
        data = json.loads(r.stdout)
        for group in data.get("data", {}).get("result", []):
            if group.get("result_type") != "video":
                continue
            return [
                {"title": (i.get("title") or "").replace('<em class="keyword">', "").replace("</em>", ""),
                 "author": i.get("author", ""),
                 "url": "https:" + i["arcurl"] if i.get("arcurl", "").startswith("//") else i.get("arcurl", ""),
                 "play": i.get("play", 0),
                 "description": (i.get("description") or "")[:200]}
                for i in group.get("data", [])[:limit]
            ]
    except Exception as e:
        return [{"error": f"bilibili search failed: {e}"}]
    return []


def _exa_search(query: str, num_results: int = 5) -> list[dict]:
    """Semantic web search via Exa (mcporter, no API key)."""
    rc, out, err = _run(
        ["mcporter", "call", f'exa.web_search_exa(query: "{query}", numResults: {num_results})'],
        timeout=30,
    )
    if rc != 0:
        return [{"error": f"exa search failed: {err.strip()[:300]}"}]
    return [{"raw": out.strip()[:3000]}]


def _opencli_search(channel: str, query: str, limit: int = 5) -> list[dict]:
    """Login-gated channel (xiaohongshu / weibo) via opencli. Graceful on missing/login."""
    rc, out, err = _run(
        ["opencli", channel, "search", query, "-f", "json"], timeout=60,
    )
    if rc != 0:
        hint = " (opencli not installed or not logged in — run `agent-reach install --channels opencli`)"
        return [{"error": f"{channel} search failed: {err.strip()[:200]}{hint}" if err.strip() else f"{channel}: unavailable{hint}"}]
    try:
        data = json.loads(out)
        items = data if isinstance(data, list) else data.get("results", data.get("data", []))
        return [
            {"title": i.get("title", ""), "author": i.get("author", i.get("user", "")),
             "url": i.get("url", ""), "description": (i.get("description") or i.get("content") or "")[:200]}
            for i in items[:limit]
        ]
    except (json.JSONDecodeError, AttributeError):
        return [{"raw": out.strip()[:1500]}]


def _v2ex_hot(limit: int = 5) -> list[dict]:
    """Zero-config V2EX hot topics."""
    rc, out, err = _run(
        ["curl", "-s", "-H", "User-Agent: agent-reach/1.0", "https://www.v2ex.com/api/topics/hot.json"],
        timeout=15,
    )
    if rc != 0:
        return [{"error": f"v2ex failed: {err.strip()[:200]}"}]
    try:
        data = json.loads(out)
        return [
            {"title": i.get("title", ""), "url": f"https://www.v2ex.com/t/{i.get('id')}",
             "description": (i.get("content") or "")[:200]}
            for i in data[:limit]
        ]
    except json.JSONDecodeError as e:
        return [{"error": f"v2ex parse failed: {e}"}]


SEARCH_CHINESE_WEB_SCHEMA = {
    "type": "function",
    "function": {
        "name": "search_chinese_web",
        "description": (
            "Search the Chinese internet via agent-reach backends — Bilibili (video/content), "
            "Exa (semantic web), XiaoHongShu, Weibo, and Douyin (login-gated, need opencli login), "
            "and V2EX (tech community). Use for Chinese-language or China-specific topics that "
            "English-centric web_search (SearXNG/DDG) won't surface well. Zero-config channels "
            "(Bilibili, Exa, V2EX) work without login; XiaoHongShu/Weibo/Douyin return an error line "
            "until `agent-reach install --channels opencli` is done and the platform is logged in."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query, Chinese or English"},
                "channels": {
                    "type": "array",
                    "items": {"type": "string", "enum": ["bilibili", "exa", "xiaohongshu", "weibo", "douyin", "v2ex"]},
                    "description": "Channels to query (default: all)",
                },
            },
            "required": ["query"],
        },
    },
}


def search_chinese_web(args: dict) -> str:
    query = args["query"]
    channels = args.get("channels") or ["bilibili", "exa", "xiaohongshu", "weibo", "douyin", "v2ex"]

    sections = {
        "bilibili": ("## Bilibili", _bilibili_search),
        "exa": ("## Exa (semantic web search)", _exa_search),
        "xiaohongshu": ("## XiaoHongShu", lambda q: _opencli_search("xiaohongshu", q)),
        "weibo": ("## Weibo", lambda q: _opencli_search("weibo", q)),
        "douyin": ("## Douyin", lambda q: _opencli_search("douyin", q)),
        "v2ex": ("## V2EX (hot topics)", _v2ex_hot),
    }

    lines = [f"Chinese-web search results for: {query}\n"]
    for ch in channels:
        if ch not in sections:
            continue
        label, fn = sections[ch]
        try:
            res = fn(query) if ch != "v2ex" else fn()
        except Exception as e:
            res = [{"error": f"{ch} crashed: {e}"}]
        lines.append(label)
        if not res:
            lines.append("(no results)")
        elif "error" in res[0]:
            lines.append(f"({res[0]['error']})")
        else:
            for r in res:
                if r.get("raw"):
                    lines.append(r["raw"])
                    continue
                title = r.get("title", "")
                url = r.get("url", "")
                desc = r.get("description", "")
                extra = f" — {r.get('author', '')}" if r.get("author") else ""
                lines.append(f"- {title}{extra}\n  {url}\n  {desc}")

    return "\n".join(lines)
