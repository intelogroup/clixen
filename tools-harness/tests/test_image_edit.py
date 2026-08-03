"""Coverage for tools/image_edit.py — resize/crop/rotate/convert for PNG/SVG.

Two layers: mocked subprocess tests (fast, deterministic, pin the exact
command each operation shells out to) plus a handful of real-binary
integration tests against actual ImageMagick/rsvg-convert output (these were
verified live against a real diagram render before this test file existed —
pinning that same behavior here so a regression is caught by CI, not just by
re-running the manual smoke test).
"""
import os
import subprocess
from unittest.mock import patch

import pytest

from tools.image_edit import edit_image_executor
from tools.registry import ALL_TOOLS, EXECUTORS, TOOL_TAGS, execute_tool


# --- Registry wiring ---------------------------------------------------------

def test_edit_image_is_registered_with_matching_executor():
    names = {t["function"]["name"] for t in ALL_TOOLS}
    assert "edit_image" in names
    assert "edit_image" in EXECUTORS


def test_edit_image_is_tagged_fs_for_automatic_path_denylist():
    """The 'fs' tag is what makes registry.execute_tool()'s _denylist_violation
    check fire automatically on the 'path'/'output_path' args — no bespoke
    path validation needed in this tool itself."""
    assert "fs" in TOOL_TAGS.get("edit_image", frozenset())


def test_sensitive_path_is_blocked_by_the_shared_denylist():
    result = execute_tool("edit_image", {
        "path": "/etc/passwd", "operation": "convert", "output_path": "/tmp/x.png",
    })
    assert "[blocked]" in result


# --- Argument validation (no subprocess calls) -------------------------------

def test_missing_path_returns_error():
    assert "error" in edit_image_executor({"operation": "resize", "width": 100})


def test_nonexistent_input_returns_error():
    result = edit_image_executor({"path": "/tmp/does_not_exist_xyz.png", "operation": "resize", "width": 100})
    assert "not found" in result


def test_unknown_operation_returns_error():
    result = edit_image_executor({"path": __file__, "operation": "sparkle"})
    assert "unsupported operation" in result


def test_resize_without_width_or_height_returns_error():
    result = edit_image_executor({"path": __file__, "operation": "resize"})
    assert "width" in result and "height" in result


def test_crop_without_dimensions_returns_error():
    result = edit_image_executor({"path": __file__, "operation": "crop"})
    assert "crop_width" in result


def test_rotate_without_degrees_returns_error():
    result = edit_image_executor({"path": __file__, "operation": "rotate"})
    assert "degrees" in result


def test_svg_to_svg_convert_rejected_as_a_no_op(tmp_path):
    svg = tmp_path / "in.svg"
    svg.write_text("<svg></svg>")
    result = edit_image_executor({
        "path": str(svg), "operation": "convert", "output_path": str(tmp_path / "out.svg"),
    })
    assert "no-op" in result


# --- Mocked subprocess: pin the exact command per operation ------------------

def _fake_success(*a, **k):
    return subprocess.CompletedProcess(args=a, returncode=0, stdout="", stderr="")


def test_resize_raster_calls_magick_with_geometry_string(tmp_path):
    src = tmp_path / "in.png"
    src.write_bytes(b"fake")
    out = tmp_path / "out.png"

    with patch("tools.image_edit._run") as mock_run, \
         patch("os.path.exists", return_value=True), \
         patch("os.path.getsize", return_value=100):
        edit_image_executor({"path": str(src), "operation": "resize", "width": 400, "height": 300, "output_path": str(out)})

    mock_run.assert_called_once_with(["magick", str(src), "-resize", "400x300", str(out)])


def test_resize_svg_calls_rsvg_convert_with_w_h_flags(tmp_path):
    src = tmp_path / "in.svg"
    src.write_text("<svg></svg>")
    out = tmp_path / "out.png"

    with patch("tools.image_edit._run") as mock_run, \
         patch("os.path.exists", return_value=True), \
         patch("os.path.getsize", return_value=100):
        edit_image_executor({"path": str(src), "operation": "resize", "width": 400, "output_path": str(out)})

    mock_run.assert_called_once_with(["rsvg-convert", str(src), "-o", str(out), "-w", "400"])


def test_crop_raster_calls_magick_crop_with_repage(tmp_path):
    src = tmp_path / "in.png"
    src.write_bytes(b"fake")
    out = tmp_path / "out.png"

    with patch("tools.image_edit._run") as mock_run, \
         patch("os.path.exists", return_value=True), \
         patch("os.path.getsize", return_value=100):
        edit_image_executor({
            "path": str(src), "operation": "crop",
            "crop_width": 100, "crop_height": 50, "crop_x": 5, "crop_y": 10,
            "output_path": str(out),
        })

    mock_run.assert_called_once_with(["magick", str(src), "-crop", "100x50+5+10", "+repage", str(out)])


def test_crop_svg_rasterizes_first_then_crops_and_cleans_up_temp(tmp_path):
    src = tmp_path / "in.svg"
    src.write_text("<svg></svg>")
    out = tmp_path / "out.png"
    raster_tmp = str(out) + ".rasterized.png"

    calls = []

    def _record(cmd):
        calls.append(cmd)
        if cmd[0] == "rsvg-convert":
            # actually create the temp file so the cleanup unlink has something to remove
            open(raster_tmp, "wb").close()

    with patch("tools.image_edit._run", side_effect=_record), \
         patch("os.path.getsize", return_value=100):
        edit_image_executor({
            "path": str(src), "operation": "crop", "crop_width": 50, "crop_height": 50,
            "output_path": str(out),
        })

    assert calls[0] == ["rsvg-convert", str(src), "-o", raster_tmp]
    assert calls[1] == ["magick", raster_tmp, "-crop", "50x50+0+0", "+repage", str(out)]
    assert not os.path.exists(raster_tmp)  # temp rasterized file cleaned up


def test_rotate_raster_calls_magick_rotate(tmp_path):
    src = tmp_path / "in.png"
    src.write_bytes(b"fake")
    out = tmp_path / "out.png"

    with patch("tools.image_edit._run") as mock_run, \
         patch("os.path.exists", return_value=True), \
         patch("os.path.getsize", return_value=100):
        edit_image_executor({"path": str(src), "operation": "rotate", "degrees": 90, "output_path": str(out)})

    mock_run.assert_called_once_with(["magick", str(src), "-rotate", "90", str(out)])


def test_convert_svg_calls_rsvg_convert_with_format_flag_from_extension(tmp_path):
    src = tmp_path / "in.svg"
    src.write_text("<svg></svg>")
    out = tmp_path / "out.jpg"

    with patch("tools.image_edit._run") as mock_run, \
         patch("os.path.exists", return_value=True), \
         patch("os.path.getsize", return_value=100):
        edit_image_executor({"path": str(src), "operation": "convert", "output_path": str(out)})

    mock_run.assert_called_once_with(["rsvg-convert", "-f", "jpg", str(src), "-o", str(out)])


def test_convert_raster_calls_bare_magick_src_to_out(tmp_path):
    src = tmp_path / "in.png"
    src.write_bytes(b"fake")
    out = tmp_path / "out.gif"

    with patch("tools.image_edit._run") as mock_run, \
         patch("os.path.exists", return_value=True), \
         patch("os.path.getsize", return_value=100):
        edit_image_executor({"path": str(src), "operation": "convert", "output_path": str(out)})

    mock_run.assert_called_once_with(["magick", str(src), str(out)])


def test_missing_binary_returns_helpful_error(tmp_path):
    src = tmp_path / "in.png"
    src.write_bytes(b"fake")

    with patch("tools.image_edit._run", side_effect=FileNotFoundError("magick")):
        result = edit_image_executor({"path": str(src), "operation": "convert", "output_path": str(tmp_path / "out.gif")})

    assert "not installed" in result
    assert "brew install" in result


def test_subprocess_failure_returns_stderr_snippet(tmp_path):
    src = tmp_path / "in.png"
    src.write_bytes(b"fake")
    err = subprocess.CalledProcessError(1, ["magick"], stderr="bad geometry")

    with patch("tools.image_edit._run", side_effect=err):
        result = edit_image_executor({"path": str(src), "operation": "resize", "width": 10, "output_path": str(tmp_path / "out.png")})

    assert "resize failed" in result
    assert "bad geometry" in result


def test_output_not_produced_is_reported(tmp_path):
    """_run succeeds (mocked) but never actually writes a file — must not
    silently report success on a path that doesn't exist."""
    src = tmp_path / "in.png"
    src.write_bytes(b"fake")
    out = tmp_path / "out.png"

    with patch("tools.image_edit._run"):
        result = edit_image_executor({"path": str(src), "operation": "resize", "width": 10, "output_path": str(out)})

    assert "output not produced" in result


# --- Real-binary integration tests (matches the requirements already
# verified installed on this machine: magick, rsvg-convert) -----------------

_HAS_MAGICK = subprocess.run(["which", "magick"], capture_output=True).returncode == 0
_HAS_RSVG = subprocess.run(["which", "rsvg-convert"], capture_output=True).returncode == 0

_SAMPLE_SVG = '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="80">' \
              '<rect width="100" height="80" fill="red"/></svg>'


@pytest.mark.skipif(not _HAS_MAGICK, reason="ImageMagick not installed")
def test_real_resize_produces_correctly_sized_png(tmp_path):
    src = tmp_path / "in.png"
    subprocess.run(["magick", "-size", "50x50", "xc:blue", str(src)], check=True)
    out = tmp_path / "out.png"

    result = edit_image_executor({"path": str(src), "operation": "resize", "width": 20, "height": 20, "output_path": str(out)})

    assert result == str(out)
    info = subprocess.run(["magick", "identify", "-format", "%wx%h", str(out)], capture_output=True, text=True)
    assert info.stdout.strip() == "20x20"


@pytest.mark.skipif(not _HAS_RSVG, reason="rsvg-convert not installed")
def test_real_svg_convert_to_png_produces_a_valid_png(tmp_path):
    src = tmp_path / "in.svg"
    src.write_text(_SAMPLE_SVG)
    out = tmp_path / "out.png"

    result = edit_image_executor({"path": str(src), "operation": "convert", "output_path": str(out)})

    assert result == str(out)
    assert out.exists() and out.stat().st_size > 0
    with open(out, "rb") as f:
        assert f.read(8) == b"\x89PNG\r\n\x1a\n"  # PNG magic bytes
