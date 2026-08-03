import pytest
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.followup import _rewrite_standalone, _needs_context

def test_argentina_finished_match_rewrite():
    history = [
        {"role": "user", "content": "What will be the next match of Argentina in the World Cup"},
        {"role": "assistant", "content": "Argentina faces Cape Verde on July 3, 2026 at Hard Rock Stadium."},
    ]
    query = "This match is finished! I need next match"
    
    assert _needs_context(query, history)
    rewritten = _rewrite_standalone(query, history)
    print(f"\nScenario 1 Rewritten: {repr(rewritten)}")
    
    # Must resolve "this match" to Argentina and search for the next opponent or next match
    rewritten_lower = rewritten.lower()
    assert "argentina" in rewritten_lower
    assert "cape verde" not in rewritten_lower or "after" in rewritten_lower or "next" in rewritten_lower
    assert "world cup" in rewritten_lower or "match" in rewritten_lower

def test_score_followup_rewrite():
    history = [
        {"role": "user", "content": "Did Colombia play today?"},
        {"role": "assistant", "content": "Yes, Colombia played Ghana on July 3, 2026 in Kansas City."},
    ]
    query = "what was the score?"
    
    assert _needs_context(query, history)
    rewritten = _rewrite_standalone(query, history)
    print(f"\nScenario 2 Rewritten: {repr(rewritten)}")
    
    rewritten_lower = rewritten.lower()
    assert "colombia" in rewritten_lower
    assert "ghana" in rewritten_lower
    assert "score" in rewritten_lower or "result" in rewritten_lower

def test_goalscorer_followup_rewrite():
    history = [
        {"role": "user", "content": "Who won the Belgium vs Senegal game?"},
        {"role": "assistant", "content": "Senegal defeated Belgium 1-0 in their World Cup match."},
    ]
    query = "who scored the goal?"
    
    assert _needs_context(query, history)
    rewritten = _rewrite_standalone(query, history)
    print(f"\nScenario 3 Rewritten: {repr(rewritten)}")
    
    rewritten_lower = rewritten.lower()
    assert "senegal" in rewritten_lower
    assert "belgium" in rewritten_lower
    assert "goal" in rewritten_lower or "scored" in rewritten_lower or "scorer" in rewritten_lower

def test_f1_schedule_followup_rewrite():
    history = [
        {"role": "user", "content": "When is the next Formula 1 race?"},
        {"role": "assistant", "content": "The next Formula 1 race is the British Grand Prix on July 5, 2026."},
    ]
    query = "What about the one after that?"
    
    assert _needs_context(query, history)
    rewritten = _rewrite_standalone(query, history)
    print(f"\nScenario 4 Rewritten: {repr(rewritten)}")
    
    rewritten_lower = rewritten.lower()
    assert "formula 1" in rewritten_lower or "f1" in rewritten_lower
    assert "race" in rewritten_lower or "grand prix" in rewritten_lower
    assert "after" in rewritten_lower or "british" in rewritten_lower or "july 5" in rewritten_lower
