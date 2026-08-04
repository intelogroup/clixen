"""Skill definitions — split out of skills_hub.py by category. See skills_hub.py for Skill/_s/SKILLS."""

from __future__ import annotations

from skills_hub import SKILLS, _s

# ── Documents: quality gates before delivery ─────────────────────────────────

SKILLS.append(_s(
    "Check Document Quality",
    "Validate a generated Word, Excel, or PDF file before it's sent: check for formula errors, "
    "forbidden functions, structural corruption, and blank or low-content pages. "
    "Covers docx, xlsx, and pdf files (word documents, excel workbooks and sheets).",
    "Docs",
    ["validate_docx", "check_xlsx_quality", "check_pdf_anomalies", "read_file", "list_directory"],
    "You are a document QA gate. Run the check matching the file extension — never skip it:\n\n"
    "STEP 1: Identify the extension:\n"
    "- .docx -> call validate_docx(path=...)\n"
    "- .xlsx -> call check_xlsx_quality(path=...)\n"
    "- .pdf  -> call check_pdf_anomalies(path=...)\n"
    "For unknown extensions, say you can only QA docx/xlsx/pdf.\n\n"
    "STEP 2: Read the JSON report. If ok/status says clean, confirm and stop.\n\n"
    "STEP 3: If issues were found, report them concretely (cell coordinates, page numbers, "
    "part names). For a .docx with a sectPr-ordering issue, tell the user to run Repair DOCX. "
    "For .xlsx formula errors, name the sheet cells to fix. Never claim a file is fine when "
    "the report lists errors.",
    ["check document quality", "quality check", "is the doc valid",
     "verify docx", "verify xlsx", "verify pdf", "doc valid",
     "formula errors", "docx won't open", "corrupt file", "file valid",
     "check excel file", "check file"],
    max_rounds=5, icon="file-check",
    trigger_regex=(
        r"(?=.*\b(check|validate|verify|quality|valid|formula|error|corrupt)\b)"
        r"(?=.*\b(docx|word|pdf|excel|xlsx|sheet|workbook|file|document)\b)"
    ),
))


SKILLS.append(_s(
    "Repair DOCX",
    "Fix a corrupted or AI-generated Word document file (.docx) that Word refuses to open "
    "('unreadable content') by repairing OOXML child-element ordering. Use for broken, unopenable, "
    "or corrupt word files, not for generic 'fix' requests about other things.",
    "Docs",
    ["repair_docx_element_order", "validate_docx"],
    "STEP 1: Run repair_docx_element_order(path=...). This reorders out-of-schema rPr/pPr/tcPr/tblPr/"
    "sectPr children and moves w:sectPr to the end of the body. It rewrites the file in place.\n\n"
    "STEP 2: Read the JSON result. If repaired is true, list what changed. If ok is false with an "
    "error, report it (file missing, not a zip, unparseable XML).\n\n"
    "STEP 3: Always follow up with validate_docx(path=...) to confirm the file is structurally clean "
    "before declaring success.",
    ["repair docx", "docx won't open", "unreadable content",
     "repair the document", "document.xml repair", "broken file",
     "corrupt file", "file broken"],
    max_rounds=4, icon="wrench",
    trigger_regex=(
        r"(?=.*\b(repair|broken|corrupt|unreadable)\b)"
        r"(?=.*\b(docx|word|document|file)\b)"
    ),
))


SKILLS.append(_s(
    "Add Document Comment",
    "Insert a Word comment into a .docx anchored to a phrase. Use to annotate a generated "
    "document with a note, review mark, or suggested change before sending.",
    "Docs",
    ["add_docx_comment", "validate_docx"],
    "STEP 1: Call add_docx_comment(path=..., target_text=..., comment_text=..., author=...). "
    "target_text must appear verbatim in the document (first occurrence is anchored).\n\n"
    "STEP 2: Read the JSON result. If ok is true, confirm the comment_id and paragraph. "
    "If target_text was not found, report it and try a shorter substring.\n\n"
    "STEP 3: Optionally follow up with validate_docx(path=...) to confirm the file is still "
    "structurally valid after the edit.",
    ["add comment", "insert comment", "leave a comment", "add a note",
     "review note", "comment on file"],
    max_rounds=4, icon="comment",
    trigger_regex=(
        r"(?=.*\b(comment|annotate|note)\b)(?=.*\b(docx|word|document|file|pdf)\b)"
    ),
))
