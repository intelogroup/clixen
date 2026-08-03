"""
Audio transcription tool — whisper-cpp subprocess → transcript → KB.

whisper-cpp (brew) handles the STT; this module wires it into the harness:
  1. Converts audio to 16kHz mono WAV via ffmpeg
  2. Runs whisper-cli with ggml-base.bin
  3. Stores transcript in KB as source='audio_transcript' (permanent TTL)
  4. Returns the transcript text for the model to reason over

Supported input formats: anything ffmpeg can decode (.mp3 .wav .m4a .ogg .flac .mp4)
"""
import os
import subprocess
import tempfile
from pathlib import Path

WHISPER_CLI  = "/opt/homebrew/bin/whisper-cli"
FFMPEG       = "/opt/homebrew/bin/ffmpeg"
WHISPER_MODEL = str(Path(__file__).resolve().parent.parent.parent / "models" / "ggml-base.bin")


SCHEMA = {
    "type": "function",
    "function": {
        "name": "transcribe_audio",
        "description": (
            "Transcribe an audio or video file to text using local Whisper. "
            "Use when the user provides an audio file path or asks to process speech. "
            "Returns the full transcript and stores it in the knowledge base."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Absolute path to the audio/video file to transcribe"
                },
                "language": {
                    "type": "string",
                    "description": "Language code e.g. 'en', 'fr'. Omit to auto-detect.",
                    "default": ""
                }
            },
            "required": ["file_path"]
        }
    }
}


def _to_wav(input_path: str, out_path: str) -> None:
    """Convert any audio/video file to 16kHz mono WAV for whisper-cpp."""
    # 2026-08-03: no timeout previously — a hung ffmpeg on corrupt/streaming
    # input blocked the transcript thread forever. 120s bound, same as whisper-cli.
    subprocess.run(
        [
            FFMPEG, "-y", "-i", input_path,
            "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le",
            out_path,
        ],
        check=True,
        capture_output=True,
        timeout=120,
    )


def _resolve_audio_path(path_str: str) -> Path:
    """Resolve an audio file path, handling ~ and LLM-expanded /home/user/ paths."""
    p = Path(path_str).expanduser()
    if not p.exists():
        home = os.path.expanduser("~")
        if not str(p).startswith(home) and path_str.startswith("/home/"):
            fallback = Path(home) / "/".join(Path(path_str).parts[3:])
            if fallback.exists():
                return fallback.resolve()
    return p.resolve()


def execute(file_path: str, language: str = "") -> str:
    """
    Transcribe audio file and store transcript in KB.
    Returns the transcript text.
    """
    resolved = _resolve_audio_path(file_path)
    if not resolved.exists():
        return f"File not found: {file_path}"

    with tempfile.TemporaryDirectory() as tmp:
        wav_path = os.path.join(tmp, "audio.wav")

        # Convert to 16kHz mono WAV
        try:
            _to_wav(str(resolved), wav_path)
        except subprocess.CalledProcessError as e:
            return f"ffmpeg conversion failed: {e.stderr.decode()[:200]}"

        # Run whisper-cli
        cmd = [WHISPER_CLI, "-m", WHISPER_MODEL, "-f", wav_path, "--output-txt", "--no-prints"]
        if language:
            cmd += ["-l", language]

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        except subprocess.TimeoutExpired:
            return "Transcription timed out (>120s)"

        # whisper-cli writes <filename>.txt next to the wav
        txt_path = wav_path + ".txt"
        if Path(txt_path).exists():
            transcript = Path(txt_path).read_text().strip()
        elif result.stdout.strip():
            transcript = result.stdout.strip()
        else:
            return f"Whisper produced no output. stderr: {result.stderr[:200]}"

    if not transcript:
        return "Transcription returned empty text."

    MAX_TRANSCRIPT_CHARS = 8000
    if len(transcript) > MAX_TRANSCRIPT_CHARS:
        orig_len = len(transcript)
        transcript = transcript[:MAX_TRANSCRIPT_CHARS] + (
            f"\n\n[Transcript truncated to {MAX_TRANSCRIPT_CHARS} characters. "
            f"Original length: {orig_len} chars]"
        )

    # Store in KB with permanent TTL — best-effort, never block the transcript
    try:
        from store.knowledge_base import KnowledgeBase
        kb = KnowledgeBase()
        kb.store(
            transcript,
            source="audio_transcript",
            query=resolved.name,
            url=str(resolved),
            auto_chunk=True,
        )
    except Exception as kb_err:
        import logging
        logging.getLogger("audio").warning(
            "KB store failed for %s (%s); returning transcript anyway",
            resolved.name, kb_err,
        )

    return transcript
