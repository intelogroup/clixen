from evals.screen_actions import CASES, State, all_of, front_is, page_moved, reply_matches, title_has, used


def test_cases_are_well_formed():
    ids = [c.id for c in CASES]
    assert len(ids) == len(set(ids)) and len(ids) >= 12
    assert all(c.prompt and callable(c.setup) and callable(c.check) and c.max_s > 0 for c in CASES)


def _st(**kw):
    base = dict(title="Alan Turing - Wikipedia - Google Chrome", front="Google Chrome", reply="ok", tools=["scroll_page"], seconds=1.0, moved=True)
    return State(**{**base, **kw})


def test_checks_read_real_state_not_the_reply():
    claim = _st(reply="Clicked Bletchley Park.", title="Alan Turing - Wikipedia")
    assert title_has("bletchley")(claim)  # reply says clicked, title says no: fail
    assert title_has("turing")(claim) is None
    assert used("scroll_page")(claim) is None and used("click_text")(claim)
    assert page_moved(_st(moved=False)) and page_moved(_st()) is None
    assert front_is("Google Chrome")(_st(front="Calculator"))
    assert reply_matches(r"couldn.?t")(_st(reply="I couldn't find it")) is None
    assert all_of(title_has("zzz"), used("scroll_page"))(_st())  # first failure wins
