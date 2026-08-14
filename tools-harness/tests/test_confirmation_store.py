import json

from tools import confirmation


def test_confirmation_is_durable_and_single_use(tmp_path, monkeypatch):
    db_path = tmp_path / "confirmations.db"
    monkeypatch.setattr(confirmation, "_DB_PATH", db_path)

    token = confirmation.request_confirmation(
        "create_pdf", {"output_path": str(tmp_path / "draft.pdf")}, "create_pdf(...)"
    )
    assert any(item["token"] == token for item in confirmation.list_pending())

    # Re-read through the durable API boundary, then ensure resolution is atomic.
    entry = confirmation.pop_pending(token)
    assert entry["tool_name"] == "create_pdf"
    assert entry["arguments"]["output_path"].endswith("draft.pdf")
    assert confirmation.pop_pending(token) is None
    assert confirmation.list_pending() == []
