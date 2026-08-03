"""
DoorDash connector, rebuilt on agent-browser (`--auto-connect` to the user's
real, already-logged-in Chrome) after BrowserOS was ripped out. Same trick as
before: read the Apollo GraphQL cache via JS eval instead of clicking UI —
the cart drawer doesn't reliably open via scripted clicks, but the cache
holds the data regardless of whether the drawer ever renders.

No OAuth-click flow here (unlike the old BrowserOS version) — --auto-connect
attaches to the user's live session, so if they're logged into DoorDash in
that Chrome, this just works. If not, ask them to log in there once.
"""
from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path

from tools._agent_browser import eval_json, get_text, navigate

log = logging.getLogger("connector_doordash")
_DATA_DIR = Path.home() / ".config" / "g4l" / "data"

_ORDERS_URL = "https://www.doordash.com/orders/"
_CACHE_TYPENAME = "ConsumerOrderWithDetails"


def _extract_cache() -> dict | None:
    js = f"""
    (() => {{
        const a = window.__APOLLO_CLIENT__;
        if (!a) return JSON.stringify({{error:'no apollo'}});
        const c = a.cache.extract();
        const m = {{}};
        Object.keys(c).forEach(k => {{ if (k.startsWith('{_CACHE_TYPENAME}')) m[k] = c[k]; }});
        return JSON.stringify({{total:Object.keys(c).length, matched:Object.keys(m).length, data:m}});
    }})()
    """
    p = eval_json(js)
    return p if p.get("data") else None


def _extract_cart_cache() -> dict | None:
    js = """
    (() => {
        const a = window.__APOLLO_CLIENT__;
        if (!a) return JSON.stringify({error:'no apollo'});
        const c = a.cache.extract();
        const cartKey = Object.keys(c).find(k => k.startsWith('OrderCart:'));
        if (!cartKey) return JSON.stringify({error:'no cart'});
        const cart = c[cartKey];
        const items = [];
        (cart.orders || []).forEach(o => {
            const order = c[o.__ref];
            if (!order || !order.orderItems) return;
            order.orderItems.forEach(oi => {
                const item = c[oi.__ref];
                if (!item) return;
                items.push({
                    name: item.item ? item.item.name : null,
                    quantity: item.quantity,
                    price_cents: item.priceOfTotalQuantity,
                });
            });
        });
        const restKey = Object.keys(c).find(k => k.startsWith('Restaurant:'));
        return JSON.stringify({
            subtotal_cents: cart.subtotal,
            restaurant_id: restKey ? restKey.split(':')[1] : null,
            restaurant_name: restKey ? c[restKey].name : null,
            items: items,
        });
    })()
    """
    p = eval_json(js)
    return p if not p.get("error") else None


def _save(data: dict) -> str:
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    p = _DATA_DIR / f"doordash_orders_{time.strftime('%Y%m%d_%H%M%S')}.json"
    p.write_text(json.dumps(data, indent=2, default=str))
    return str(p)


def _not_logged_in() -> str | None:
    url = eval_json("JSON.stringify(window.location.href)")
    if isinstance(url, dict) and url.get("error"):
        return url["error"]
    return None


def get_my_doordash_cart() -> str:
    """Get items currently in your DoorDash cart. Reads the Apollo cache
    directly instead of opening the cart drawer UI."""
    nav = navigate("https://www.doordash.com/home")
    if nav.startswith("[agent-browser]"):
        return f"[doordash] {nav}"

    cart = _extract_cart_cache()
    if not cart:
        log.warning("DoorDash cart Apollo-cache extraction returned nothing.")
        return "[doordash] No active cart found — or you're not logged into DoorDash in that Chrome."

    items = cart.get("items", [])
    if not items:
        return "Your DoorDash cart is empty."

    lines = [f"Your DoorDash Cart ({len(items)} items):"]
    if cart.get("restaurant_name"):
        lines.append(f"Restaurant: {cart['restaurant_name']}")
    for it in items:
        price = f"${it['price_cents'] / 100:.2f}" if it.get("price_cents") is not None else ""
        lines.append(f"  {it.get('quantity', 1)}x {it.get('name', '?')} {price}")
    if cart.get("subtotal_cents") is not None:
        lines.append(f"\nSubtotal: ${cart['subtotal_cents'] / 100:.2f}")
    return "\n".join(lines)


def get_my_doordash_orders() -> str:
    """Get your DoorDash orders. Single call, returns formatted data."""
    nav = navigate(_ORDERS_URL)
    if nav.startswith("[agent-browser]"):
        return f"[doordash] {nav}"

    cache = _extract_cache()
    if cache:
        data = cache.get("data", {})
        items = list(data.values())
        path = _save(data)
        lines = [f"Your DoorDash Orders ({cache.get('matched', 0)} items, cache {cache.get('total', 0)} entries):",
                 f"Full data: {path}", ""]
        for item in items[:15]:
            t = item.get("__typename", "")
            if t == _CACHE_TYPENAME:
                created = (item.get("createdAt") or item.get("submittedAt") or "")[:10]
                status = "fulfilled" if item.get("fulfilledAt") else "cancelled" if item.get("cancelledAt") else "active"
                lines.append(f"  {created} | {status} | order {str(item.get('id',''))[:12]}")
        if len(items) > 15:
            lines.append(f"  ... + {len(items)-15} more cache entries")
        return "\n".join(lines)

    log.warning(
        "DoorDash Apollo-cache extraction returned no '%s' entries — falling back "
        "to raw text dump. Not logged in, or cache typename changed.",
        _CACHE_TYPENAME,
    )
    text = get_text()
    return f"[doordash] Could not read order cache (log into DoorDash in that Chrome?). Page text:\n{text[:800]}"


_ETA_RANGE_RE = re.compile(r"(\d{1,2}:\d{2})\s*[–-]\s*(\d{1,2}:\d{2}\s*(?:AM|PM))", re.I)
_STEP_RE = re.compile(r"Step\s+(\d+)\s+out\s+of\s+(\d+)", re.I)
_STATUS_PHRASES = [
    "Order confirmed", "Preparing your order", "Order is ready",
    "Dasher waiting for order", "Order picked up", "On its way",
    "Arriving soon", "Delivered",
]


def get_doordash_order_status(order_id: str = "") -> str:
    """Get delivery status/ETA for a DoorDash order. Pass an order id from
    get_my_doordash_orders(), or leave blank for your most recent active
    order. Reads the order UUID from the Apollo cache, then loads the live
    tracking page for ETA/status."""
    nav = navigate(_ORDERS_URL)
    if nav.startswith("[agent-browser]"):
        return f"[doordash] {nav}"

    cache = _extract_cache()
    if not cache:
        return "[doordash] Could not read order cache. Call get_my_doordash_orders() first."

    items = [v for v in cache.get("data", {}).values() if v.get("__typename") == _CACHE_TYPENAME]

    if order_id:
        match = next(
            (it for it in items if order_id in str(it.get("id", "")) or order_id == it.get("orderUuid")),
            None,
        )
    else:
        active = [it for it in items if not it.get("fulfilledAt") and not it.get("cancelledAt")]
        active.sort(key=lambda it: it.get("createdAt") or "", reverse=True)
        match = active[0] if active else None

    if not match:
        return "[doordash] No matching order found" + (f" for '{order_id}'" if order_id else " (no active orders)")

    if match.get("fulfilledAt"):
        return f"Order {match.get('id', '')} was delivered at {match['fulfilledAt']}."
    if match.get("cancelledAt"):
        return f"Order {match.get('id', '')} was cancelled at {match['cancelledAt']}."

    order_uuid = match.get("orderUuid")
    if not order_uuid:
        return "[doordash] Order found but missing tracking UUID."

    nav = navigate(f"https://www.doordash.com/orders/{order_uuid}/")
    if nav.startswith("[agent-browser]"):
        return f"[doordash] {nav}"

    text = get_text()
    eta_m = _ETA_RANGE_RE.search(text)
    step_m = _STEP_RE.search(text)
    status_line = next((p for p in _STATUS_PHRASES if p.lower() in text.lower()), "")

    if not eta_m and not status_line:
        log.warning(
            "DoorDash order-status page had no recognizable ETA/status text "
            "for order %s — tracking page layout likely changed.", match.get("id", ""),
        )
        return f"Order {match.get('id', '')} tracking page (could not parse status):\n{text[:500]}"

    lines = [f"Order {match.get('id', '')} status:"]
    if status_line:
        lines.append(f"  Status: {status_line}")
    if eta_m:
        lines.append(f"  ETA: {eta_m.group(1)} - {eta_m.group(2)}")
    if step_m:
        lines.append(f"  Progress: step {step_m.group(1)} of {step_m.group(2)}")
    return "\n".join(lines)


# =========================================================================
# Schema
# =========================================================================

GET_MY_DOORDASH_ORDERS_SCHEMA = {
    "type": "function", "function": {
        "name": "get_my_doordash_orders",
        "description": "Get your DoorDash delivery orders via your real, already-logged-in Chrome (agent-browser --auto-connect). Extracts order data from the Apollo page cache. Single call, returns formatted orders with dates, stores, and status.",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
}

GET_MY_DOORDASH_CART_SCHEMA = {
    "type": "function", "function": {
        "name": "get_my_doordash_cart",
        "description": "Get items currently in your DoorDash cart (name, quantity, price, subtotal). Reads the Apollo GraphQL cache directly — does not require opening the cart drawer UI.",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
}

GET_DOORDASH_ORDER_STATUS_SCHEMA = {
    "type": "function", "function": {
        "name": "get_doordash_order_status",
        "description": "Get delivery status and ETA for a DoorDash order. Pass order_id from get_my_doordash_orders(), or leave blank for your most recent active order.",
        "parameters": {"type": "object", "properties": {
            "order_id": {"type": "string", "description": "Order id (optional — defaults to most recent active order)", "default": ""},
        }, "required": []},
    },
}

SCHEMAS = [GET_MY_DOORDASH_ORDERS_SCHEMA, GET_MY_DOORDASH_CART_SCHEMA, GET_DOORDASH_ORDER_STATUS_SCHEMA]
