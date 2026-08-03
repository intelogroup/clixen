"""Outbound SIP phone call via pjsua2 + TextNow/Spiff + Kokoro TTS."""

import json
import logging
import os
import subprocess
import tempfile
import time
import urllib.request

_log = logging.getLogger(__name__)

SCHEMA = {
    "type": "function",
    "function": {
        "name": "make_phone_call",
        "description": "Call a phone number and play a spoken message. Uses TextNow SIP credentials and Kokoro TTS.",
        "parameters": {
            "type": "object",
            "properties": {
                "number": {"type": "string", "description": "Destination phone number in international format, e.g. +17875551212"},
                "message": {"type": "string", "description": "Text to speak when the call is answered"},
            },
            "required": ["number", "message"],
        },
    },
}


def _cred(key: str) -> str:
    v = os.environ.get(f"TEXTNOW_{key}") or os.environ.get(f"SPIFF_{key}")
    if not v:
        raise ValueError(f"phone_call: missing TEXTNOW_{key} env var")
    return v


def _tts(text: str) -> str:
    """Generate audio via Kokoro daemon, return path to WAV file."""
    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    tmp.close()
    payload = json.dumps({"text": text, "voice": "af_heart"}).encode()
    req = urllib.request.Request(
        "http://127.0.0.1:9237/synthesize", data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        resp = urllib.request.urlopen(req, timeout=60)
        with open(tmp.name, "wb") as f:
            f.write(resp.read())
    except Exception as exc:
        _log.warning("phone_call: kokoro tts failed (%s), falling back to piper", exc)
        subprocess.run(
            ["piper", "--output-file", tmp.name],
            input=text.encode(), timeout=30,
        )
    return tmp.name


def exec_make_phone_call(args: dict) -> str:
    number = args["number"]
    message = args["message"]

    import pjsua2 as pj

    server = _cred("SIP_SERVER")
    user = _cred("SIP_USER")
    password = _cred("SIP_PASS")
    audio_path = _tts(message)
    _log.info("phone_call: audio ready at %s", audio_path)

    ep = pj.Endpoint()
    ep_cfg = pj.EpConfig()
    ep.libCreate()
    ep.libInit(ep_cfg)
    ep.audDevManager().setNullDev()
    ep.libStart()

    acc_cfg = pj.AccountConfig()
    acc_cfg.idUri = f"sip:{user}@{server}"
    acc_cfg.regConfig.registrarUri = f"sip:{server}"
    cred = pj.AuthCredInfo("digest", "*", user, 0, password)
    acc_cfg.sipConfig.authCreds.push_back(cred)
    acc = ep.createAccount(acc_cfg)
    time.sleep(1)

    call_cfg = pj.CallConfig()
    call = pj.Call(acc, -1)
    call.makeCall(f"sip:{number}@{server}", call_cfg)
    time.sleep(2)

    player = pj.AudioMediaPlayer()
    player.createPlayer(audio_path)
    call_media = call.getAudioMedia()
    player.startTransmit(call_media)

    time.sleep(len(message) * 0.08 + 1)
    call.hangUp(pj.CallOpParam())
    ep.libDestroy()
    os.unlink(audio_path)

    return f"[phone call to {number} completed]"
