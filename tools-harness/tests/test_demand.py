import numpy as np

from demand.backtest import gate, mase, rolling_mase
from demand.forecast import naive, seasonal_naive, slope


def test_naive_mase_is_one_on_random_walk_scale():
    y = np.arange(30, dtype=float)
    assert mase(y[-4:], naive(y[:-4], 4), y[:-4]) > 0


def test_seasonal_naive_beats_naive_on_seasonal_series():
    t = np.arange(52 * 3)
    y = 100 + 20 * np.sin(2 * np.pi * t / 52)
    assert rolling_mase(y, seasonal_naive) < rolling_mase(y, naive)


def test_slope_beats_naive_on_trend():
    y = 10 + 2.0 * np.arange(60)
    assert rolling_mase(y, slope) < rolling_mase(y, naive)


def test_slope_never_negative():
    assert (slope(np.array([10.0, 5.0, 1.0, 0.0, 0.0]), 8) >= 0).all()


def test_tag_keeps_only_software_pain_and_rank_counts():
    from demand.pain import rank, tag
    items = [{"url": f"u{i}", "text": f"I hate manual reminders {i}"} for i in range(4)]
    reply = ('noise [{"i":0,"software_pain":true,"pain":"manual invoice reminders","evidence":"hate manual reminders","incumbent":"QuickBooks","payer":"freelancer"},'
             '{"i":1,"software_pain":false,"pain":"client wont pay","incumbent":null,"payer":"unknown"},'
             '{"i":2,"software_pain":true,"pain":"manual invoice reminders","evidence":"hate manual reminders","incumbent":null,"payer":"freelancer"},'
             '{"i":3,"software_pain":true,"pain":"made up","evidence":"quote not in post","incumbent":null,"payer":"unknown"}]')
    tagged = tag(items, "invoicing", llm=lambda _p: reply)
    assert [t["url"] for t in tagged] == ["u0", "u2"]
    assert rank(tagged)[0] == {"pain": "manual invoice reminders", "count": 2,
                               "incumbents": ["QuickBooks"], "urls": ["u0", "u2"]}


def test_tag_survives_garbage_llm_reply():
    from demand.pain import tag
    assert tag([{"url": "u", "text": "t"}], "invoicing", llm=lambda _p: "sorry, no") == []


def test_gate_rejects_model_equal_to_baseline():
    s = {"a": 10 + 2.0 * np.arange(60)}
    assert not gate(s, naive, naive)["passed"]
    assert gate(s, slope, naive)["passed"]
