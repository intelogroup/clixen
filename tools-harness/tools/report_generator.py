"""
Report Generator — Offloads report synthesis and formatting from LLM to Python.
Supports compiling multiple SearchResult outputs into structured reports.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from tools.search_result import SearchResult, SearchSnippet


def compile_research_report(results_json: str, 
                             topic: str, 
                             output_path: str, 
                             format: str = "markdown") -> str:
    """
    Compile multiple search results into a structured research report.
    
    Args:
        results_json: JSON string containing a list of SearchResult objects (as dicts).
        topic: The main topic of the research.
        output_path: Where to save the generated report.
        format: 'markdown' or 'html'.
        
    Returns:
        Path to the generated report or error message.
    """
    try:
        raw_results = json.loads(results_json)
        # Handle both a single list of results or a dict with a list
        if isinstance(raw_results, dict) and "results" in raw_results:
            raw_results = raw_results["results"]
        elif isinstance(raw_results, dict):
            # Maybe it's a single result object
            raw_results = [raw_results]
        
        if not isinstance(raw_results, list):
            return f"Error: results_json must be a list of search results. Got {type(raw_results).__name__}"

        results = []
        for r in raw_results:
            try:
                # Basic validation: must have 'source' or 'content'
                if not isinstance(r, dict) or ("source" not in r and "content" not in r):
                    continue
                # Coerce dict into something SearchResult can handle
                content = r.get("content", "")
                ok = r.get("ok", True)
                source = r.get("source", "unknown")
                query = r.get("query", "")
                items = r.get("items", [])
                
                res = SearchResult(content=content, ok=ok, source=source, query=query, items=items)
                results.append(res)
            except Exception as e:
                print(f"Skipping invalid result: {e}")
                continue
                
        if not results:
            return "Error: No valid search results provided to compile. Ensure results are a list of JSON objects."

        report_lines = [
            f"# Research Report: {topic}",
            f"*Generated on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*",
            "",
            "## Executive Summary",
            f"This report compiles findings on **{topic}** from multiple intelligence sources.",
            "",
            "## Key Findings by Source",
            ""
        ]

        all_snippets = []
        for res in results:
            source_name = res.source.capitalize() or "Unknown Source"
            report_lines.append(f"### Source: {source_name}")
            report_lines.append(f"**Query:** `{res.query}`")
            report_lines.append("")
            
            if res.ok and res.items:
                for item in res.items:
                    # SearchResult constructor handles SearchSnippet conversion
                    snippet = item
                    all_snippets.append(snippet)
                    report_lines.append(f"- **[{snippet.title}]({snippet.url})**")
                    report_lines.append(f"  > {snippet.snippet}")
                    if snippet.published_date:
                        report_lines.append(f"  *Published: {snippet.published_date}*")
                    report_lines.append("")
            elif res.ok and res.content:
                # Fallback for plain content results
                report_lines.append(f"#### Summary from Source")
                report_lines.append(res.content[:1000] + ("..." if len(res.content) > 1000 else ""))
                report_lines.append("")
            else:
                report_lines.append(f"*No results found for this source (Reason: {res.error or 'Empty'})*")
                report_lines.append("")

        # Add a consolidated Bibliography/Sources section
        if all_snippets:
            report_lines.append("## Consolidated Bibliography")
            seen_urls = set()
            for snippet in all_snippets:
                if snippet.url and snippet.url not in seen_urls:
                    report_lines.append(f"- [{snippet.title or snippet.url}]({snippet.url})")
                    seen_urls.add(snippet.url)

        report_content = "\n".join(report_lines)
        
        p = Path(output_path).expanduser()
        p.parent.mkdir(parents=True, exist_ok=True)
        
        if format.lower() == "html":
            try:
                import markdown
                html = markdown.markdown(report_content, extensions=["extra", "tables"])
            except ImportError:
                # Basic fallback if markdown lib is missing
                html = f"<pre>{report_content}</pre>"

            # Add basic styling
            styled_html = f"""
            <!DOCTYPE html>
            <html>
            <head>
                <meta charset="utf-8">
                <title>Research Report: {topic}</title>
                <style>
                    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; line-height: 1.6; color: #333; max-width: 900px; margin: 0 auto; padding: 2rem; }}
                    h1 {{ color: #1a73e8; border-bottom: 2px solid #e8eaed; padding-bottom: 0.5rem; }}
                    h2, h3 {{ color: #1a73e8; margin-top: 2rem; }}
                    blockquote {{ border-left: 4px solid #1a73e8; padding-left: 1rem; color: #5f6368; font-style: italic; background: #f8f9fa; padding: 10px; }}
                    hr {{ border: 0; border-top: 1px solid #e8eaed; margin: 2rem 0; }}
                    a {{ color: #1a73e8; text-decoration: none; }}
                    a:hover {{ text-decoration: underline; }}
                    li {{ margin-bottom: 1rem; }}
                    code {{ background: #f1f3f4; padding: 0.2rem 0.4rem; border-radius: 4px; font-size: 0.9rem; }}
                </style>
            </head>
            <body>
                {html}
            </body>
            </html>
            """
            p.write_text(styled_html, encoding="utf-8")
        else:
            p.write_text(report_content, encoding="utf-8")

        return str(p)
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        return f"Error compiling report: {str(e)}"


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

COMPILE_REPORT_SCHEMA = {
    "type": "function",
    "function": {
        "name": "compile_research_report",
        "description": (
            "Consolidate multiple search results into a professional research report. "
            "Use this to offload formatting and synthesis from the LLM. "
            "Pass the raw JSON list of results from previous web_search or tech_search calls."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "results_json": {
                    "type": "string", 
                    "description": "JSON string containing a list of SearchResult objects (e.g. [res1, res2])"
                },
                "topic": {"type": "string", "description": "The main topic or title for the report"},
                "output_path": {"type": "string", "description": "Where to save the report (e.g. ~/Desktop/report.md)"},
                "format": {
                    "type": "string", 
                    "enum": ["markdown", "html"], 
                    "default": "markdown",
                    "description": "Output format: markdown or html"
                },
            },
            "required": ["results_json", "topic", "output_path"],
        },
    },
}


def execute(args: dict) -> str:
    return compile_research_report(
        results_json=args["results_json"],
        topic=args["topic"],
        output_path=args["output_path"],
        format=args.get("format", "markdown")
    )
