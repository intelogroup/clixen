"""Single place that decides which URLs a browsing tool may load."""
from urllib.parse import urlparse

_ALLOWED = ("http", "https")


def navigable(url: str) -> bool:
    """http(s) with a host, or about:blank. Blocks file:, javascript:, data:, chrome:, etc."""
    u = url.strip()
    if u == "about:blank":
        return True
    p = urlparse(u)
    return p.scheme in _ALLOWED and bool(p.netloc)


def refusal(url: str) -> str:
    return f"Refused: only http/https URLs can be opened (got {url!r})."
