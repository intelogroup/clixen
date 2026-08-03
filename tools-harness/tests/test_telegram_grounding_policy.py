"""
Telegram routing policy: the model's factual memory is not trusted, so almost everything
routes to web-grounded search. Only real greetings / creative asks / local tasks stay on
the model. Pure regex — deterministic, no network.
"""

import pytest

from clients.router import classify_telegram

_WEB = {"temporal", "factual_qa", "web_search"}
_LOCAL_CHAT = {"casual"}
_LOCAL_TASK = {"casual", "math", "code_quick", "code_medium", "code_heavy",
               "filesystem", "git", "email", "reminder", "calendar", "tasks"}


@pytest.mark.parametrize("q", [
    "who won France vs Sweden",
    "is the soccer match Mexico vs Ecuador playing now?",
    "what is the capital of Peru",
    "how does mRNA work",
    "is it raining in Boston",
    "who is Elon Musk",
    "explain quantum entanglement",
    "tell me about the eiffel tower",
    "what happened today",
    "some random trivia about the roman empire",  # flipped default → web
])
def test_factual_and_current_go_web(q):
    assert classify_telegram(q)[1] in _WEB, q


@pytest.mark.parametrize("q", [
    "hi", "hello there", "hey how are you", "thanks!", "good night", "ok cool",
    "you're funny", "who are you", "what can you do",
])
def test_greetings_stay_local_casual(q):
    assert classify_telegram(q)[1] in _LOCAL_CHAT, q


@pytest.mark.parametrize("q", [
    "write me a poem about the sea",
    "tell me a joke",
    "brainstorm startup names",
    "pretend you are a pirate",
    "compose a haiku about rain",
])
def test_creative_stays_local(q):
    assert classify_telegram(q)[1] in _LOCAL_CHAT, q


@pytest.mark.parametrize("q", [
    "what is 2+2", "what is 12 * 9", "15% of 300", "calculate 100 / 4",
])
def test_arithmetic_stays_local_math(q):
    assert classify_telegram(q)[1] == "math", q


@pytest.mark.parametrize("q", [
    "what's my name",
    "you said earlier we agreed on the plan",
])
def test_memory_refs_stay_local(q):
    # Conversation-memory refs must use history on the chat model, never web.
    assert classify_telegram(q)[1] == "casual", q
