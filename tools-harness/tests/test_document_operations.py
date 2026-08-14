def test_compare_documents_reports_changed_lines():
    from tools.document_operations import compare_documents

    result = compare_documents("a.txt", "amount: 10\nstatus: open", "b.txt", "amount: 12\nstatus: open")

    assert "amount: 10" in result
    assert "amount: 12" in result


def test_extract_fields_returns_requested_labels():
    from tools.document_operations import extract_fields

    result = extract_fields("Invoice 1042\nTotal: $1,250.00\nDue date: 2026-09-01", ["invoice", "total", "due date"])

    assert result["invoice"] == "1042"
    assert result["total"] == "$1,250.00"
    assert result["due date"] == "2026-09-01"


def test_detect_inconsistencies_finds_conflicting_values():
    from tools.document_operations import detect_inconsistencies

    result = detect_inconsistencies({"a.txt": "notice period: 30 days", "b.txt": "notice period: 60 days"})

    assert result[0]["key"] == "notice period"
    assert {item["value"] for item in result[0]["sources"]} == {"30 days", "60 days"}


def test_classify_document_returns_semantic_type_and_format():
    from tools.document_operations import classify_document

    assert classify_document("invoice.pdf", "Invoice\nAmount due: $40") == {
        "type": "invoice", "format": "pdf"
    }


def test_batch_inspect_documents_is_read_only_and_detects_duplicates(tmp_path):
    from tools.document_operations import batch_inspect_documents
    import json

    first = tmp_path / "one.txt"
    second = tmp_path / "copy.txt"
    first.write_text("same contents")
    second.write_text("same contents")
    result = json.loads(batch_inspect_documents(str(tmp_path)))

    assert len(result["documents"]) == 2
    assert len(result["duplicates"]) == 1
    assert first.exists() and second.exists()
