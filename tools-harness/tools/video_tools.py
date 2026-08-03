"""
Video tools — yt-dlp download, ffmpeg processing, transcript extraction.

Best OSS: yt-dlp (download), ffmpeg (process/trim/convert), whisper (transcribe).
"""

import json
import logging
import subprocess
from pathlib import Path

_log = logging.getLogger("video_tools")

# ---------------------------------------------------------------------------
# yt-dlp: download video
# ---------------------------------------------------------------------------

YTDLP_SCHEMA = {
    "type": "function",
    "function": {
        "name": "download_video",
        "description": (
            "Download a video from YouTube or any supported URL using yt-dlp. "
            "Returns the local file path of the downloaded video. "
            "Supports YouTube, Vimeo, Twitter, TikTok, and 1000+ other sites."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "URL of the video to download",
                },
                "output_dir": {
                    "type": "string",
                    "description": "Directory to save the video (default: ~/Downloads)",
                    "default": "",
                },
                "format": {
                    "type": "string",
                    "description": "Format code or 'best' (default), 'bestvideo+bestaudio', or specific resolution like '720p'",
                    "default": "best",
                },
                "audio_only": {
                    "type": "boolean",
                    "description": "Download audio only (extract audio, save as m4a)",
                    "default": False,
                },
            },
            "required": ["url"],
        },
    },
}


def ytdlp_execute(url: str, output_dir: str = "", format: str = "best",
                  audio_only: bool = False) -> str:
    """Download video with yt-dlp."""
    out_dir = Path(output_dir or Path.home() / "Downloads")
    out_dir.mkdir(parents=True, exist_ok=True)

    cmd = ["yt-dlp", "--no-playlist", "--no-progress", "--print", "filename"]

    if audio_only:
        cmd += ["-f", "bestaudio", "--extract-audio", "--audio-format", "m4a"]
    else:
        cmd += ["-f", format]

    cmd += ["-o", str(out_dir / "%(title)s.%(ext)s"), url]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if result.returncode != 0:
            return f"yt-dlp failed: {result.stderr[:500]}"

        filename = result.stdout.strip()
        if filename and Path(filename).exists():
            return f"Downloaded: {filename}"
        # Search output dir for new files
        recent = sorted(out_dir.glob("*"), key=lambda p: p.stat().st_mtime, reverse=True)[:1]
        if recent:
            return f"Downloaded: {recent[0]}"
        return f"yt-dlp completed but file not found. Output: {result.stdout[:500]}"
    except FileNotFoundError:
        return "yt-dlp is not installed. Run: pip install yt-dlp"
    except subprocess.TimeoutExpired:
        return "Download timed out (10 min limit)"
    except Exception as e:
        return f"yt-dlp error: {type(e).__name__}: {e}"


# ---------------------------------------------------------------------------
# ffmpeg: process video
# ---------------------------------------------------------------------------

FFMPEG_SCHEMA = {
    "type": "function",
    "function": {
        "name": "process_video",
        "description": (
            "Process a video with ffmpeg — trim, convert format, resize, "
            "extract audio, merge clips, apply filters. "
            "Returns the output file path."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "input_path": {
                    "type": "string",
                    "description": "Absolute path to input video file",
                },
                "operation": {
                    "type": "string",
                    "enum": ["trim", "convert", "resize", "extract_audio", "info", "concat"],
                    "description": "Operation to perform",
                },
                "output_path": {
                    "type": "string",
                    "description": "Output file path (auto-generated if not provided)",
                    "default": "",
                },
                "start": {
                    "type": "string",
                    "description": "Start time for trim (e.g. '00:00:30')",
                    "default": "",
                },
                "end": {
                    "type": "string",
                    "description": "End time for trim (e.g. '00:01:30')",
                    "default": "",
                },
                "width": {
                    "type": "integer",
                    "description": "Target width for resize",
                    "default": 0,
                },
                "height": {
                    "type": "integer",
                    "description": "Target height for resize",
                    "default": 0,
                },
                "format": {
                    "type": "string",
                    "description": "Output format for convert (e.g. 'mp4', 'gif', 'webm')",
                    "default": "mp4",
                },
            },
            "required": ["input_path", "operation"],
        },
    },
}


def ffmpeg_execute(
    input_path: str,
    operation: str,
    output_path: str = "",
    start: str = "",
    end: str = "",
    width: int = 0,
    height: int = 0,
    format: str = "mp4",
) -> str:
    """Process video with ffmpeg."""
    inp = Path(input_path)
    if not inp.exists():
        return f"Input file not found: {input_path}"

    if output_path:
        out = Path(output_path)
    else:
        suffix_map = {"trim": inp.suffix, "convert": f".{format}", "resize": inp.suffix,
                      "extract_audio": ".m4a", "info": ".txt", "concat": inp.suffix}
        suffix = suffix_map.get(operation, inp.suffix)
        out = inp.parent / f"{inp.stem}_{operation}{suffix}"

    try:
        if operation == "info":
            cmd = ["ffprobe", "-v", "quiet", "-print_format", "json",
                   "-show_format", "-show_streams", str(inp)]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if result.returncode == 0 and result.stdout:
                info = json.loads(result.stdout)
                fmt = info.get("format", {})
                streams = info.get("streams", [])
                lines = [
                    f"File: {fmt.get('filename', input_path)}",
                    f"Duration: {fmt.get('duration', '?')}s",
                    f"Size: {int(fmt.get('size', 0)) / 1024 / 1024:.1f} MB",
                ]
                for s in streams:
                    codec = s.get("codec_type", "?")
                    if codec == "video":
                        lines.append(f"Video: {s.get('width')}x{s.get('height')} {s.get('codec_name')} {s.get('r_frame_rate', '').split('/')[0] if '/' in s.get('r_frame_rate', '') else s.get('r_frame_rate', '')}fps")
                    elif codec == "audio":
                        lines.append(f"Audio: {s.get('codec_name')} {s.get('sample_rate')}Hz {s.get('channels')}ch")
                return "\n".join(lines)
            return f"ffprobe failed: {result.stderr[:300]}"

        elif operation == "trim":
            cmd = ["ffmpeg", "-y", "-i", str(inp)]
            if start:
                cmd += ["-ss", start]
            if end:
                cmd += ["-to", end]
            cmd += ["-c", "copy", str(out)]

        elif operation == "convert":
            if format == "gif":
                cmd = ["ffmpeg", "-y", "-i", str(inp), "-vf", "fps=10,scale=480:-1", str(out)]
            else:
                cmd = ["ffmpeg", "-y", "-i", str(inp), "-c:v", "libx264", "-preset", "fast",
                       "-c:a", "aac", str(out)]

        elif operation == "resize":
            w = width or 1280
            h = height or 720
            cmd = ["ffmpeg", "-y", "-i", str(inp), "-vf", f"scale={w}:{h}", str(out)]

        elif operation == "extract_audio":
            cmd = ["ffmpeg", "-y", "-i", str(inp), "-vn", "-acodec", "aac", "-b:a", "192k", str(out)]

        elif operation == "concat":
            return "concat requires a file list. Use trim+merge manually with multiple process_video calls."

        else:
            return f"Unknown operation: {operation}"

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if result.returncode == 0 and out.exists():
            size_mb = out.stat().st_size / 1024 / 1024
            return f"Processed: {out} ({size_mb:.1f} MB)"
        return f"ffmpeg failed: {result.stderr[:500]}"

    except FileNotFoundError:
        return "ffmpeg is not installed. Run: brew install ffmpeg"
    except subprocess.TimeoutExpired:
        return "Processing timed out (5 min limit)"
    except Exception as e:
        return f"ffmpeg error: {type(e).__name__}: {e}"
