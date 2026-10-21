from jobs.handlers import demand_pain_miner


def test_missing_config_fails_without_running(monkeypatch):
    monkeypatch.setattr("demand.pain.run", lambda *a, **k: (_ for _ in ()).throw(AssertionError("ran")))
    out = demand_pain_miner.handle({"config": {"niche": "x"}})
    assert out["success"] is False and "queries" in out["error"]


def test_success_counts_items(monkeypatch):
    monkeypatch.setattr("demand.pain.run", lambda niche, queries, keywords=None: [{"count": 3}, {"count": 2}])
    assert demand_pain_miner.handle({"config": {"niche": "n", "queries": ["q"]}}) == {"success": True, "items_processed": 5}


def test_run_error_is_reported_not_raised(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("down")
    monkeypatch.setattr("demand.pain.run", boom)
    out = demand_pain_miner.handle({"config": {"niche": "n", "queries": ["q"]}})
    assert out == {"success": False, "items_processed": 0, "error": "down"}
