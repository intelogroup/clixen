import importlib
import sys


def _load_x_bird(monkeypatch, tmp_path):
    monkeypatch.setenv("TWSCRAPE_ACCOUNTS_DB", str(tmp_path / "accounts.db"))
    sys.modules.pop("tools.x_bird", None)
    return importlib.import_module("tools.x_bird")


def test_x_search_without_account_explains_secure_cookie_setup(monkeypatch, tmp_path):
    x_bird = _load_x_bird(monkeypatch, tmp_path)

    result = x_bird.x_search("clixen", count=1)

    assert result == (
        "[x error] no active twscrape X accounts configured. Run once: "
        'twscrape --db "$HOME/.clixen/twscrape_accounts.db" add_cookie <X_USERNAME> '
        "(the cookie prompt is hidden; paste auth_token and ct0 there, never in chat)"
    )


def test_x_tools_and_agent_are_reachable_from_public_registries():
    from tools.orchestrator_tools import ASK_X_AGENT_SCHEMA
    from tools.registry import ALL_TOOLS, EXECUTORS

    registered = {schema["function"]["name"] for schema in ALL_TOOLS}

    assert {"x_search", "x_read_tweet", "x_user_tweets", "ask_x_agent"} <= registered
    assert {"x_search", "x_read_tweet", "x_user_tweets", "ask_x_agent"} <= set(EXECUTORS)
    assert ASK_X_AGENT_SCHEMA["function"]["name"] == "ask_x_agent"
