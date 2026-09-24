"""Unit tests for prep_animation pure helpers (no SAM3 model needed)."""

import numpy as np
import pytest

from tools.prep_animation import (
    MOTION_DESC,
    ZONE_FRACTIONS,
    _build_prompt,
    _derive_seed,
    _derive_zones,
)


def _subject_mask(shape=(512, 768), box=(100, 80, 660, 470)):
    x1, y1, x2, y2 = box
    m = np.zeros(shape, dtype=bool)
    m[y1:y2, x1:x2] = True
    m[y1 + 40:y2 - 40, x1 + 30:x2 - 30] = False  # hole -> ring shape
    return m


def test_derive_zones_bbox_and_area():
    m = _subject_mask()
    z = _derive_zones(m)
    assert z is not None
    assert z["bbox"] == [100, 80, 659, 469]
    assert z["subject_area_frac"] == pytest.approx(m.mean(), abs=1e-4)
    assert set(z["zones"]) == set(ZONE_FRACTIONS)


def test_derive_zones_head_above_torso():
    m = _subject_mask()
    z = _derive_zones(m)
    head = z["zones"]["head"]["bbox"]
    torso = z["zones"]["torso"]["bbox"]
    assert head[1] == 80
    assert torso[1] == head[3] == 197  # head occupies [80,197), torso [197,322)
    assert head[0] == torso[0] == 100
    assert head[2] == torso[2] == 659


def test_derive_zones_empty():
    assert _derive_zones(np.zeros((64, 64), dtype=bool)) is None


def test_derive_zones_deterministic():
    m = _subject_mask()
    assert _derive_zones(m) == _derive_zones(m.copy())


def test_build_prompt_lists_only_enabled():
    p = _build_prompt("person", ["breathing"])
    assert MOTION_DESC["breathing"] in p
    assert MOTION_DESC["head_sway"] not in p
    assert "Camera locked, background" in p
    assert "almost a still photo" in p


def test_build_prompt_all_off():
    p = _build_prompt("person", [])
    assert "no subject motion at all" in p


def test_build_prompt_custom_motion_overrides_toggles():
    p = _build_prompt("pen", ["breathing"], custom_motion="the pen rolls down the table and falls off the edge")
    assert "the pen rolls down the table and falls off the edge" in p
    assert MOTION_DESC["breathing"] not in p


def test_derive_seed_deterministic_and_differs():
    img = b"image-bytes"
    mask = b"mask-bytes"
    assert _derive_seed(img, mask) == _derive_seed(img, mask)
    assert _derive_seed(img, mask) != _derive_seed(img, b"other-mask")
