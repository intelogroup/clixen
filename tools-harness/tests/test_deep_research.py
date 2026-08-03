"""
Unit tests for the upgraded deep research pipeline in tools/deep_research.py.
"""
import json
from unittest.mock import patch, MagicMock
from tools.deep_research import (
    _decompose_query,
    _validate_citations,
    _get_gap_questions,
    _critique_and_revise,
    _evaluate_source_credibility,
    _run_theme_subagent,
    _synthesize_report,
    _evaluate_report_craap,
    execute,
)


@patch("tools.deep_research._call_llm")
def test_decompose_query(mock_call):
    mock_call.return_value = "query one\nquery two\nquery three"
    queries = _decompose_query("complex query", "mock-model")
    assert queries == ["query one", "query two", "query three"]


def test_validate_citations():
    report = (
        "# Report Title\n\n"
        "Some claim with citation [1]. Another claim with citation [2].\n"
        "A hallucinated claim with a missing citation like [4] is ignored.\n\n"
        "## References\n"
        "1. Active reference 1 (http://active1.com)\n"
        "2. Active reference 2 (http://active2.com)\n"
        "3. Hallucinated/unused reference 3 (http://unused.com)\n"
    )
    sources = [
        {"url": "http://active1.com", "title": "Active 1"},
        {"url": "http://active2.com", "title": "Active 2"},
        {"url": "http://unused.com", "title": "Unused 3"},
    ]

    cleaned = _validate_citations(report, sources)
    # Reference 3 is unused, so it should be stripped
    assert "Active reference 1" in cleaned
    assert "Active reference 2" in cleaned
    assert "unused.com" not in cleaned


@patch("tools.deep_research._call_llm")
def test_get_gap_questions(mock_call):
    mock_call.return_value = "gap query one\ngap query two"
    gaps = _get_gap_questions("main topic", "existing findings", "mock-model")
    assert gaps == ["gap query one", "gap query two"]


@patch("tools.deep_research._call_llm")
def test_critique_and_revise_passes_immediately(mock_call):
    # Mock critique response returning JSON showing a passing score of 8
    critique_json = {
        "score": 8,
        "critique": "Draft looks good",
        "revisions_needed": []
    }
    mock_call.return_value = json.dumps(critique_json)

    sources = [{"url": "http://example.com", "title": "Source", "content": "Text"}]
    result = _critique_and_revise("topic", "Draft content", sources, "mock-model")

    # Should return original draft immediately without calling rewrite/revision
    assert result == "Draft content"
    # LLM should have only been called once (for critique, not revision)
    assert mock_call.call_count == 1


@patch("tools.deep_research._call_llm")
def test_critique_and_revise_performs_revision(mock_call):
    # Mock critique response showing a failing score of 5 on round 1, then a pass on round 2
    critique_json_fail = {
        "score": 5,
        "critique": "Draft is too brief",
        "revisions_needed": ["Add more context"]
    }
    # Revised report needs to be > 200 characters to pass the revision length check
    revised_report = (
        "# Revised Report Title\n\n"
        "Added context here to make this report highly detailed and comprehensive. "
        "We want to ensure we hit the required length constraint for the revised draft "
        "so that the pipeline successfully accepts this revision as a valid update to "
        "the final report. This draft now contains sufficient background information."
    )
    critique_json_pass = {
        "score": 9,
        "critique": "Better",
        "revisions_needed": []
    }

    mock_call.side_effect = [
        json.dumps(critique_json_fail),  # Round 1 critique
        revised_report,                  # Round 1 revision
        json.dumps(critique_json_pass),  # Round 2 critique
    ]

    sources = [{"url": "http://example.com", "title": "Source", "content": "Text"}]
    result = _critique_and_revise("topic", "# Draft Title", sources, "mock-model")

    assert result == revised_report
    assert mock_call.call_count == 3


def test_evaluate_source_credibility():
    # Academic/Gov
    cred1 = _evaluate_source_credibility("https://ncbi.nlm.nih.gov/pmc/articles/123", "randomized trial study cohort")
    assert "High Credibility" in cred1["tier"]
    assert cred1["score"] == 10  # 9 + 1 (cues)

    # Press
    cred2 = _evaluate_source_credibility("https://nytimes.com/world/news", "Some press coverage")
    assert "Established Press" in cred2["tier"]
    assert cred2["score"] == 7

    # Blog
    cred3 = _evaluate_source_credibility("https://myblog.com/post", "sponsored opinion piece")
    assert "Standard Web Source" in cred3["tier"]
    assert cred3["score"] == 4  # 5 - 1 (opinion)


@patch("tools.deep_research._search")
@patch("tools.deep_research._fetch_url")
@patch("tools.deep_research._call_llm")
def test_run_theme_subagent(mock_call, mock_fetch, mock_search):
    # Mock search
    mock_item = MagicMock()
    mock_item.url = "https://pubmed.ncbi.nlm.nih.gov/1234"
    mock_item.title = "Academic Study"
    
    mock_search_result = MagicMock()
    mock_search_result.ok = True
    mock_search_result.items = [mock_item]
    mock_search.return_value = mock_search_result

    # Mock fetch (>50 chars)
    mock_fetch.return_value = "This is a peer-reviewed research paper detailing clinical trial outcomes for Vibrio cholerae."

    # Mock subagent LLM response
    mock_call.return_value = "Factual theme subagent findings."

    seen = set()
    res = _run_theme_subagent("main query", "subtheme query", seen, breadth=2, model="mock-model")

    assert res["theme"] == "subtheme query"
    assert len(res["sources"]) == 1
    assert res["sources"][0]["url"] == "https://pubmed.ncbi.nlm.nih.gov/1234"
    assert res["sources"][0]["credibility"]["score"] == 10  # gov + peer-reviewed/clinical trial
    assert res["findings"] == "Factual theme subagent findings."


@patch("tools.deep_research._call_llm")
def test_synthesize_report(mock_call):
    mock_call.return_value = "# Research Report: Title\n\n## Evidence Hierarchy & Source Credibility\n\nBibliography details."
    
    sources = [{
        "url": "https://ncbi.nlm.nih.gov/pmc/123",
        "title": "Study",
        "content": "Text",
        "credibility": {"tier": "Tier 1: High Credibility", "score": 9}
    }]
    findings = ["Theme 1 findings.", "Theme 2 findings."]
    
    report = _synthesize_report("query", sources, findings, "mock-model")
    assert "# Research Report: Title" in report
    assert mock_call.call_count == 1


@patch("tools.deep_research._call_llm")
def test_evaluate_report_craap(mock_call):
    eval_json = {
        "currency": {"score": 9, "reason": "Fresh facts."},
        "relevance": {"score": 9, "reason": "Accurate."},
        "authority": {"score": 9, "reason": "Clear."},
        "accuracy": {"score": 9, "reason": "Verified."},
        "purpose": {"score": 10, "reason": "Objective."}
    }
    mock_call.return_value = json.dumps(eval_json)
    res = _evaluate_report_craap("query", "# Draft Report", "mock-model")
    assert "## ⚖️ Research Quality & Evaluation Matrix" in res
    assert "| **Currency** | 9/10 | Fresh facts. |" in res


def test_research_state_cache():
    import tempfile
    from pathlib import Path
    import tools.deep_research as dr
    
    with tempfile.TemporaryDirectory() as tmpdir:
        # Patch Path inside tools.deep_research to resolve Path(__file__).parent.parent as Path(tmpdir)
        with patch("tools.deep_research.Path") as mock_path:
            # We mock the Path class return value when called with __file__ or no args
            mock_reports_dir = Path(tmpdir)
            
            mock_inst = MagicMock()
            mock_inst.parent.parent = mock_reports_dir
            mock_path.return_value = mock_inst
            
            # Save
            dr._save_research_state("test_chat", {"test": "data"})
            state_file = mock_reports_dir / "data" / "research_reports" / "state_test_chat.json"
            assert state_file.exists()
            assert json.loads(state_file.read_text(encoding="utf-8")) == {"test": "data"}
            
            # Load
            loaded = dr._load_research_state("test_chat")
            assert loaded == {"test": "data"}
            
            # Delete
            dr._delete_research_state("test_chat")
            assert not state_file.exists()


@patch("tools.deep_research._decompose_query")
@patch("tools.deep_research._save_research_state")
@patch("tools.deep_research._load_research_state")
def test_execute_checkpoint_fresh(mock_load, mock_save, mock_decomp):
    mock_load.return_value = None
    mock_decomp.return_value = ["theme 1", "theme 2"]
    
    # Running fresh should save proposed plan and return approval prompt
    res = execute("Topic Query", depth=2, breadth=3, chat_id="test_chat")
    assert "### 📋 Proposed Research Plan" in res
    assert "- theme 1" in res
    mock_save.assert_called_once()
