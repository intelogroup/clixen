import json


def test_transcript_archive_is_encrypted_and_compressed(tmp_path, monkeypatch):
    from cryptography.fernet import Fernet
    from store import transcript_archive

    key = Fernet.generate_key()
    monkeypatch.setattr(transcript_archive, "_ARCHIVE_DIR", tmp_path / "archive")
    monkeypatch.setenv("CLIXEN_ARCHIVE_KEY", key.decode("ascii"))
    target = transcript_archive.archive_raw_session("chat", [{"role": "user", "content": "private"}])

    raw = target.read_bytes()
    assert b"private" not in raw
    payload = json.loads(__import__("gzip").decompress(Fernet(key).decrypt(raw)))
    assert payload["history"][0]["content"] == "private"
