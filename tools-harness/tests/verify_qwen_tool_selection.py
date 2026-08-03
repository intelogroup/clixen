"""
Comprehensive tool selection verification for qwen3.5:4b.

Tests whether the qwen3.5:4b model can successfully map user queries
to each of the 37 tools in the local agent toolset.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

# Ensure tools-harness is in path
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import ollama
from agents.local_agent_tools import get_local_agent_tools
from agents.specialists.data_specialist import _DATA_TOOL_SCHEMAS
from agents.local_agent_nodes import _LOCAL_AGENT_SYSTEM_PROMPT_DEFAULT


# Define test cases for all key tools in local agent toolset (37 tools)
TEST_CASES = [
    # 1. Core filesystem
    {
        "category": "Filesystem",
        "name": "list_directory",
        "query": "list the files in the directory /tmp",
        "expected": ["list_directory", "bash_exec"],
    },
    {
        "category": "Filesystem",
        "name": "find_files",
        "query": "find all files with extension .pdf in the folder /Users/kalinovdameus",
        "expected": ["find_files"],
    },
    {
        "category": "Filesystem",
        "name": "read_file",
        "query": "read the file /tmp/hello.txt and tell me what's in it",
        "expected": ["read_file", "read_document"],
    },
    {
        "category": "Filesystem",
        "name": "write_file",
        "query": "write the text 'hello world' to a file at /tmp/hello.txt",
        "expected": ["write_file"],
    },
    {
        "category": "Filesystem",
        "name": "edit_file",
        "query": "edit the file /tmp/hello.txt to replace 'hello' with 'hi'",
        "expected": ["edit_file"],
    },
    {
        "category": "Filesystem",
        "name": "create_directory",
        "query": "create a new directory /tmp/my_new_dir",
        "expected": ["create_directory", "bash_exec"],
    },
    # 2. Audio
    {
        "category": "Audio",
        "name": "transcribe_audio",
        "query": "transcribe the audio recording meeting.mp3 to text",
        "expected": ["transcribe_audio"],
    },
    # 3. Documents
    {
        "category": "Documents",
        "name": "read_pdf",
        "query": "read the PDF document at /tmp/paper.pdf and tell me what is on page 2",
        "expected": ["read_pdf", "read_document"],
    },
    {
        "category": "Documents",
        "name": "read_document",
        "query": "open and read the Word document at /tmp/resume.docx",
        "expected": ["read_document"],
    },
    # 4. Form tools
    {
        "category": "Forms",
        "name": "detect_form_fields",
        "query": "detect the form fields in the PDF form /tmp/w4.pdf",
        "expected": ["detect_form_fields", "detect_pdf_form_fields"],
    },
    {
        "category": "Forms",
        "name": "fill_form",
        "query": "fill out the PDF form /tmp/w4.pdf directly with fields: Name='Jim', Age='30' (assume field detection has already been done)",
        "expected": ["fill_form", "fill_pdf_form"],
    },
    {
        "category": "Forms",
        "name": "update_form",
        "query": "update the form /tmp/w4.pdf with field updates: Status='Married'",
        "expected": ["update_form", "fill_form", "fill_pdf_form"],
    },
    {
        "category": "Forms",
        "name": "vision_detect_form_fields",
        "query": "detect the form fields in the scanned image /tmp/scanned.png using vision-based detection",
        "expected": ["vision_detect_form_fields"],
    },
    {
        "category": "Forms",
        "name": "vision_fill_form_fields",
        "query": "fill out the scanned form image /tmp/scanned.png by using vision to write 'John' in the Name field",
        "expected": ["vision_fill_form_fields"],
    },
    {
        "category": "Forms",
        "name": "detect_flat_pdf_fields",
        "query": "detect flat form fields on the non-interactive PDF /tmp/flat.pdf that has no AcroForm fields",
        "expected": ["detect_flat_pdf_fields"],
    },
    {
        "category": "Forms",
        "name": "fill_flat_pdf",
        "query": "fill out the flat PDF /tmp/flat.pdf using the flat-field coordinates",
        "expected": ["fill_flat_pdf"],
    },
    # 5. Document creation
    {
        "category": "Doc Creation",
        "name": "data_to_xlsx",
        "query": "convert the CSV dataset /tmp/data.csv to an Excel xlsx spreadsheet at /tmp/data.xlsx",
        "expected": ["data_to_xlsx"],
    },
    {
        "category": "Doc Creation",
        "name": "markdown_to_pptx",
        "query": "convert the markdown deck /tmp/slides.md to a PowerPoint pptx presentation at /tmp/slides.pptx",
        "expected": ["markdown_to_pptx"],
    },
    # 6. Shell
    {
        "category": "Shell",
        "name": "bash_exec",
        "query": "run a bash shell command to see the git status of the project",
        "expected": ["bash_exec"],
    },
    # 7. Data Analysis / Stats (15 tools)
    {
        "category": "Data Analysis",
        "name": "run_python",
        "query": "execute a python script to calculate the standard deviation of list [10, 12, 15, 20]",
        "expected": ["run_python"],
    },
    {
        "category": "Data Analysis",
        "name": "analyze_dataset",
        "query": "analyze the dataset stored in the CSV file /tmp/data.csv and summarize the columns",
        "expected": ["analyze_dataset"],
    },
    {
        "category": "Data Analysis",
        "name": "query_data",
        "query": "query the dataset /tmp/data.csv using SQL logic to find rows where age > 30",
        "expected": ["query_data"],
    },
    {
        "category": "Data Analysis",
        "name": "plot_chart",
        "query": "plot a bar chart of column sales by region from /tmp/data.csv and save to chart.png",
        "expected": ["plot_chart"],
    },
    {
        "category": "Data Analysis",
        "name": "correlate",
        "query": "calculate the correlation matrix between the variables in /tmp/data.csv",
        "expected": ["correlate"],
    },
    {
        "category": "Data Analysis",
        "name": "run_stats_test",
        "query": "run a statistical t-test or ANOVA comparing column sales for Group A vs Group B",
        "expected": ["run_stats_test"],
    },
    {
        "category": "Data Analysis",
        "name": "run_regression",
        "query": "run a linear regression model predicting sales using columns advertising and pricing",
        "expected": ["run_regression"],
    },
    {
        "category": "Data Analysis",
        "name": "make_summary_table",
        "query": "create a beautiful summary statistics table for columns in /tmp/data.csv",
        "expected": ["make_summary_table"],
    },
    {
        "category": "Data Analysis",
        "name": "plot_interactive",
        "query": "generate an interactive plotly chart or plot from columns in /tmp/data.csv",
        "expected": ["plot_interactive", "plot_chart"],
    },
    {
        "category": "Data Analysis",
        "name": "detect_outliers",
        "query": "detect or identify anomalies and outliers in column sales of dataset /tmp/data.csv",
        "expected": ["detect_outliers"],
    },
    {
        "category": "Data Analysis",
        "name": "run_pca",
        "query": "run a principal component analysis (PCA) on the columns of /tmp/data.csv",
        "expected": ["run_pca"],
    },
    {
        "category": "Data Analysis",
        "name": "plot_roc",
        "query": "calculate and plot the ROC curve for the classification models in python",
        "expected": ["plot_roc", "plot_chart", "run_python"],
    },
    {
        "category": "Data Analysis",
        "name": "run_full_analysis",
        "query": "run a full comprehensive data analysis workflow on the dataset /tmp/data.csv",
        "expected": ["run_full_analysis"],
    },
    {
        "category": "Data Analysis",
        "name": "run_time_series",
        "query": "run a time series analysis and forecast future values using dates and sales columns",
        "expected": ["run_time_series"],
    },
    {
        "category": "Data Analysis",
        "name": "run_survival",
        "query": "run a survival analysis or Cox proportional hazards regression on /tmp/patient_data.csv",
        "expected": ["run_survival"],
    },
    {
        "category": "Data Analysis",
        "name": "run_meta_analysis",
        "query": "run a meta-analysis or pool effect sizes on the clinical studies in /tmp/studies.csv",
        "expected": ["run_meta_analysis"],
    },
]


def main() -> int:
    model = "qwen3.5:4b"
    
    # 1. Get the full 37 tools returned by get_local_agent_tools()
    # (data specialist is imported above, so its schemas are automatically appended to ALL_TOOLS)
    tools = get_local_agent_tools()
    
    print(f"\033[1m\033[36m=== Qwen 3.5 Tool Selection Verification ===\033[0m")
    print(f"\033[2mModel under test: {model}\033[0m")
    print(f"\033[2mTotal active test tools: {len(tools)}\033[0m\n")
    
    # 2. Build the premium system prompt with real home dir and data specialist guidance
    home_dir = str(Path.home())
    base_prompt = _LOCAL_AGENT_SYSTEM_PROMPT_DEFAULT.format(home_dir=home_dir)
    
    data_guide = (
        "\n\nDATA ANALYSIS TOOL SELECTION GUIDE:\n"
        "- To ANALYZE a structured dataset (CSV/Excel/JSON) and get basic stats: call analyze_dataset(path='X')\n"
        "- To QUERY a dataset using DuckDB SQL (filter, group, join): call query_data(path='X', sql='SELECT...')\n"
        "- To PLOT a static chart (bar, line, scatter, hist, heatmap, pie): call plot_chart(path='X', type='...', x='...', y='...')\n"
        "- To COMPUTE a correlation matrix and show a heatmap: call correlate(path='X')\n"
        "- To RUN a statistical test (ttest, anova, kruskal, normality, vif, chi2, etc.): call run_stats_test(path='X', test='...')\n"
        "- To RUN a regression model (OLS, logistic/logit, GLM): call run_regression(path='X', formula='...', x='...', y='...')\n"
        "- To GENERATE an APA descriptive statistics table: call make_summary_table(path='X')\n"
        "- To PLOT an interactive Plotly chart (box, violin, scatter, line): call plot_interactive(path='X', type='...', x='...', y='...')\n"
        "- To DETECT outliers and anomalies (IQR/z-score): call detect_outliers(path='X', method='...')\n"
        "- To RUN Principal Component Analysis (PCA) and biplot: call run_pca(path='X')\n"
        "- To PLOT a ROC (Receiver Operating Characteristic) curve and compute AUC: call plot_roc(path='X', y_true='...', y_score='...')\n"
        "- To RUN time series ARIMA modeling and forecast: call run_time_series(path='X', date_col='...', value_col='...')\n"
        "- To RUN survival analysis (Kaplan-Meier, Cox PH regression): call run_survival(path='X', time_col='...', event_col='...')\n"
        "- To RUN a meta-analysis or pool clinical effect sizes: call run_meta_analysis(path='X', effect_col='...', variance_col='...')\n"
        "- To RUN a COMPLETE automated end-to-end data analysis pipeline: call run_full_analysis(path='X')\n\n"
        "FORM TOOL SELECTION GUIDE:\n"
        "- To DETECT form fields in a standard PDF/DOCX: call detect_form_fields(file_path='X')\n"
        "- To FILL a standard PDF/DOCX directly (when you already have field names/values): call fill_form(file_path='X', fields='...')\n"
        "- To UPDATE specific fields in an already-filled form: call update_form(file_path='X', field_updates='...')\n"
        "- To DETECT interactive AcroForm fields specifically: call detect_pdf_form_fields(path='X')\n"
        "- To FILL an interactive AcroForm PDF specifically: call fill_pdf_form(path='X', fields='...')\n"
        "- To DETECT fields on a scanned/flat PDF using OCR + drawings: call detect_flat_pdf_fields(path='X')\n"
        "- To FILL a flat PDF using detected coordinates: call fill_flat_pdf(path='X', fields_json='...', values_json='...')\n"
        "- To DETECT fields in a scanned image using vision-based detection: call vision_detect_form_fields(path='X')\n"
        "- To FILL a scanned form image using vision: call vision_fill_form_fields(path='X', fields_json='...', values_json='...')\n\n"
        "Examples:\n"
        "- 'calculate correlation matrix' → correlate(path='/tmp/data.csv')\n"
        "- 'run linear regression predicting y' → run_regression(path='/tmp/data.csv', formula='y ~ x1 + x2')\n"
        "- 'compare means with t-test' → run_stats_test(path='/tmp/data.csv', test='ttest', x='group', y='score')\n"
        "- 'plot scatter of weight by height' → plot_chart(path='/tmp/data.csv', type='scatter', x='height', y='weight')\n"
        "- 'query data to find active rows' → query_data(path='/tmp/data.csv', sql='SELECT * FROM df WHERE active=1')\n"
        "- 'detect flat form fields on the non-interactive PDF' → detect_flat_pdf_fields(path='/tmp/flat.pdf')\n"
        "- 'fill out the flat PDF using flat-field coordinates' → fill_flat_pdf(path='/tmp/flat.pdf', fields_json='...', values_json='...')\n"
        "- 'detect form fields in scanned image using vision' → vision_detect_form_fields(path='/tmp/scanned.png')\n"
        "- 'fill out scanned form image using vision' → vision_fill_form_fields(path='/tmp/scanned.png', fields_json='...', values_json='...')\n\n"
        "NEVER narrate. NEVER say 'I will...' or 'Let me...'. Just call the tool immediately."
    )
    
    premium_prompt = base_prompt + data_guide
    
    passed_count = 0
    failed_cases = []
    
    header = f"  {'Category':<15} {'Target Tool':<25} {'Selected Tool':<25} {'Status':<10}"
    print(header)
    print("  " + "-" * len(header))
    
    for i, case in enumerate(TEST_CASES, 1):
        messages = [
            {
                "role": "system",
                "content": premium_prompt,
            },
            {
                "role": "user",
                "content": case["query"],
            },
        ]
        
        t0 = time.perf_counter()
        try:
            resp = ollama.chat(
                model=model,
                messages=messages,
                tools=tools,
                options={
                    "think": False,
                    "temperature": 0.0,
                },
                keep_alive=-1,
            )
            elapsed = time.perf_counter() - t0
            
            tool_calls = resp.message.tool_calls or []
            selected_tool = tool_calls[0].function.name if tool_calls else "NONE (text response)"
            
            status = "\033[31mFAIL\033[0m"
            is_ok = False
            if selected_tool in case["expected"]:
                status = "\033[32mPASS\033[0m"
                is_ok = True
                passed_count += 1
            else:
                failed_cases.append({
                    "case": case,
                    "selected": selected_tool,
                    "response_content": resp.message.content or "",
                })
                
            print(f"  {case['category']:<15} {case['name']:<25} {selected_tool:<25} {status:<10} ({elapsed:.2f}s)")
            
        except Exception as e:
            elapsed = time.perf_counter() - t0
            print(f"  {case['category']:<15} {case['name']:<25} \033[31mERROR\033[0m: {type(e).__name__:<17} \033[31mFAIL\033[0m ({elapsed:.2f}s)")
            failed_cases.append({
                "case": case,
                "selected": f"Exception: {type(e).__name__}",
                "response_content": str(e),
            })
            
    print("\n\033[1m=== Verification Summary ===\033[0m")
    total = len(TEST_CASES)
    pass_pct = (passed_count / total) * 100
    color = "\033[32m" if pass_pct == 100 else ("\033[33m" if pass_pct >= 80 else "\033[31m")
    print(f"  Total Cases: {total}")
    print(f"  Passed     : {passed_count}")
    print(f"  Failed     : {len(failed_cases)}")
    print(f"  Pass Rate  : {color}{pass_pct:.1f}%\033[0m\n")
    
    if failed_cases:
        print("\033[1m\033[31mFailed Cases Details:\033[0m")
        for fc in failed_cases:
            case = fc["case"]
            print(f"  - \033[1m{case['category']}/{case['name']}\033[0m")
            print(f"    Query   : {case['query']}")
            print(f"    Expected: {case['expected']}")
            print(f"    Selected: {fc['selected']}")
            if fc["response_content"]:
                print(f"    Response: {fc['response_content'][:200]}")
            print()
            
    return 0 if len(failed_cases) == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
