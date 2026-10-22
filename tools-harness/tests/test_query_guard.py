

def test_screen_commands_skip_the_sports_clarifier():
    from tools.query_guard import check_all

    assert check_all("now go on youtube type : Barcelona Vs Psg soccer", has_history=False) is None
    assert check_all("click the search box, type: Barcelona Vs Psg soccer, press enter", has_history=False) is None
    assert check_all("Barcelona Vs Psg", has_history=False)  # a plain info query is still clarified
