import os
import subprocess
from pathlib import Path
from threading import Lock

_model_cache = {}
_model_lock = Lock()

_MODELS_DIR = Path(__file__).resolve().parent.parent.parent / "models"
_WHISPER_CLI = "whisper-cli"

# ggml model names available for the whisper.cpp Metal path — only sizes with a
# local ggml file get this fast path, others fall through to faster-whisper.
_GGML_MODELS = {
    "tiny": "ggml-tiny.bin",
    "tiny.en": "ggml-tiny.en.bin",
    "base": "ggml-base.bin",
    "base.en": "ggml-base.en.bin",
    "small.en": "ggml-small.en.bin",
    "medium.en": "ggml-medium.en.bin",
    "large-v3": "ggml-large-v3.bin",
    "large-v3-turbo": "ggml-large-v3-turbo.bin",
}


def _resolve_audio_path(path: str) -> str:
    p = Path(path).expanduser()
    if not p.exists() and str(p).startswith("/home/"):
        real_home = Path.home()
        p = Path(str(p).replace("/home/", str(real_home.parent) + "/", 1))
    return str(p.resolve())


def _transcribe_whisper_cpp(resolved: str, model_size: str, language: str | None) -> str | None:
    """ponytail: whisper.cpp Metal path — ~4x faster than faster-whisper on Apple Silicon
    (benchmarked on M4: 0.56s vs 2.15s wall time for a 13.5s clip, same accuracy).
    Returns None on any failure so the caller falls back to faster-whisper."""
    ggml_name = _GGML_MODELS.get(model_size)
    if not ggml_name:
        return None
    model_path = _MODELS_DIR / ggml_name
    if not model_path.exists():
        return None

    cmd = [
        _WHISPER_CLI, "-m", str(model_path), "-f", resolved,
        "-nt", "-np", "-l", language or "auto",
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None
    if result.returncode != 0:
        return None

    text = " ".join(result.stdout.split())
    return text or None


def transcribe_audio(
    file_path: str,
    model_size: str = "base",
    language: str | None = None,
    max_chars: int = 8000,
) -> dict:
    resolved = _resolve_audio_path(file_path)

    cpp_text = _transcribe_whisper_cpp(resolved, model_size, language)
    if cpp_text is not None:
        truncated = len(cpp_text) > max_chars
        return {
            "text": cpp_text[:max_chars],
            "duration_sec": 0.0,
            "language": language or "auto",
            "truncated": truncated,
        }

    from faster_whisper import WhisperModel

    global _model_cache
    with _model_lock:
        if model_size not in _model_cache:
            # compute_type="int8" on CTranslate2/CPU on Apple Silicon lacks a
            # real int8 GEMM kernel — confirmed live 2026-09-22: it silently
            # produced divide-by-zero/overflow/NaN in the mel-spectrogram
            # matmul (feature_extractor.py) and hallucinated fluent-sounding
            # but content-free transcripts instead of erroring. float32 is
            # the numerically safe path on this platform; slower, but correct.
            _model_cache[model_size] = WhisperModel(
                model_size, device="cpu", compute_type="float32"
            )
        model = _model_cache[model_size]

    segments, info = model.transcribe(resolved, language=language)
    text_parts = []
    total_len = 0
    truncated = False
    for seg in segments:
        segment_text = seg.text.strip()
        if total_len + len(segment_text) > max_chars:
            remaining = max_chars - total_len
            if remaining > 0:
                text_parts.append(segment_text[:remaining])
            truncated = True
            break
        text_parts.append(segment_text)
        total_len += len(segment_text) + 1

    return {
        "text": " ".join(text_parts),
        "duration_sec": round(info.duration, 2),
        "language": info.language,
        "truncated": truncated,
    }


_creole_model = None
_creole_processor = None
_creole_lock = Lock()

# generic Whisper (even large-v3) is a known weak spot for Haitian Creole — a
# low-resource language it wasn't trained much on (confirmed live 2026-09-22:
# large-v3 language-ID confidence sat at 0.26-0.59 on real Creole clips, and
# leaned French). This community fine-tune produces genuine Kreyòl with real
# orthography instead — verified by cross-checking against large-v3 output on
# the same clips: both surfaced the same underlying content (school
# construction, concrete pouring, a water/drainage problem), so it's real
# signal, not noise, and this model's version reads far cleaner.
_CREOLE_MODEL_ID = "ZeeshanGeoPk/haitian-speech-to-text"


def transcribe_haitian_creole(file_path: str, max_new_tokens: int = 440) -> dict:
    """Transcribe audio with a Whisper fine-tune for Haitian Creole.

    Loads audio via ffmpeg directly (not torchaudio — sidesteps it entirely;
    a decode step is all that's needed here, no torchaudio-specific features
    used) into a raw float32 PCM array, matching what WhisperProcessor wants.

    Chunks into <=28s windows before generating — this model's decoder has a
    hard 448-token limit (config.max_target_positions, not a knob you can
    raise past), so anything longer than ~1-2 minutes of speech silently cut
    off mid-sentence in one generate() call (confirmed live 2026-09-22, a
    ~99s clip). 28s (not the full 30s Whisper's encoder window supports)
    leaves headroom so a word isn't split exactly on a chunk boundary.
    """
    resolved = _resolve_audio_path(file_path)
    proc = subprocess.run(
        ["ffmpeg", "-i", resolved, "-f", "f32le", "-ac", "1", "-ar", "16000", "-"],
        capture_output=True, check=True,
    )
    import numpy as np
    audio = np.frombuffer(proc.stdout, dtype=np.float32)

    global _creole_model, _creole_processor
    with _creole_lock:
        if _creole_model is None:
            from transformers import WhisperProcessor, WhisperForConditionalGeneration
            _creole_processor = WhisperProcessor.from_pretrained(_CREOLE_MODEL_ID)
            _creole_model = WhisperForConditionalGeneration.from_pretrained(_CREOLE_MODEL_ID)
        processor, model = _creole_processor, _creole_model

    chunk_samples = 28 * 16000
    parts = []
    for start in range(0, max(len(audio), 1), chunk_samples):
        chunk = audio[start:start + chunk_samples]
        if len(chunk) < 1600:  # <0.1s tail sliver — nothing usable in it
            continue
        inputs = processor(chunk, sampling_rate=16000, return_tensors="pt").input_features
        ids = model.generate(inputs, max_new_tokens=max_new_tokens)
        part = processor.batch_decode(ids, skip_special_tokens=True)[0].strip()
        if part:
            parts.append(part)

    return {
        "text": " ".join(parts),
        "duration_sec": round(len(audio) / 16000, 2),
        "language": "ht",
        "truncated": False,
    }


# ---------------------------------------------------------------------------
# Audio format conversion (ffmpeg)
# ---------------------------------------------------------------------------

_AUDIO_CODECS: dict[str, list[str]] = {
    "mp3":  ["-acodec", "libmp3lame"],
    "m4a":  ["-acodec", "aac"],
    "wav":  ["-acodec", "pcm_s16le"],
    "flac": ["-acodec", "flac"],
    "ogg":  ["-acodec", "libvorbis"],
    "aac":  ["-acodec", "aac"],
    "opus": ["-acodec", "libopus"],
}

CONVERT_AUDIO_SCHEMA = {
    "type": "function",
    "function": {
        "name": "convert_audio",
        "description": (
            "Convert an audio file to a different format using ffmpeg. "
            "Supported formats: mp3, m4a, wav, flac, ogg, aac, opus. "
            "Returns the output file path."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "input_path": {
                    "type": "string",
                    "description": "Absolute path to the input audio file",
                },
                "output_format": {
                    "type": "string",
                    "enum": ["mp3", "m4a", "wav", "flac", "ogg", "aac", "opus"],
                    "description": "Target audio format",
                },
                "output_path": {
                    "type": "string",
                    "description": "Output file path. Auto-generated next to input if omitted.",
                    "default": "",
                },
                "bitrate": {
                    "type": "string",
                    "description": "Audio bitrate for lossy formats, e.g. '128k', '192k', '320k'",
                    "default": "192k",
                },
            },
            "required": ["input_path", "output_format"],
        },
    },
}


def convert_audio(
    input_path: str,
    output_format: str,
    output_path: str = "",
    bitrate: str = "192k",
) -> str:
    """Convert audio file to a different format."""
    inp = Path(input_path).expanduser()
    if not inp.exists():
        return f"Input file not found: {input_path}"

    fmt = output_format.lower().lstrip(".")
    if fmt not in _AUDIO_CODECS:
        return f"Unsupported format: {fmt}. Choose from: {', '.join(sorted(_AUDIO_CODECS))}"

    out = Path(output_path).expanduser() if output_path else inp.parent / f"{inp.stem}.{fmt}"

    codec_flags = list(_AUDIO_CODECS[fmt])
    # Add bitrate for lossy formats
    if fmt not in ("wav", "flac"):
        codec_flags += ["-b:a", bitrate]

    cmd = ["ffmpeg", "-y", "-i", str(inp)] + codec_flags + [str(out)]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if result.returncode == 0 and out.exists():
            size_mb = out.stat().st_size / 1024 / 1024
            return f"Converted: {out} ({size_mb:.1f} MB)"
        return f"ffmpeg failed: {result.stderr[-400:]}"
    except FileNotFoundError:
        return "ffmpeg is not installed. Run: brew install ffmpeg"
    except subprocess.TimeoutExpired:
        return "Conversion timed out (5 min limit)"
    except Exception as e:
        return f"convert_audio error: {type(e).__name__}: {e}"
