"""
Audio-chat pipeline extracted from harness.py — self-contained aside from
`local_chat` (harness's cloud/local dispatch wrapper), imported lazily inside
run_audio() to avoid a circular import with harness.py re-exporting this.
"""

from pathlib import Path

from store.conversation import (
    get as conv_get,
    append as conv_append,
    trim_to_budget,
    compact_old_turns,
    get_lock,
)
from tools.memory_tools import recall_block as memory_recall
from tools.registry import ALL_TOOLS
from harness_tts import _speak


def run_audio(
    audio_path: str,
    prompt: str = "",
    model: str = "gemma4:12b-mlx",
    tts: bool = False,
    tts_voice: str = "af_heart",
    chat_id: str | None = None,
) -> dict:
    """
    Transcribe an audio file and send the transcript to the local LLM.

    Pipeline:
        audio_path → whisper-cpp (STT) → transcript → LLM → response

    `chat_id`, when given, threads this turn through the same conversation
    history/memory continuity as text chat — two transcriptions in the same
    chat_id build on each other instead of each starting from zero context.

    Returns:
        {"transcript": str, "response": str}
    """
    from harness import local_chat  # lazy: harness.py re-exports run_audio from here
    from tools.audio import execute as transcribe

    transcript = transcribe(audio_path)
    if (
        transcript.startswith("File not found")
        or transcript.startswith("ffmpeg")
        or transcript.startswith("Whisper")
        or transcript.startswith("Transcription")
    ):
        return {"transcript": transcript, "response": ""}

    user_message = f"Transcript of audio file '{Path(audio_path).name}':\n\n{transcript}"
    if prompt:
        user_message += f"\n\n{prompt}"
    else:
        user_message += "\n\nPlease respond to or summarise the above transcript."

    history = []
    lock = None
    if chat_id is not None:
        lock = get_lock(chat_id)
        with lock:
            history = trim_to_budget(conv_get(chat_id), model, user_message)

    _mem_block = memory_recall(user_message)
    system_prompt = _mem_block if _mem_block else None

    response = local_chat(
        user_message=user_message,
        tools=ALL_TOOLS,
        model=model,
        history=history,
        system_prompt=system_prompt,
    )

    if chat_id is not None:
        with lock:
            conv_append(chat_id, "user", user_message)
            conv_append(chat_id, "assistant", response)
            compact_old_turns(chat_id, model)

    if tts:
        _speak(response, voice=tts_voice)

    return {"transcript": transcript, "response": response}
