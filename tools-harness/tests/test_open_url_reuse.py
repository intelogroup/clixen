from tools.open_actions import norm_url, same_page


def test_norm_url_ignores_scheme_www_fragment_trailing_slash():
    assert norm_url("https://www.YouTube.com/") == norm_url("http://youtube.com#top") == "youtube.com"
    assert norm_url("https://en.wikipedia.org/wiki/Alan_Turing#Early_life") == "en.wikipedia.org/wiki/alan_turing"
    assert norm_url("https://x.com/a?q=1") != norm_url("https://x.com/a?q=2")  # the query is part of the page


def test_same_page_compares_the_front_tab_not_the_title():
    assert same_page("https://en.wikipedia.org/wiki/Alan_Turing#Early_life", "https://en.wikipedia.org/wiki/Alan_Turing")
    assert not same_page("https://en.wikipedia.org/wiki/Bletchley_Park", "https://en.wikipedia.org/wiki/Alan_Turing")
    assert not same_page("", "https://example.com")  # unreadable front page: open normally
