"""Scrapling in the orb: the self-healing selector, and where its memory lives.

The point of granting it is narrow: sites redesign, and a hard-coded selector then
returns nothing (or worse, the wrong element). Scrapling saves a structural
fingerprint on the first read and RELOCATES the element afterwards. These tests hold
that behaviour, plus the storage rule — fingerprints must not land in the cwd, which
is read-only inside a signed .app.
"""
import sys
from pathlib import Path

import pytest

HARNESS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HARNESS))

from tools import scrapling_fetch as sf  # noqa: E402

PAGE_V1 = """<html><body><div class="card"><h2 class="product-title">Widget</h2>
<span class="price">$19.99</span></div></body></html>"""

# The site renamed .price -> .cost and moved it a level deeper. A CSS selector from
# yesterday returns nothing here; the adaptive lookup should still find $19.99.
PAGE_V2 = """<html><body><div class="card"><h2 class="product-title">Widget</h2>
<div class="buybox"><span class="cost">$19.99</span></div></div></body></html>"""


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("CLIXEN_SCRAPLING_DIR", str(tmp_path / "scrapling"))
    return tmp_path


def test_fingerprint_storage_follows_the_data_dir_not_the_cwd(tmp_path, monkeypatch):
    monkeypatch.delenv("CLIXEN_SCRAPLING_DIR", raising=False)
    monkeypatch.setenv("CLIXEN_DATA_DIR", str(tmp_path / "vault" / "data"))
    sf._adaptive_storage()
    assert (tmp_path / "vault" / "data" / "scrapling").is_dir()


def test_explicit_override_wins(tmp_path, monkeypatch):
    monkeypatch.setenv("CLIXEN_SCRAPLING_DIR", str(tmp_path / "custom"))
    sf._adaptive_storage()
    assert (tmp_path / "custom").is_dir()


def test_a_saved_selector_relocates_after_the_markup_changes(store):
    first = sf._scrapling_extract(PAGE_V1, ".price", domain="shop.test", mode="auto_save")
    assert "19.99" in first, first

    # Straight CSS on the new page returns nothing — that is the failure being fixed.
    raw = sf._scrapling_extract(PAGE_V2, ".price", domain="shop.test", mode="raw")
    assert "19.99" not in raw, f"the selector was expected to break: {raw}"

    healed = sf._scrapling_extract(PAGE_V2, ".price", domain="shop.test", mode="adaptive", similarity=40)
    assert "19.99" in healed, f"adaptive relocation did not find the element: {healed}"


def test_bad_selectors_return_a_message_instead_of_raising(store):
    out = sf._scrapling_extract(PAGE_V1, "[[[not a selector", domain="shop.test")
    assert isinstance(out, str) and "ok" in out.lower()


def test_empty_inputs_are_refused(store):
    assert "Empty HTML" in sf._scrapling_extract("", ".price", domain="x.test")
    assert "Empty selector" in sf._scrapling_extract(PAGE_V1, "", domain="x.test")
