"""Smoke test: user.automation telegram-query dedupe + pubmed days->reldate wiring."""
from unittest.mock import patch

from jobs.handlers import user_automation


def test_telegram_query_dedupe_only_sends_new_and_persists_seen_ids():
    listing = (
        "ID: msg1\n  From: cccs@example.com\n  Subject: Booking confirmed\n  Date: today\n  Preview: x\n\n"
        "ID: msg2\n  From: cccs@example.com\n  Subject: Reminder\n  Date: today\n  Preview: y"
    )
    instance = {
        "id": "wf1",
        "task_name": "CCCS Booking Watch",
        "action_type": "telegram",
        "config": {
            "query": "CCCS booking",
            "message": "New: {sender} / {subject}",
            "seen_ids": ["msg1"],  # msg1 already seen -> only msg2 should send
        },
    }
    sent = []
    saved_config = {}

    with patch("tools.gmail.list_emails", return_value=listing), \
         patch("tools.telegram_send.send_telegram", side_effect=lambda m: sent.append(m) or "ok"), \
         patch("store.workflow_store.update_workflow_instance",
               side_effect=lambda wid, config: saved_config.update(config)):
        result = user_automation.handle(instance)

    assert result["success"] is True
    assert result["new_items"] == 1
    assert len(sent) == 1
    assert "msg2" not in instance["config"]["seen_ids"]  # original untouched
    assert set(saved_config["seen_ids"]) == {"msg1", "msg2"}


def test_telegram_query_no_new_items_sends_nothing():
    listing = "ID: msg1\n  From: cccs@example.com\n  Subject: Booking confirmed\n  Date: today\n  Preview: x"
    instance = {
        "id": "wf1",
        "task_name": "CCCS Booking Watch",
        "action_type": "telegram",
        "config": {"query": "CCCS booking", "seen_ids": ["msg1"]},
    }
    with patch("tools.gmail.list_emails", return_value=listing), \
         patch("tools.telegram_send.send_telegram") as mock_send:
        result = user_automation.handle(instance)

    mock_send.assert_not_called()
    assert result["new_items"] == 0


def test_pubmed_days_config_maps_to_reldate_and_date_sort():
    instance = {
        "id": "wf2",
        "task_name": "Anti-Aging PubMed Watch",
        "action_type": "email",
        "config": {
            "query": "senescence",
            "recipient": "x@example.com",
            "days": 1,
            "seen_pmids": [],
        },
    }
    captured = {}

    def fake_pubmed_search(query, max_results=10, reldate=None, sort="relevance"):
        captured.update(query=query, max_results=max_results, reldate=reldate, sort=sort)
        from tools.search_result import SearchResult
        return SearchResult(content="", ok=False, error="no results", source="pubmed", query=query)

    with patch("tools.pubmed.execute", side_effect=fake_pubmed_search):
        user_automation.handle(instance)

    assert captured["reldate"] == 1
    assert captured["sort"] == "pub_date"


if __name__ == "__main__":
    test_telegram_query_dedupe_only_sends_new_and_persists_seen_ids()
    test_telegram_query_no_new_items_sends_nothing()
    test_pubmed_days_config_maps_to_reldate_and_date_sort()
    print("all passed")
