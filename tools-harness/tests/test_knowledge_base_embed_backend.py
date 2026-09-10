"""A store must stay in one embedding space.

nomic-embed and text-embedding-3-small are both pinned to 768 dims (EMBED_DIM), so
mixing them raises nothing and silently returns confident nonsense. Measured on the
live memory store 2026-09-10: a fact searched by its own exact text came back at
distance 1.49 behind four unrelated rows, and recall_block() had been returning ""
for every query for as long as Ollama had been down.

No network: both backends are faked, one vector space per backend.
"""
import pytest

from store import knowledge_base as kb


def _fake_backends(monkeypatch, available=("openai", "ollama")):
    """Give each backend its own distinguishable vector space."""
    space = {"openai": 1.0, "ollama": -1.0}

    def fake_openai(text: str) -> list[float]:
        if "openai" not in available:
            raise kb.EmbedBackendUnavailable("openai down")
        kb.EMBED_BACKEND = "openai"
        return [space["openai"]] * kb.EMBED_DIM

    def fake_ollama(text: str) -> list[float]:
        if "ollama" not in available:
            raise RuntimeError("connection refused")
        kb.EMBED_BACKEND = "ollama"
        return [space["ollama"]] * kb.EMBED_DIM

    monkeypatch.setattr(kb, "_embed_openai", fake_openai)
    monkeypatch.setattr(kb, "_embed_ollama", fake_ollama)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")


def test_first_write_stamps_the_store(tmp_path, monkeypatch):
    _fake_backends(monkeypatch)
    store = kb.KnowledgeBase(db_path=str(tmp_path / "k.lance"))
    assert store.embed_backend is None  # empty store hasn't committed to a space

    store.store("hello", source="manual")
    assert store.embed_backend == "openai"
    assert kb.KnowledgeBase(db_path=str(tmp_path / "k.lance")).embed_backend == "openai"


def test_write_is_refused_rather_than_mixed(tmp_path, monkeypatch):
    _fake_backends(monkeypatch)
    path = str(tmp_path / "k.lance")
    store = kb.KnowledgeBase(db_path=path)
    store.store("first row", source="manual")

    # OpenAI goes away; Ollama is up. Writing anyway would put a second vector space
    # into this table, and that row would be unfindable for good.
    _fake_backends(monkeypatch, available=("ollama",))
    store = kb.KnowledgeBase(db_path=path)
    with pytest.raises(kb.EmbedBackendUnavailable):
        store.store("second row", source="manual")
    assert store.table.count_rows() == 1


def test_search_is_empty_rather_than_cross_space(tmp_path, monkeypatch):
    _fake_backends(monkeypatch)
    path = str(tmp_path / "k.lance")
    kb.KnowledgeBase(db_path=path).store("the user's dog is named Biscuit", source="manual")

    _fake_backends(monkeypatch, available=("ollama",))
    assert kb.KnowledgeBase(db_path=path).search("dog") == []


def test_probe_reads_the_majority_space_of_an_unstamped_store(tmp_path, monkeypatch):
    """Stores written before the stamp existed have to be measured, not assumed — the
    live store looked like an Ollama store by history and was two thirds OpenAI."""
    _fake_backends(monkeypatch)
    path = str(tmp_path / "k.lance")
    store = kb.KnowledgeBase(db_path=path)
    for i in range(6):
        store.store(f"row {i}", source="manual")
    store._stamp_path.unlink()

    assert kb.KnowledgeBase(db_path=path).embed_backend == "openai"


def test_unstamped_store_stays_undecided_when_no_backend_answers(tmp_path, monkeypatch):
    """Guessing here strands whichever half of a user's memory guessed wrong."""
    _fake_backends(monkeypatch)
    path = str(tmp_path / "k.lance")
    store = kb.KnowledgeBase(db_path=path)
    store.store("row", source="manual")
    store._stamp_path.unlink()

    _fake_backends(monkeypatch, available=())
    assert kb.KnowledgeBase(db_path=path).embed_backend is None
