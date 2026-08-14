from agents.document_evidence import chunk_evidence, attach_citation_map, evaluate_grounding, rank_evidence


def test_chunk_evidence_preserves_source_and_structural_locators():
    chunks = chunk_evidence(
        "/tmp/report.pdf",
        "# Executive Summary\nRevenue grew.\n\n## Page 2\nRisk remains.\n",
        max_chars=1000,
    )

    assert [c["citation"] for c in chunks] == ["E1", "E2"]
    assert chunks[0]["source"] == "/tmp/report.pdf"
    assert chunks[0]["locator"] == "Executive Summary"
    assert chunks[1]["locator"] == "page 2"
    assert "Revenue grew." in chunks[0]["text"]


def test_chunk_evidence_preserves_pdf_page_markers():
    chunks = chunk_evidence("report.pdf", "--- Page 7 ---\nA finding.\n--- Page 8 ---\nAnother finding.")

    assert [chunk["locator"] for chunk in chunks] == ["page 7", "page 8"]


def test_chunk_evidence_splits_large_sections_without_losing_order():
    chunks = chunk_evidence("notes.txt", "alpha " * 20 + "\n\n" + "beta " * 20, max_chars=40)

    assert len(chunks) > 1
    assert "alpha" in chunks[0]["text"]
    assert "beta" in chunks[-1]["text"]
    assert [c["citation"] for c in chunks] == [f"E{i}" for i in range(1, len(chunks) + 1)]


def test_attach_citation_map_keeps_only_known_ids_and_flags_unknown_ids():
    chunks = chunk_evidence("report.pdf", "# Summary\nImportant finding.")

    result = attach_citation_map("Finding [E1]. Unsupported claim [E9].", chunks)

    assert "[E1] report.pdf — Summary" in result
    assert "[E9]" in result
    assert "unknown evidence id" in result.lower()


def test_attach_citation_map_flags_invented_numeric_claim():
    chunks = [{"citation": "E1", "source": "facts.txt", "locator": "document", "text": "Term: 30 days"}]

    result = attach_citation_map("The term is 90 days [E1].", chunks)

    assert "GROUNDING WARNINGS" in result
    assert "90" in result


def test_attach_citation_map_does_not_flag_supported_numeric_claim():
    chunks = [{"citation": "E1", "source": "facts.txt", "locator": "document", "text": "Term: 30 days"}]

    result = attach_citation_map("The term is 30 days [E1].", chunks)

    assert "GROUNDING WARNINGS" not in result


def test_attach_citation_map_does_not_flag_cited_page_locator():
    chunks = [{"citation": "E3", "source": "slides.zip", "locator": "page 3", "text": "Gastroschisis"}]

    result = attach_citation_map("Gastroschisis is shown on page 3 [E3].", chunks)

    assert "GROUNDING WARNINGS" not in result


def test_rank_evidence_prefers_query_matches_and_keeps_source_diversity():
    chunks = [
        {"citation": "E1", "source": "a.txt", "locator": "document", "text": "apples and pears"},
        {"citation": "E2", "source": "b.txt", "locator": "document", "text": "termination notice is 30 days"},
        {"citation": "E3", "source": "c.txt", "locator": "document", "text": "termination notice is 60 days"},
    ]

    ranked = rank_evidence("What is the termination notice?", chunks, limit=2)

    assert [chunk["citation"] for chunk in ranked] == ["E2", "E3"]


def test_evaluate_grounding_reports_unknown_and_unsupported_claims():
    chunks = [{"citation": "E1", "source": "report.txt", "locator": "document", "text": "Term: 30 days."}]

    metrics = evaluate_grounding("The term is 90 days [E1] and [E9].", chunks)

    assert metrics["citation_count"] == 2
    assert metrics["known_citation_rate"] == 0.5
    assert metrics["unknown_ids"] == ["E9"]
    assert metrics["warnings"]
    assert metrics["grounded"] is False
