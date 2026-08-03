"""
Central registry of all tool schemas and executors.
Add new tools here — harness.py imports from this file only.
"""

import fnmatch
import logging as _logging
import os
import contextvars
from tools.search_result import SearchResult
import tools.websearch as websearch

CURRENT_CHAT_ID: contextvars.ContextVar[str | None] = contextvars.ContextVar("current_chat_id", default=None)

from tools.orchestrator_tools import (
    ASK_LOCAL_AGENT_SCHEMA, ASK_WEB_SEARCH_SCHEMA, ASK_BROWSER_AGENT_SCHEMA,
    ASK_MACOS_NATIVE_SCHEMA, ASK_EMAIL_AGENT_SCHEMA, ASK_TASKS_AGENT_SCHEMA,
    ASK_CALENDAR_AGENT_SCHEMA, ASK_DEEP_RESEARCH_SCHEMA,
    ASK_DOCS_AGENT_SCHEMA, ASK_SHEETS_AGENT_SCHEMA,
    ASK_READ_FILE_SCHEMA, ASK_WRITE_FILE_SCHEMA, ASK_DELETE_FILE_SCHEMA,
    ASK_RENAME_FILE_SCHEMA, ASK_RUN_COMMAND_SCHEMA, ASK_FETCH_URL_SCHEMA,
    ASK_AUTOMATION_AGENT_SCHEMA, ASK_DEV_AGENT_SCHEMA, ASK_MESSAGING_AGENT_SCHEMA,
    ASK_RESEARCH_AGENT_SCHEMA, ASK_VISION_AGENT_SCHEMA, ASK_UTILITY_AGENT_SCHEMA,
    ASK_TRANSPORT_AGENT_SCHEMA, ASK_YOUTUBE_AGENT_SCHEMA, ASK_X_AGENT_SCHEMA,
    ASK_REDDIT_AGENT_SCHEMA,
    ASK_SCIENCE_SCOUT_AGENT_SCHEMA,
    ASK_SEND_MESSAGE_SCHEMA,
    QUERY_RECENT_TRACES_SCHEMA,
)
from tools.make_phone_call import SCHEMA as MAKE_PHONE_CALL_SCHEMA
from tools.deep_research import SCHEMA as DEEP_RESEARCH_SCHEMA, execute as deep_research_execute
from tools.searxng_search import SCHEMA as SEARXNG_SCHEMA
from tools.scrapling_fetch import (
    SCHEMA as SCRAPLING_FETCH_SCHEMA,
    STEALTHY_SCHEMA as SCRAPLING_STEALTHY_SCHEMA,
    EXTRACT_SCHEMA as SCRAPLING_EXTRACT_SCHEMA,
    FETCH_AND_EXTRACT_SCHEMA as SCRAPLING_FETCH_AND_EXTRACT_SCHEMA,
    _scrapling_fetch, _scrapling_stealthy_fetch,
    _scrapling_extract, _scrapling_fetch_and_extract,
)

_search_log = _logging.getLogger("websearch")


def _to_model_str(result: SearchResult | str) -> str:
    """Unwrap SearchResult → model string. Pass through plain strings unchanged."""
    if isinstance(result, SearchResult):
        return result.to_model_str()
    return result


from tools.searxng_search import execute as _searxng_execute
from tools.local_search import SCHEMA as LOCAL_SCHEMA, execute as local_execute
from tools.audio import SCHEMA as AUDIO_SCHEMA, execute as audio_execute
from tools.audio_tools import CONVERT_AUDIO_SCHEMA, convert_audio as _convert_audio
from tools.ocr import SCHEMA as OCR_SCHEMA, execute as ocr_execute
from tools.surya_ocr import SCHEMA as SURYA_OCR_SCHEMA, execute as surya_ocr_execute
from tools.video_tools import (
    YTDLP_SCHEMA, FFMPEG_SCHEMA,
    ytdlp_execute, ffmpeg_execute,
)
from tools.fast_search import (
    GLOB_SCHEMA, FD_SCHEMA, RG_SCHEMA,
    glob_execute, fd_execute, rg_execute,
)
from tools.peekaboo import (
    SCHEMA as PEEKABOO_SCHEMA,
    LIST_WINDOWS_SCHEMA as PEEKABOO_LIST_WINDOWS_SCHEMA,
    execute as peekaboo_execute,
    list_windows as peekaboo_list_windows,
)
from tools.slack_search import (
    SEARCH_SCHEMA as SLACK_SEARCH_SCHEMA,
    STATUS_SCHEMA as SLACK_STATUS_SCHEMA,
    search as slack_search_execute,
    status as slack_status_execute,
)
from tools.imessage_search import (
    SEARCH_SCHEMA as IMESSAGE_SEARCH_SCHEMA,
    STATUS_SCHEMA as IMESSAGE_STATUS_SCHEMA,
    SEND_SCHEMA as IMESSAGE_SEND_SCHEMA,
    search as imessage_search_execute,
    status as imessage_status_execute,
    send as imessage_send_execute,
)
from tools.contacts_resolver import (
    SCHEMA as CONTACTS_RESOLVE_SCHEMA,
    execute as contacts_resolve_execute,
)
from tools.set_opencode_model import (
    SCHEMA as SET_OPENCODE_MODEL_SCHEMA,
    execute as set_opencode_model_execute,
)
from tools.rga_tool import (
    SCHEMA as RGA_SCHEMA,
    execute as rga_execute,
)
from tools.docling_tool import (
    SCHEMA as DOCLING_SCHEMA,
    execute as docling_execute,
)
from tools.spotlight import (
    SCHEMA as SPOTLIGHT_SCHEMA,
    FIND_RECENT_SCHEMA,
    execute as spotlight_execute,
    find_recent as spotlight_find_recent,
)
from tools.macos_native import (
    CLIPBOARD_READ_SCHEMA,
    CLIPBOARD_WRITE_SCHEMA,
    SAFARI_TABS_SCHEMA,
    NOTES_LIST_SCHEMA,
    NOTES_CREATE_SCHEMA,
    APPLESCRIPT_SCHEMA,
    clipboard_read as mn_clipboard_read,
    clipboard_write as mn_clipboard_write,
    list_safari_tabs as mn_list_safari_tabs,
    list_notes as mn_list_notes,
    notes_create as mn_notes_create,
    applescript_run as mn_applescript_run,
)
from tools.system_status import (
    SCHEMA as SYSTEM_STATUS_SCHEMA,
    execute as system_status_execute,
)
from tools.reminders import (
    LIST_REMINDERS_SCHEMA,
    CREATE_REMINDER_SCHEMA,
    LIST_CAL_SCHEMA,
    list_reminders as rem_list,
    create_reminder as rem_create,
    list_calendar_events as cal_list,
)
from tools.email_parse import (
    SCHEMA as EMAIL_PARSE_SCHEMA,
    execute as email_parse_execute,
)
from tools.whatsapp_search import (
    SEARCH_SCHEMA as WHATSAPP_SEARCH_SCHEMA,
    STATUS_SCHEMA as WHATSAPP_STATUS_SCHEMA,
    search as whatsapp_search_execute,
    status as whatsapp_status_execute,
)
from tools.document_create import (
    MARKDOWN_TO_DOCX_SCHEMA,
    MARKDOWN_TO_PDF_SCHEMA,
    CREATE_PDF_SCHEMA,
    JSON_TO_DOCX_SCHEMA,
    DATA_TO_XLSX_SCHEMA,
    MARKDOWN_TO_PPTX_SCHEMA,
    TEXT_TO_FILE_SCHEMA,
    HTML_TO_FILE_SCHEMA,
    markdown_to_docx_executor,
    markdown_to_pdf_executor,
    create_pdf_executor,
    json_to_docx_executor,
    data_to_xlsx_executor,
    markdown_to_pptx_executor,
    text_to_file_executor,
    html_to_file_executor,
)
from tools.diagram_render import RENDER_DIAGRAM_SCHEMA, render_diagram_executor
from tools.image_edit import EDIT_IMAGE_SCHEMA, edit_image_executor
from tools.github_search import (
    GITHUB_SEARCH_SCHEMA, github_search_executor,
    GITHUB_LIST_PRS_SCHEMA, github_list_prs_executor,
    GITHUB_CREATE_ISSUE_SCHEMA, github_create_issue_executor,
    GITHUB_CREATE_PR_SCHEMA, github_create_pr_executor,
)
from tools.filesystem import (
    READ_FILE_SCHEMA,
    GREP_FILES_SCHEMA,
    FIND_FILES_SCHEMA,
    LIST_DIR_SCHEMA,
    FILE_TREE_SCHEMA,
    READ_MANY_SCHEMA,
    FILE_INFO_SCHEMA,
    FIND_LARGEST_SCHEMA,
    RENAME_FILE_SCHEMA,
    DELETE_FILE_SCHEMA,
    CREATE_DIRECTORY_SCHEMA,
    COPY_FILE_SCHEMA,
    read_file,
    grep_files,
    find_files,
    list_directory,
    file_tree,
    read_many_files,
    file_info,
    find_largest,
    rename_file,
    delete_file,
    create_directory,
    copy_file,
)
from tools.structured import (
    READ_DOCUMENT_SCHEMA,
    PARSE_FILE_SCHEMA,
    READ_PDF_SCHEMA,
    PARSE_CODE_SCHEMA,
    read_document,
    parse_file,
    read_pdf,
    parse_code,
)
from tools.semantic_files import (
    INDEX_DIR_SCHEMA,
    SEMANTIC_SEARCH_SCHEMA,
    index_directory,
    semantic_file_search,
)
from tools.shell import (
    BASH_EXEC_SCHEMA,
    WRITE_FILE_SCHEMA,
    EDIT_FILE_SCHEMA,
    EDIT_FILE_FUZZY_SCHEMA,
    APPEND_FILE_SCHEMA,
    UNDO_LAST_EDIT_SCHEMA,
    DOWNLOAD_URL_SCHEMA,
    bash_exec,
    write_file,
    edit_file,
    edit_file_fuzzy,
    append_file,
    undo_last_edit,
    download_url,
)
from tools.context7 import SCHEMA as CONTEXT7_SCHEMA, execute as context7_execute
from tools.git import (
    GIT_STATUS_SCHEMA,
    GIT_DIFF_SCHEMA,
    GIT_LOG_SCHEMA,
    GIT_ADD_SCHEMA,
    GIT_COMMIT_SCHEMA,
    GIT_CHECKOUT_SCHEMA,
    GIT_WORKTREE_SCHEMA,
    git_status,
    git_diff,
    git_log,
    git_add,
    git_commit,
    git_checkout,
    git_new_worktree,
)
from tools.repl import (
    RUN_PYTHON_SCHEMA,
    RESET_KERNEL_SCHEMA,
    LIST_VARS_SCHEMA,
    run_python,
    reset_kernel,
    list_kernel_vars,
)
from tools.reminder import SET_REMINDER_SCHEMA, set_reminder
from tools.opencode_tool import ASK_OPENCODE_SCHEMA, exec_ask_opencode
from tools.telegram_send import SEND_TELEGRAM_SCHEMA, send_telegram
from tools.time_tool import GET_CURRENT_TIME_SCHEMA, get_current_time
from tools.bus_eta import BUS_ETA_SCHEMA, bus_eta
from tools.gmail import (
    LIST_EMAILS_SCHEMA,
    READ_EMAIL_SCHEMA,
    SEND_EMAIL_SCHEMA,
    GET_LATEST_EMAIL_SCHEMA,
    DOWNLOAD_GMAIL_ATTACHMENT_SCHEMA,
    LIST_GMAIL_ATTACHMENTS_SCHEMA,
    list_emails,
    read_email,
    send_email,
    get_latest_email,
    download_attachment,
    list_all_attachments,
)
from tools.gmail import list_emails_with_attachments
from tools.agent_reach import SEARCH_CHINESE_WEB_SCHEMA, search_chinese_web
from tools.email_attachments import LIST_EMAIL_ATTACHMENTS_SCHEMA, list_email_attachments
from tools.image_generation import GENERATE_LOCAL_IMAGE_SCHEMA, generate_local_image
from tools.pdf_tools import pdf_to_markdown, extract_pdf_images, detect_pdf_form_fields, fill_pdf_form
from tools.form_tools import detect_form_fields, fill_form, update_form
from tools.vision_form_tools import (
    vision_detect_form_fields,
    vision_fill_form_fields,
    vision_update_form_fields,
)
from tools.flat_pdf_tools import (
    DETECT_FLAT_PDF_SCHEMA,
    FILL_FLAT_PDF_SCHEMA,
    detect_flat_pdf_fields as _detect_flat_pdf_fields,
    fill_flat_pdf as _fill_flat_pdf,
)
from tools.google_auth import REFRESH_TOKEN_SCHEMA, refresh_google_token
from tools.gcalendar import (
    LIST_EVENTS_SCHEMA,
    CREATE_EVENT_SCHEMA,
    DELETE_EVENT_SCHEMA,
    list_calendar_events,
    create_calendar_event,
    delete_calendar_event,
)
from tools.gtasks import (
    LIST_TASKS_SCHEMA,
    CREATE_TASK_SCHEMA,
    COMPLETE_TASK_SCHEMA,
    DELETE_TASK_SCHEMA,
    list_tasks,
    create_task,
    complete_task,
    delete_task,
)
from tools.browser import (
    ALL_BROWSER_SCHEMAS,
    browser_navigate,
    browser_snapshot,
    browser_get_url,
    browser_click,
    browser_type,
    browser_check,
    browser_select,
    browser_wait,
    browser_get_content,
    browser_get_attribute,
    browser_run_js,
    browser_screenshot,
    browser_save_session,
    browser_load_session,
    browser_close,
)
from tools.automation_tools import AUTOMATION_TOOLS_SCHEMAS, AUTOMATION_EXECUTORS
from tools.memory_tools import MEMORY_SCHEMAS, MEMORY_EXECUTORS
from tools.plan_tool import PLAN_TASK_SCHEMAS, PLAN_TASK_EXECUTORS
from skills_hub import SKILLS_HUB_SCHEMAS, SKILLS_HUB_EXECUTORS

from tools.arxiv_tool import SCHEMA as ARXIV_SCHEMA, execute as arxiv_execute
from tools.pubmed_tool import SCHEMA as PUBMED_SCHEMA, execute as pubmed_execute
from tools.whatsapp_tool import SCHEMA as WHATSAPP_SCHEMA, execute as whatsapp_execute
from tools.youtube_tool import SEARCH_SCHEMA as YT_SEARCH_SCHEMA, TRANSCRIPT_SCHEMA as YT_TRANSCRIPT_SCHEMA
from tools.youtube_tool import search_youtube as yt_search_execute, get_youtube_transcript as yt_transcript_execute
from tools.discovery_sources import (
    WIKIDATA_SCHEMA, search_wikidata,
    WIKIPEDIA_SCHEMA, search_wikipedia,
    OPENALEX_SCHEMA, search_openalex,
    CROSSREF_SCHEMA, search_crossref,
    OPENCORPORATES_SCHEMA, search_opencorporates,
    SEC_EDGAR_SCHEMA, search_sec_edgar,
    GEOCODE_ADDRESS_SCHEMA, geocode_address,
    GDELT_NEWS_SCHEMA, search_gdelt_news,
)
from tools.gdocs_tool import SCHEMAS as GDOCS_SCHEMAS
from tools.gdocs_tool import (
    list_google_docs as _gdocs_list,
    read_google_doc as _gdocs_read,
    create_google_doc as _gdocs_create,
    append_google_doc as _gdocs_append,
    update_google_doc_title as _gdocs_update_title,
)
from tools.gsheets_tool import SCHEMAS as GSHEETS_SCHEMAS
from tools.gsheets_tool import (
    list_google_sheets as _gsheets_list,
    read_google_sheet as _gsheets_read,
    create_google_sheet as _gsheets_create,
    append_google_sheet as _gsheets_append,
    update_google_sheet_cell as _gsheets_update_cell,
)
from tools.clutter_tools import SCHEMAS as CLUTTER_SCHEMAS
from tools.clutter_tools import (
    analyze_directory as _clutter_analyze,
    suggest_organization as _clutter_suggest,
    apply_organization as _clutter_apply,
)
from tools.report_generator import COMPILE_REPORT_SCHEMA, execute as _report_execute
from tools.api_client import SCHEMAS as API_SCHEMAS
from tools.api_client import api_fetch, api_discover, api_list_domains, api_config_from_curl
from tools.vault import SCHEMAS as VAULT_SCHEMAS
from tools.vault import vault_save, vault_get, vault_list, vault_delete
from tools.connector_doordash import SCHEMAS as DOORDASH_SCHEMAS
from tools.connector_doordash import get_my_doordash_orders, get_my_doordash_cart, get_doordash_order_status
from tools.connector_uber import SCHEMAS as UBER_SCHEMAS
from tools.connector_uber import get_my_uber_trips, estimate_uber_ride
from tools.connector_ringback import SCHEMAS as RINGBACK_SCHEMAS
from tools.connector_ringback import call_my_phone
from tools.connector_sofascore import SCHEMAS as SOFASCORE_SCHEMAS
from tools.connector_sofascore import (
    search_teams, get_team, get_scheduled_events,
    get_live_scores, get_event, get_standings, get_tournaments,
)
from tools.local_vision import SCHEMAS as LOCAL_VISION_SCHEMAS
from tools.local_vision import local_vision_snap, local_vision_highlight
from tools.session_browser import SCHEMAS as SESSION_SCHEMAS
from tools.session_browser import session_login, session_check

LIST_ATTACHMENTS_SCHEMA = {
    "type": "function",
    "function": {
        "name": "list_pdf_attachments",
        "description": (
            "List recent emails from watched senders (jayveedz19@gmail.com, kalinovjim@gmail.com) "
            "that contain PDF attachments. Returns sender, subject, date, and attachment filenames."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "senders": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of sender email addresses to filter by",
                    "default": ["jayveedz19@gmail.com", "kalinovjim@gmail.com"],
                },
            },
            "required": [],
        },
    },
}

RUN_INBOX_MONITOR_SCHEMA = {
    "type": "function",
    "function": {
        "name": "run_inbox_monitor",
        "description": (
            "Run one full inbox monitor cycle: check Gmail for new PDF attachments from "
            "jayveedz19@gmail.com and kalinovjim@gmail.com, download each PDF, convert to Markdown, "
            "extract images, summarize with AI, send a Telegram notification, create Google Tasks "
            "for action items, and log everything to the Excel tracker. "
            "Use this to trigger the full automated pipeline in one step."
        ),
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
}

CONVERT_PDF_SCHEMA = {
    "type": "function",
    "function": {
        "name": "convert_pdf",
        "description": (
            "Convert a local PDF file to Markdown and extract any embedded images as PNG files. "
            "Saves .md and _imgN.png files alongside the PDF. Returns the markdown text."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "pdf_path": {
                    "type": "string",
                    "description": "Absolute path to the PDF file",
                },
                "output_dir": {
                    "type": "string",
                    "description": "Directory to save output files (defaults to same dir as PDF)",
                    "default": "",
                },
            },
            "required": ["pdf_path"],
        },
    },
}

DETECT_PDF_FORM_SCHEMA = {
    "type": "function",
    "function": {
        "name": "detect_pdf_form_fields",
        "description": (
            "Inspect a local PDF and return every interactive AcroForm field: "
            "name, type (Text/CheckBox/RadioButton/ListBox/ComboBox), current value, "
            "options (for list/combo), required flag. Use before fill_pdf_form. "
            "XFA dynamic forms are not supported."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Absolute path to the PDF file"},
            },
            "required": ["path"],
        },
    },
}

FILL_PDF_FORM_SCHEMA = {
    "type": "function",
    "function": {
        "name": "fill_pdf_form",
        "description": (
            "Fill AcroForm fields in a PDF and save to a new file. "
            "Pass field names/values from detect_pdf_form_fields. "
            "Original PDF is never modified — output defaults to {stem}_filled.pdf."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Absolute path to source PDF"},
                "fields": {
                    "type": "string",
                    "description": 'JSON object: {"FieldName": "value", ...}',
                },
                "output_path": {
                    "type": "string",
                    "description": "Output path (defaults to {stem}_filled.pdf alongside source)",
                    "default": "",
                },
            },
            "required": ["path", "fields"],
        },
    },
}

DETECT_FORM_SCHEMA = {
    "type": "function",
    "function": {
        "name": "detect_form_fields",
        "description": (
            "Detect all form fields in a PDF or DOCX file. Automatically detects file type. "
            "Returns JSON with field names, types, and metadata. Use before fill_form. "
            "Supports interactive PDF forms (AcroForm) and DOCX table-based forms."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "Absolute path to PDF or DOCX file"},
            },
            "required": ["file_path"],
        },
    },
}

FILL_FORM_SCHEMA = {
    "type": "function",
    "function": {
        "name": "fill_form",
        "description": (
            "Fill form fields in a PDF or DOCX and save to a new file. "
            "Automatically detects file type. Original file never modified — "
            "output defaults to {stem}_filled.{ext}."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "Absolute path to PDF or DOCX file"},
                "fields": {
                    "type": "string",
                    "description": 'JSON object: {"field_name": "value", ...}',
                },
                "output_path": {
                    "type": "string",
                    "description": "Output path (defaults to {stem}_filled.{ext})",
                    "default": "",
                },
            },
            "required": ["file_path", "fields"],
        },
    },
}

UPDATE_FORM_SCHEMA = {
    "type": "function",
    "function": {
        "name": "update_form",
        "description": (
            "Update specific fields in an already-filled form (PDF or DOCX). "
            "Preserves all other field values. Output defaults to {stem}_updated.{ext}."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "Absolute path to existing filled form"},
                "field_updates": {
                    "type": "string",
                    "description": 'JSON object: {"field_name": "new_value", ...}',
                },
                "output_path": {
                    "type": "string",
                    "description": "Output path (defaults to {stem}_updated.{ext})",
                    "default": "",
                },
            },
            "required": ["file_path", "field_updates"],
        },
    },
}

VISION_DETECT_FORM_SCHEMA = {
    "type": "function",
    "function": {
        "name": "vision_detect_form_fields",
        "description": (
            "Detect form fields in a PDF that has no interactive AcroForm fields "
            "(scanned or print-to-PDF forms). Uses pdfplumber heuristic analysis to identify "
            "underscore-based fill-in lines, checkboxes, and labeled input fields. "
            "Returns JSON with field label, type, page, and bounding box. "
            "Use before vision_fill_form_fields."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Absolute path to the PDF file"},
                "pages": {
                    "type": "string",
                    "description": "Page range like '1-5' or '3'. Defaults to all pages.",
                    "default": "",
                },
            },
            "required": ["path"],
        },
    },
}

VISION_FILL_FORM_SCHEMA = {
    "type": "function",
    "function": {
        "name": "vision_fill_form_fields",
        "description": (
            "Fill fields in a PDF using coordinates from vision_detect_form_fields. "
            "Overlays text at detected bounding boxes. Handles text inputs, checkboxes, "
            "radio buttons, and signature lines. "
            "Use after vision_detect_form_fields to fill the detected fields."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Absolute path to source PDF"},
                "fields_json": {
                    "type": "string",
                    "description": (
                        'JSON from vision_detect_form_fields: {"fields": [...], "total": N} '
                        "or the raw fields array"
                    ),
                },
                "values_json": {
                    "type": "string",
                    "description": 'JSON object mapping field labels to values: {"Full Name": "John Doe", ...}',
                },
                "output_path": {
                    "type": "string",
                    "description": "Output path (defaults to {stem}_filled_vision.pdf)",
                    "default": "",
                },
            },
            "required": ["path", "fields_json", "values_json"],
        },
    },
}

VISION_UPDATE_FORM_SCHEMA = {
    "type": "function",
    "function": {
        "name": "vision_update_form_fields",
        "description": (
            "Update specific fields in an already-filled vision-processed form. "
            "Reuses the original field coordinates. Output defaults to {stem}_updated_vision.pdf."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path to existing filled form PDF"},
                "fields_json": {
                    "type": "string",
                    "description": "Original field detection JSON (to get bbox coordinates)",
                },
                "updates_json": {
                    "type": "string",
                    "description": 'JSON object: {"Field Label": "new value", ...}',
                },
                "output_path": {
                    "type": "string",
                    "description": "Output path (defaults to {stem}_updated_vision.pdf)",
                    "default": "",
                },
            },
            "required": ["path", "fields_json", "updates_json"],
        },
    },
}

WEB_SEARCH_SCHEMA = {
    "type": "function",
    "function": {
        "name": "web_search",
        "description": "Search the web for current, real-time information. Use when the question needs up-to-date facts.",
        "parameters": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "The search query"}},
            "required": ["query"],
        },
    },
}

WORKFLOW_JOB_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "create_workflow",
            "description": (
                "Schedule an already-registered internal handler pipeline (e.g. 'email.daily_summary', "
                "'golden.queries') to run on a cron or interval. Low-level tool for known pipeline IDs "
                "only — for a new user-requested automation (watch emails, telegram/webhook/notification "
                "action, daily briefing), use create_automation instead, which builds the handler config "
                "for you and validates action_type. Automatically blocks creating a duplicate: if an "
                "active instance of the same automation_id already exists with no params or the same "
                "params, this returns an Error naming the existing instance instead of creating a second "
                "one — read the returned string, it tells you what already exists and whether force=true "
                "is needed."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "workflow_id": {"type": "string", "description": "Unique identifier for this workflow instance (e.g. 'my_daily_task')."},
                    "automation_id": {"type": "string", "description": "Registered pipeline/handler ID this workflow dispatches to (e.g. 'email.daily_summary', 'health_check') — NOT the same as the workflow_id/instance UUID used by pause_automation/resume_automation/delete_automation."},
                    "task_name": {"type": "string", "description": "Human-readable task description."},
                    "schedule_type": {"type": "string", "enum": ["cron", "interval"], "description": "The scheduling method."},
                    "schedule_expr": {"type": "string", "description": "For 'cron', standard 5-field cron expression (e.g. '*/5 * * * *'). For 'interval', number of seconds (e.g. '300')."},
                    "params": {"type": "object", "description": "Optional parameters to pass to the workflow executor."},
                    "timezone": {"type": "string", "description": "Optional timezone name for cron schedules (default: 'UTC')."},
                    "status": {"type": "string", "enum": ["active", "paused"], "description": "Initial status (default: 'active')."},
                    "force": {"type": "boolean", "description": "Set true to create another active instance of the same automation_id even though one already exists with matching/no params (default: false, which blocks the duplicate)."},
                },
                "required": ["workflow_id", "automation_id", "task_name", "schedule_type", "schedule_expr"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_available_pipelines",
            "description": (
                "List the automation_id values that are legal to pass to create_workflow — "
                "already-registered internal handler pipelines (e.g. 'email.watch_sender', "
                "'inbox.monitor_attachments'), each with a one-line description of what it does, "
                "what config keys it expects, and how many active instances already exist (e.g. "
                "'[1 active instance(s), next: 2026-07-12T07:00:00]' or '[0 active instances]'). "
                "Call this BEFORE create_workflow if you're not certain a suitable pipeline already "
                "exists — reusing one with new config (new senders, new schedule) is usually correct "
                "instead of building a new automation from scratch. If a pipeline already shows an "
                "active instance and the new request wants the same thing, tell the user it's already "
                "scheduled instead of creating a duplicate."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_workflows",
            "description": "List all active and paused workflow instances in the system.",
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "description": "Maximum number of workflow instances to return (default: 50)."},
                },
                "required": [],
            },
        },
    },
]

from tools.x_bird import (
    SCHEMA_SEARCH as X_BIRD_SEARCH_SCHEMA,
    SCHEMA_READ as X_BIRD_READ_SCHEMA,
    SCHEMA_USER as X_BIRD_USER_SCHEMA,
    x_search as x_bird_search_execute,
    x_read_tweet as x_bird_read_execute,
    x_user_tweets as x_bird_user_execute,
)

# All available tool schemas (passed to ollama tools=[...])
