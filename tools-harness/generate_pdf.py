#!/usr/bin/env python3
"""Convert a Markdown research report to a styled PDF using markdown + weasyprint.

Usage: python generate_pdf.py <input.md> [output.pdf]
"""

import markdown
import weasyprint
import os
import sys

# Read the markdown file
md_path = sys.argv[1] if len(sys.argv) > 1 else os.environ.get(
    "GENERATE_PDF_INPUT", "data/research_reports/report.md"
)
with open(md_path, "r", encoding="utf-8") as f:
    md_content = f.read()

# Convert markdown to HTML with extensions
html_content = markdown.markdown(
    md_content,
    extensions=[
        'markdown.extensions.tables',
        'markdown.extensions.fenced_code',
        'markdown.extensions.codehilite',
        'markdown.extensions.toc',
        'markdown.extensions.smarty',
        'markdown.extensions.extra',
    ]
)

# Wrap in a styled HTML document
styled_html = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<style>
    @page {
        size: A4;
        margin: 2.5cm 2cm 2.5cm 2cm;
        @bottom-center {
            content: counter(page);
            font-family: Helvetica, Arial, sans-serif;
            font-size: 9pt;
            color: #888;
        }
    }
    body {
        font-family: Helvetica, Arial, sans-serif;
        font-size: 10.5pt;
        line-height: 1.6;
        color: #1a1a1a;
    }
    h1 {
        font-size: 22pt;
        color: #1a3a5c;
        border-bottom: 3px solid #1a3a5c;
        padding-bottom: 12px;
        margin-top: 0;
        margin-bottom: 24px;
        page-break-before: avoid;
    }
    h2 {
        font-size: 16pt;
        color: #2c5f8a;
        border-bottom: 1.5px solid #b0c4de;
        padding-bottom: 6px;
        margin-top: 30px;
        margin-bottom: 14px;
        page-break-after: avoid;
    }
    h3 {
        font-size: 13pt;
        color: #3a7bb5;
        margin-top: 22px;
        margin-bottom: 10px;
        page-break-after: avoid;
    }
    h4 {
        font-size: 11.5pt;
        color: #4a8fc9;
        margin-top: 18px;
        margin-bottom: 8px;
        page-break-after: avoid;
    }
    p {
        margin: 8px 0;
        text-align: justify;
    }
    table {
        width: 100%;
        border-collapse: collapse;
        margin: 16px 0;
        font-size: 9.5pt;
        page-break-inside: avoid;
    }
    th {
        background-color: #1a3a5c;
        color: white;
        padding: 8px 10px;
        text-align: left;
        font-weight: 600;
    }
    td {
        padding: 6px 10px;
        border: 1px solid #d0d0d0;
        vertical-align: top;
    }
    tr:nth-child(even) {
        background-color: #f4f7fa;
    }
    tr:nth-child(odd) {
        background-color: #ffffff;
    }
    ul, ol {
        margin: 6px 0;
        padding-left: 24px;
    }
    li {
        margin: 3px 0;
    }
    blockquote {
        border-left: 4px solid #2c5f8a;
        margin: 16px 0;
        padding: 10px 16px;
        background-color: #f0f5fa;
    }
    blockquote p {
        margin: 4px 0;
    }
    strong {
        color: #1a3a5c;
    }
    hr {
        border: none;
        border-top: 1px solid #ccc;
        margin: 24px 0;
    }
    code {
        font-family: 'Courier New', monospace;
        font-size: 9pt;
        background-color: #f0f0f0;
        padding: 1px 4px;
        border-radius: 3px;
    }
    pre {
        background-color: #f5f5f5;
        border: 1px solid #ddd;
        border-radius: 4px;
        padding: 12px;
        overflow-x: auto;
        font-size: 9pt;
    }
    pre code {
        background: none;
        padding: 0;
    }
</style>
</head>
<body>
""" + html_content + """
</body>
</html>"""

# Generate PDF
output_path = sys.argv[2] if len(sys.argv) > 2 else os.path.join(
    os.path.expanduser("~"), "AI_Predictions_2030_Report.pdf"
)
weasyprint.HTML(string=styled_html).write_pdf(output_path)

size_kb = os.path.getsize(output_path) / 1024
print(f"PDF successfully created at: {output_path}")
print(f"File size: {size_kb:.1f} KB")
