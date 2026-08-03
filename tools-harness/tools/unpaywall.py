"""
Unpaywall API — check if a DOI has an open-access version.

Returns the best open-access URL for a given DOI, or None if not available.
Can also download the OA PDF directly to disk via download_oa_pdf().

Requires UNPAYWALL_EMAIL env var for polite API access.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import requests

log = logging.getLogger("unpaywall")

_UNPAYWALL_API = "https://api.unpaywall.org/v2/"
_TIMEOUT = 8.0
_DOWNLOAD_TIMEOUT = 25.0
_MIN_PDF_BYTES = 5000
_USER_AGENT = "Mozilla/5.0 (research; +unpaywall-tool)"


def get_oa_url(doi: str) -> str | None:
    """Get open-access URL for a DOI via Unpaywall API."""
    if not doi:
        return None

    email = os.environ.get("UNPAYWALL_EMAIL", "")
    try:
        resp = requests.get(
            f"{_UNPAYWALL_API}{doi}",
            params={"email": email} if email else {},
            timeout=_TIMEOUT,
        )
        if resp.status_code != 200:
            return None

        data = resp.json()
        best = data.get("best_oa_location") or {}
        oa_url = best.get("url") or best.get("url_for_pdf") or best.get("url_for_landing_page")
        return oa_url

    except Exception:
        log.debug("unpaywall lookup failed for %s", doi)
        return None


def get_oa_pdf_url(doi: str) -> str | None:
    """Get the direct open-access PDF URL for a DOI, if one exists.

    Unlike get_oa_url(), this only returns a link that Unpaywall itself
    flags as pointing at a PDF (url_for_pdf) — it will not return a
    landing page that merely links to a PDF elsewhere.
    """
    if not doi:
        return None

    email = os.environ.get("UNPAYWALL_EMAIL", "")
    try:
        resp = requests.get(
            f"{_UNPAYWALL_API}{doi}",
            params={"email": email} if email else {},
            timeout=_TIMEOUT,
        )
        if resp.status_code != 200:
            return None

        data = resp.json()
        best = data.get("best_oa_location") or {}
        return best.get("url_for_pdf")

    except Exception:
        log.debug("unpaywall lookup failed for %s", doi)
        return None


def download_oa_pdf(doi: str, dest_path: str | Path) -> dict:
    """Download the open-access PDF for a DOI to dest_path.

    Returns a dict: {"ok": bool, "path": str | None, "bytes": int | None,
    "reason": str | None}. `reason` is set whenever ok is False, one of:
    "no_doi", "no_oa_pdf", "http_<status>", "not_a_pdf", "error:<msg>".

    dest_path's parent directory is created if missing. Any file at
    dest_path that isn't a valid PDF (too small, wrong magic bytes) is
    treated as a failed download and not written.
    """
    if not doi:
        return {"ok": False, "path": None, "bytes": None, "reason": "no_doi"}

    pdf_url = get_oa_pdf_url(doi)
    if not pdf_url:
        return {"ok": False, "path": None, "bytes": None, "reason": "no_oa_pdf"}

    try:
        resp = requests.get(
            pdf_url,
            timeout=_DOWNLOAD_TIMEOUT,
            headers={"User-Agent": _USER_AGENT},
            allow_redirects=True,
        )
        if resp.status_code != 200:
            return {
                "ok": False, "path": None, "bytes": None,
                "reason": f"http_{resp.status_code}",
            }

        content = resp.content
        if len(content) < _MIN_PDF_BYTES or content[:4] != b"%PDF":
            return {
                "ok": False, "path": None, "bytes": len(content),
                "reason": "not_a_pdf",
            }

        dest = Path(dest_path).expanduser()
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(content)
        return {"ok": True, "path": str(dest), "bytes": len(content), "reason": None}

    except Exception as exc:
        log.debug("unpaywall download failed for %s: %s", doi, exc)
        return {"ok": False, "path": None, "bytes": None, "reason": f"error:{exc}"}
