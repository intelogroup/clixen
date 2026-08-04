"""
Regression test: agents/specialists/dispatch.py classify() must route document
READING queries to the "read" specialist, never to "scraper" or None.

Confirmed live (chat_ui.log, 2026-08-03, session f8d213d4-...): every one of
these user turns failed to deliver — "extract text from the PDF file at
/Users/kalinovda..." matched=scraper (scraper's bare \\bextract\\b accept stole
it), and the rest returned None (no specialist), dumping the task on the weak
LLM loop, which then refused the PDF ("encoding issues"), read the wrong file
(AGENTS.md instead of the oxygen .md), and claimed LibreOffice wasn't installed.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from agents.specialists.dispatch import classify

DOC_QUERIES = [
    "extract text from the PDF file at /Users/kalinovdameus/developer/benoucheca/perso/006-006 Haiti oxygen strategy outline.pdf",
    "can you read the 006-006 Haiti oxygen strategy outline.docx",
    "verify the .md file version",
    "no verify the docs here to see which one is the bhi report",
    "use libreoffice",
    "great!  verify the BHI report and tell me what are the comments are",
    "Verify the BHI report in the directory /Users/kalinovdameus/developer/benoucheca/perso",
    "use it to read the docs",
    "read the 006-006 Haiti oxygen strategy outline.pdf",
    "what is in this file",
]

WEB_QUERIES = [
    "extract the prices from this website",
    "scrape that webpage",
    "screenshot the landing page",
]

NON_READ_QUERIES = [
    "write a new script",
    "analyze this csv",
    "fill out the form in resume.pdf",
]


def test_doc_read_queries_route_to_read():
    for q in DOC_QUERIES:
        assert classify(q) == "read", f"{q!r} -> {classify(q)!r}, want 'read'"


def test_web_scrape_queries_stay_on_scraper():
    for q in WEB_QUERIES:
        assert classify(q) == "scraper", f"{q!r} -> {classify(q)!r}, want 'scraper'"


def test_non_read_queries_do_not_route_to_read():
    for q in NON_READ_QUERIES:
        assert classify(q) != "read", f"{q!r} -> {classify(q)!r}, expected not 'read'"


if __name__ == "__main__":
    test_doc_read_queries_route_to_read()
    test_web_scrape_queries_stay_on_scraper()
    test_non_read_queries_do_not_route_to_read()
    print("specialist dispatch doc-routing tests passed")
