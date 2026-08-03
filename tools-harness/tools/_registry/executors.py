"""Registry: EXECUTORS dict — tool name -> callable. See tools/registry.py."""

from __future__ import annotations

from tools._registry import imports as _ri
globals().update({k: v for k, v in vars(_ri).items() if not k.startswith("__")})

_HOME = str(__import__("pathlib").Path.home())

EXECUTORS = {
    "ask_local_agent": lambda args: __import__("tools.orchestrator_tools", fromlist=["exec_ask_local_agent"]).exec_ask_local_agent(args),
    "ask_read_file": lambda args: __import__("tools.orchestrator_tools", fromlist=["exec_ask_read_file"]).exec_ask_read_file(args),
    "ask_fetch_url": lambda args: __import__("tools.orchestrator_tools", fromlist=["exec_ask_fetch_url"]).exec_ask_fetch_url(args),
    "ask_write_file": lambda args: __import__("tools.orchestrator_tools", fromlist=["exec_ask_write_file"]).exec_ask_write_file(args),
    "ask_delete_file": lambda args: __import__("tools.orchestrator_tools", fromlist=["exec_ask_delete_file"]).exec_ask_delete_file(args),
    "ask_rename_file": lambda args: __import__("tools.orchestrator_tools", fromlist=["exec_ask_rename_file"]).exec_ask_rename_file(args),
    "ask_run_command": lambda args: __import__("tools.orchestrator_tools", fromlist=["exec_ask_run_command"]).exec_ask_run_command(args),
    "ask_web_search": lambda args: __import__("tools.orchestrator_tools", fromlist=["exec_ask_web_search"]).exec_ask_web_search(args),
    "ask_browser_agent": lambda args: __import__("tools.orchestrator_tools", fromlist=["exec_ask_browser_agent"]).exec_ask_browser_agent(args),
    "ask_macos_native": lambda args: __import__("tools.orchestrator_tools", fromlist=["exec_ask_macos_native"]).exec_ask_macos_native(args),
    "ask_email_agent": lambda args: __import__("tools.orchestrator_tools", fromlist=["exec_ask_email_agent"]).exec_ask_email_agent(args),
    "ask_tasks_agent": lambda args: __import__("tools.orchestrator_tools", fromlist=["exec_ask_tasks_agent"]).exec_ask_tasks_agent(args),
    "ask_calendar_agent": lambda args: __import__("tools.orchestrator_tools", fromlist=["exec_ask_calendar_agent"]).exec_ask_calendar_agent(args),
    "ask_docs_agent": lambda args: __import__("tools.orchestrator_tools", fromlist=["exec_ask_docs_agent"]).exec_ask_docs_agent(args),
    "ask_sheets_agent": lambda args: __import__("tools.orchestrator_tools", fromlist=["exec_ask_sheets_agent"]).exec_ask_sheets_agent(args),
    "ask_deep_research": lambda args: __import__("tools.orchestrator_tools", fromlist=["exec_ask_deep_research"]).exec_ask_deep_research(args),
    "ask_automation_agent": lambda args: __import__("tools.orchestrator_tools", fromlist=["exec_ask_automation_agent"]).exec_ask_automation_agent(args),
    "ask_dev_agent": lambda args: __import__("tools.orchestrator_tools", fromlist=["exec_ask_dev_agent"]).exec_ask_dev_agent(args),
    "ask_messaging_agent": lambda args: __import__("tools.orchestrator_tools", fromlist=["exec_ask_messaging_agent"]).exec_ask_messaging_agent(args),
    "ask_research_agent": lambda args: __import__("tools.orchestrator_tools", fromlist=["exec_ask_research_agent"]).exec_ask_research_agent(args),
    "ask_vision_agent": lambda args: __import__("tools.orchestrator_tools", fromlist=["exec_ask_vision_agent"]).exec_ask_vision_agent(args),
    "ask_utility_agent": lambda args: __import__("tools.orchestrator_tools", fromlist=["exec_ask_utility_agent"]).exec_ask_utility_agent(args),
    "ask_transport_agent": lambda args: __import__("tools.orchestrator_tools", fromlist=["exec_ask_transport_agent"]).exec_ask_transport_agent(args),
    "ask_youtube_agent": lambda args: __import__("tools.orchestrator_tools", fromlist=["exec_ask_youtube_agent"]).exec_ask_youtube_agent(args),
    "ask_x_agent": lambda args: __import__("tools.orchestrator_tools", fromlist=["exec_ask_x_agent"]).exec_ask_x_agent(args),
    "ask_reddit_agent": lambda args: __import__("tools.orchestrator_tools", fromlist=["exec_ask_reddit_agent"]).exec_ask_reddit_agent(args),
    "ask_science_scout_agent": lambda args: __import__("tools.orchestrator_tools", fromlist=["exec_ask_science_scout_agent"]).exec_ask_science_scout_agent(args),
    "ask_send_message": lambda args: __import__("tools.orchestrator_tools", fromlist=["exec_ask_send_message"]).exec_ask_send_message(args),
    "make_phone_call": lambda args: __import__("tools.make_phone_call", fromlist=["exec_make_phone_call"]).exec_make_phone_call(args),
    "query_recent_traces": lambda args: __import__("tools.orchestrator_tools", fromlist=["exec_query_recent_traces"]).exec_query_recent_traces(args),

    "create_workflow": lambda args: __import__("tools.workflow_job_tools", fromlist=["create_workflow"]).create_workflow(args),
    "list_available_pipelines": lambda args: __import__("tools.workflow_job_tools", fromlist=["list_available_pipelines"]).list_available_pipelines(args),
    "list_workflows": lambda args: __import__("tools.workflow_job_tools", fromlist=["list_workflows"]).list_workflows(args),

    "local_search": lambda args: local_execute(
        query=args["query"],
        sources=args.get("sources"),
        top_k=args.get("top_k", 4),
    ),
    "web_search": lambda args: websearch.search(query=args["query"]),
    "deep_research": lambda args: deep_research_execute(
        query=args["query"],
        depth=args.get("depth", 2),
        breadth=args.get("breadth", 3),
    ),
    "searxng_search": lambda args: _to_model_str(
        _searxng_execute(
            query=args["query"],
            categories=args.get("categories", "general"),
            time_range=args.get("time_range", ""),
        )
    ),
    "scrapling_fetch": lambda args: _scrapling_fetch(
        url=args["url"],
        timeout=args.get("timeout", 30),
        headless=args.get("headless", True),
        proxy=args.get("proxy"),
    ),
    "scrapling_stealthy_fetch": lambda args: _scrapling_stealthy_fetch(
        url=args["url"],
        timeout=args.get("timeout", 60),
        solve_cloudflare=args.get("solve_cloudflare", False),
        proxy=args.get("proxy"),
    ),
    "scrapling_extract": lambda args: _scrapling_extract(
        html=args["html"],
        selector=args["selector"],
        domain=args.get("domain", ""),
        extract=args.get("extract", "text"),
        mode=args.get("mode", "auto_save"),
        similarity=args.get("similarity", 40),
    ),
    "scrapling_fetch_and_extract": lambda args: _scrapling_fetch_and_extract(
        url=args["url"],
        selector=args["selector"],
        extract=args.get("extract", "text"),
        stealth=args.get("stealth", False),
    ),
    "transcribe_audio": lambda args: audio_execute(
        file_path=args["file_path"],
        language=args.get("language", ""),
    ),
    "convert_audio": lambda args: _convert_audio(
        input_path=args["input_path"],
        output_format=args["output_format"],
        output_path=args.get("output_path", ""),
        bitrate=args.get("bitrate", "192k"),
    ),
    "ocr_image": lambda args: ocr_execute(
        image_path=args["image_path"],
        lang=args.get("lang", "en"),
    ),
    "surya_ocr": lambda args: surya_ocr_execute(
        image_path=args["image_path"],
        lang=args.get("lang", "en"),
        detect_tables=args.get("detect_tables", False),
    ),
    "take_screenshot": lambda args: peekaboo_execute(
        mode=args.get("mode", "screen"),
        app=args.get("app", ""),
    ),
    "list_windows": lambda args: peekaboo_list_windows(
        app=args.get("app", ""),
    ),
    "slack_search": lambda args: slack_search_execute(
        query=args["query"],
        limit=args.get("limit", 10),
        channel=args.get("channel", ""),
    ),
    "slack_status": lambda args: slack_status_execute(),
    "imessage_search": lambda args: imessage_search_execute(
        query=args.get("query", ""),
        limit=args.get("limit", 15),
        contact=args.get("contact", ""),
        days=args.get("days", 0),
    ),
    "imessage_status": lambda args: imessage_status_execute(),
    "imessage_send": lambda args: imessage_send_execute(
        to=args["to"],
        message=args["message"],
        service=args.get("service", "iMessage"),
    ),
    "contacts_resolve": lambda args: contacts_resolve_execute(
        query=args["query"],
        resolve_handles=args.get("resolve_handles", True),
    ),
    "set_opencode_model": lambda args: set_opencode_model_execute(model=args["model"]),
    "parse_document": lambda args: docling_execute(
        path=args["path"],
        max_chars=args.get("max_chars", 12000),
    ),
    "archive_grep": lambda args: rga_execute(
        pattern=args["pattern"],
        path=args.get("path", "."),
        max_count=args.get("max_count", 5),
        case_insensitive=args.get("case_insensitive", True),
    ),
    "parse_email_file": lambda args: email_parse_execute(
        path=args["path"],
        max_chars=args.get("max_chars", 8000),
    ),
    "whatsapp_search": lambda args: whatsapp_search_execute(
        query=args["query"],
        limit=args.get("limit", 10),
        contact=args.get("contact", ""),
        days=args.get("days", 0),
    ),
    "whatsapp_status": lambda args: whatsapp_status_execute(),
    "spotlight_search": lambda args: spotlight_execute(
        query=args["query"],
        directory=args.get("directory", ""),
        limit=args.get("limit", 25),
    ),
    "find_recent": lambda args: spotlight_find_recent(
        kind=args.get("kind", "any"),
        days=args.get("days", 7),
        query=args.get("query", ""),
        directory=args.get("directory", ""),
        limit=args.get("limit", 25),
    ),
    "system_status": lambda args: system_status_execute(),
    "clipboard_read": lambda args: mn_clipboard_read(),
    "clipboard_write": lambda args: mn_clipboard_write(text=args["text"]),
    "list_safari_tabs": lambda args: mn_list_safari_tabs(),
    "list_notes": lambda args: mn_list_notes(
        query=args["query"],
        limit=args.get("limit", 10),
    ),
    "notes_create": lambda args: mn_notes_create(
        title=args["title"],
        body=args.get("body", ""),
        folder=args.get("folder", ""),
        account=args.get("account", "iCloud"),
    ),
    "applescript_run": lambda args: mn_applescript_run(script=args["script"]),
    "list_reminders": lambda args: rem_list(
        list_name=args.get("list_name", ""),
        show_completed=args.get("show_completed", False),
    ),
    "create_reminder": lambda args: rem_create(
        title=args["title"],
        due=args.get("due", ""),
        list_name=args.get("list_name", ""),
        notes=args.get("notes", ""),
    ),
    "list_mac_calendar_events": lambda args: cal_list(
        days=args.get("days", 7),
    ),
    # Filesystem
    "read_file": lambda args: read_file(
        path=args["path"],
        offset=args.get("offset", 1),
        limit=args.get("limit", 200),
    ),
    "grep_files": lambda args: grep_files(
        pattern=args["pattern"],
        path=args.get("path", _HOME),
        glob=args.get("glob", "*"),
        max_results=args.get("max_results", 30),
        ignore_case=args.get("ignore_case", False),
    ),
    "find_files": lambda args: find_files(
        pattern=args["pattern"],
        path=args.get("path", _HOME),
        max_results=args.get("max_results", 20),
    ),
    "list_directory": lambda args: list_directory(
        path=args.get("path", _HOME),
        show_hidden=args.get("show_hidden", False),
        limits=args.get("limits", 50),
    ),
    "file_tree": lambda args: file_tree(
        path=args["path"],
        max_depth=args.get("max_depth", 3),
        show_hidden=args.get("show_hidden", False),
    ),
    "read_many_files": lambda args: read_many_files(
        paths=args["paths"],
        limit_per_file=args.get("limit_per_file", 100),
    ),
    "file_info": lambda args: file_info(path=args["path"]),
    "find_largest": lambda args: find_largest(
        path=args.get("path", _HOME),
        n=args.get("n", 10),
        show_hidden=args.get("show_hidden", False),
    ),
    # Fast search tools
    "glob": lambda args: glob_execute(
        pattern=args["pattern"],
        path=args.get("path", "."),
    ),
    "fd_find": lambda args: fd_execute(
        pattern=args["pattern"],
        path=args.get("path", "."),
        extension=args.get("extension", ""),
        max_results=args.get("max_results", 30),
        no_ignore=args.get("no_ignore", False),
    ),
    "ripgrep": lambda args: rg_execute(
        pattern=args["pattern"],
        path=args.get("path", "."),
        file_type=args.get("file_type", ""),
        max_results=args.get("max_results", 50),
        ignore_case=args.get("ignore_case", True),
        literal=args.get("literal", False),
        no_ignore=args.get("no_ignore", False),
    ),
    "rename_file": lambda args: rename_file(src=args["src"], dest=args["dest"]),
    "delete_file": lambda args: delete_file(
        path=args["path"],
        recursive=args.get("recursive", False),
    ),
    "create_directory": lambda args: create_directory(path=args["path"]),
    "copy_file": lambda args: copy_file(
        src=args["src"],
        dest=args["dest"],
        recursive=args.get("recursive", False),
    ),
    # Document creation
    "markdown_to_docx": lambda args: markdown_to_docx_executor(args),
    "markdown_to_pdf": lambda args: markdown_to_pdf_executor(args),
    "create_pdf": lambda args: create_pdf_executor(args),
    "json_to_docx": lambda args: json_to_docx_executor(args),
    "data_to_xlsx": lambda args: data_to_xlsx_executor(args),
    "markdown_to_pptx": lambda args: markdown_to_pptx_executor(args),
    "render_diagram": lambda args: render_diagram_executor(args),
    "edit_image": lambda args: edit_image_executor(args),
    "github_search": lambda args: github_search_executor(args),
    "github_list_prs": lambda args: github_list_prs_executor(args),
    "github_create_issue": lambda args: github_create_issue_executor(args),
    "github_create_pr": lambda args: github_create_pr_executor(args),
    "text_to_file": lambda args: text_to_file_executor(args),
    "html_to_file": lambda args: html_to_file_executor(args),
    # Structured parsing
    "read_document": lambda args: read_document(
        path=args["path"],
        pages=args.get("pages", "1-10"),
        max_chars=args.get("max_chars", 12000),
        lang=args.get("lang", "en"),
    ),
    "parse_file": lambda args: parse_file(
        path=args["path"],
        max_rows=args.get("max_rows", 50),
    ),
    "read_pdf": lambda args: read_pdf(
        path=args["path"],
        pages=args.get("pages", "1-10"),
    ),
    "parse_code": lambda args: parse_code(
        path=args["path"],
        include_bodies=args.get("include_bodies", False),
    ),
    # Git
    "git_status": lambda args: git_status(path=args.get("path", _HOME)),
    "git_diff": lambda args: git_diff(
        path=args.get("path", _HOME),
        staged=args.get("staged", False),
        file=args.get("file", ""),
    ),
    "git_log": lambda args: git_log(
        path=args.get("path", _HOME),
        n=args.get("n", 15),
    ),
    "git_add": lambda args: git_add(
        path=args.get("path", _HOME),
        files=args.get("files", "."),
    ),
    "git_commit": lambda args: git_commit(
        message=args["message"],
        path=args.get("path", _HOME),
    ),
    "git_checkout": lambda args: git_checkout(
        branch=args["branch"],
        path=args.get("path", _HOME),
        create=args.get("create", False),
    ),
    "git_new_worktree": lambda args: git_new_worktree(
        repo_path=args["repo_path"],
        worktree_path=args["worktree_path"],
        branch=args["branch"],
    ),
    # Python REPL
    "run_python": lambda args: run_python(
        code=args["code"],
        timeout=args.get("timeout", 30),
    ),
    "reset_kernel": lambda args: reset_kernel(),
    "list_kernel_vars": lambda args: list_kernel_vars(),
    # Docs
    "get_library_docs": lambda args: context7_execute(
        library=args["library"],
        topic=args.get("topic", ""),
    ),
    # Shell / agent
    "bash_exec": lambda args: bash_exec(
        command=args["command"],
        cwd=args.get("cwd", _HOME),
        timeout=args.get("timeout", 60),
    ),
    "write_file": lambda args: write_file(
        path=args["path"],
        content=args["content"],
    ),
    "append_file": lambda args: append_file(
        path=args["path"],
        content=args["content"],
    ),
    "download_url": lambda args: download_url(
        url=args["url"],
        dest_path=args["dest_path"],
    ),
    "edit_file": lambda args: edit_file(
        path=args["path"],
        old_str=args["old_str"],
        new_str=args["new_str"],
    ),
    "edit_file_fuzzy": lambda args: edit_file_fuzzy(
        path=args["path"],
        old_str=args["old_str"],
        new_str=args["new_str"],
    ),
    "undo_last_edit": lambda args: undo_last_edit(
        path=args["path"],
    ),
    # Time
    "get_current_time": lambda args: get_current_time(
        timezone_name=args.get("timezone", ""),
    ),
    "bus_eta": lambda args: bus_eta(
        origin=args["origin"],
        destination=args.get("destination", ""),
        depart_in=args.get("depart_in", 0),
    ),
    # Telegram
    "send_telegram": lambda args: send_telegram(message=args["message"]),
    # Reminders
    "set_reminder": lambda args: set_reminder(
        text=args["text"],
        iso_datetime=args["iso_datetime"],
    ),
    # opencode
    "ask_opencode": lambda args: exec_ask_opencode(args),
    # Chinese web search (agent-reach)
    "search_chinese_web": lambda args: search_chinese_web(args),
    # Google auth
    "refresh_google_token": lambda args: refresh_google_token(),
    # Gmail
    "get_latest_email": lambda args: get_latest_email(query=args.get("query", "")),
    "run_inbox_monitor": lambda args: _run_inbox_monitor_executor(),
    "download_gmail_attachment": lambda args: download_attachment(
        message_id=args["message_id"],
        attachment_id=args["attachment_id"],
        filename=args["filename"],
        dest_dir=args.get("dest_dir", ""),
    ),
    "list_gmail_attachments": lambda args: __import__("json").dumps(
        list_all_attachments(
            seen_ids=set(),
            max_results=args.get("max_results", 20),
        ),
        default=str,
    ),
    "list_pdf_attachments": lambda args: __import__("json").dumps(
        list_emails_with_attachments(
            senders=args.get("senders") or _ri.watched_senders(),
            seen_ids=set(),
        ),
        default=str,
    ),
    "convert_pdf": lambda args: _convert_pdf_executor(args),
    "detect_pdf_form_fields": lambda args: detect_pdf_form_fields(path=args["path"]),
    "fill_pdf_form": lambda args: fill_pdf_form(
        path=args["path"],
        fields=args["fields"],
        output_path=args.get("output_path") or None,
    ),
    "detect_form_fields": lambda args: detect_form_fields(
        file_path=args.get("file_path") or args.get("path") or args.get("file") or ""
    ),
    "fill_form": lambda args: fill_form(
        file_path=args["file_path"],
        fields=args["fields"],
        output_path=args.get("output_path") or None,
    ),
    "update_form": lambda args: update_form(
        file_path=args["file_path"],
        field_updates=args["field_updates"],
        output_path=args.get("output_path") or None,
    ),
    "vision_detect_form_fields": lambda args: vision_detect_form_fields(
        path=args["path"],
        pages=args.get("pages") or None,
    ),
    "vision_fill_form_fields": lambda args: vision_fill_form_fields(
        path=args["path"],
        fields_json=args["fields_json"],
        values_json=args["values_json"],
        output_path=args.get("output_path") or None,
    ),
    "vision_update_form_fields": lambda args: vision_update_form_fields(
        path=args["path"],
        fields_json=args["fields_json"],
        updates_json=args["updates_json"],
        output_path=args.get("output_path") or None,
    ),
    "detect_flat_pdf_fields": lambda args: _detect_flat_pdf_fields(
        path=args["path"],
        pages=args.get("pages") or None,
    ),
    "fill_flat_pdf": lambda args: _fill_flat_pdf(
        path=args["path"],
        fields_json=args["fields_json"],
        values_json=args["values_json"],
        output_path=args.get("output_path") or None,
    ),
    "list_emails": lambda args: list_emails(
        query=args.get("query", ""),
        max_results=args.get("max_results", 10),
    ),
    "read_email": lambda args: read_email(message_id=args["message_id"]),
    "generate_local_image": lambda args: generate_local_image(
        prompt=args["prompt"],
        model=args.get("model", "x/z-image-turbo"),
        output_path=args.get("output_path", ""),
        width=args.get("width", 512),
        height=args.get("height", 512),
        steps=args.get("steps", 20),
    ),
    "send_email": lambda args: send_email(
        to=args["to"],
        subject=args["subject"],
        body=args["body"],
        html_body=args.get("html_body", ""),
        attachment_paths=args.get("attachment_paths"),
    ),
    # Google Calendar
    "list_calendar_events": lambda args: list_calendar_events(
        days_ahead=args.get("days_ahead", 14),
        max_results=args.get("max_results", 10),
        query=args.get("query", ""),
    ),
    "create_calendar_event": lambda args: create_calendar_event(
        title=args["title"],
        start_iso=args["start_iso"],
        end_iso=args.get("end_iso", ""),
        description=args.get("description", ""),
        location=args.get("location", ""),
    ),
    "delete_calendar_event": lambda args: delete_calendar_event(
        event_id=args["event_id"],
    ),
    # Google Tasks
    "list_tasks": lambda args: list_tasks(
        task_list=args.get("task_list", "My Tasks"),
        include_completed=args.get("include_completed", False),
        max_results=args.get("max_results", 20),
    ),
    "create_task": lambda args: create_task(
        title=args["title"],
        notes=args.get("notes", ""),
        due_iso=args.get("due_iso", ""),
        task_list=args.get("task_list", "My Tasks"),
    ),
    "complete_task": lambda args: complete_task(
        task_id=args["task_id"],
        task_list=args.get("task_list", "My Tasks"),
    ),
    "delete_task": lambda args: delete_task(
        task_id=args["task_id"],
        task_list=args.get("task_list", "My Tasks"),
    ),
    # Browser automation
    "browser_navigate": lambda args: browser_navigate(
        url=args["url"],
        timeout=args.get("timeout", 20000),
    ),
    "browser_snapshot": lambda args: browser_snapshot(
        selector=args.get("selector", ""),
        max_chars=args.get("max_chars", 6000),
    ),
    "browser_get_url": lambda args: browser_get_url(),
    "browser_click": lambda args: browser_click(
        selector=args["selector"],
        timeout=args.get("timeout", 5000),
        force=args.get("force", False),
    ),
    "browser_type": lambda args: browser_type(
        selector=args["selector"],
        text=args["text"],
        timeout=args.get("timeout", 5000),
    ),
    "browser_check": lambda args: browser_check(
        selector=args["selector"],
        check=args.get("check", True),
    ),
    "browser_select": lambda args: browser_select(
        selector=args["selector"],
        value=args["value"],
    ),
    "browser_wait": lambda args: browser_wait(
        selector=args.get("selector", ""),
        timeout=args.get("timeout", 10000),
    ),
    "browser_get_content": lambda args: browser_get_content(
        selector=args.get("selector", ""),
        max_chars=args.get("max_chars", 4000),
    ),
    "browser_get_attribute": lambda args: browser_get_attribute(
        selector=args["selector"],
        attribute=args["attribute"],
    ),
    "browser_run_js": lambda args: browser_run_js(script=args["script"]),
    "browser_screenshot": lambda args: browser_screenshot(
        path=args.get("path", ""),
        full_page=args.get("full_page", False),
    ),
    "browser_close": lambda args: browser_close(),
    "browser_save_session": lambda args: browser_save_session(
        name=args.get("name", "default"),
    ),
    "browser_load_session": lambda args: browser_load_session(
        name=args.get("name", "default"),
    ),
    # Semantic search
    "index_directory": lambda args: index_directory(
        path=args["path"],
        glob=args.get("glob", "*"),
    ),
    "semantic_file_search": lambda args: semantic_file_search(
        query=args["query"],
        path_filter=args.get("path_filter", ""),
        top_k=args.get("top_k", 5),
    ),
    "list_email_attachments": lambda args: list_email_attachments(
        month=args.get("month", ""),
    ),
    # Automation management
    **AUTOMATION_EXECUTORS,
    # Persistent cross-session memory
    **MEMORY_EXECUTORS,
    # Mid-task plan/todo scratchpad
    **PLAN_TASK_EXECUTORS,
    # Skills hub
    **SKILLS_HUB_EXECUTORS,
    # Research
    "search_arxiv": lambda args: arxiv_execute(
        query=args["query"],
        max_results=args.get("max_results", 5),
    ),
    "search_pubmed": lambda args: pubmed_execute(
        query=args["query"],
        max_results=args.get("max_results", 5),
    ),
    # Discovery Sources
    "search_wikidata": lambda args: search_wikidata(
        query=args["query"],
        limit=args.get("limit", 5),
    ),
    "search_wikipedia": lambda args: search_wikipedia(
        query=args["query"],
        limit=args.get("limit", 3),
        sentences=args.get("sentences", 3),
    ),
    "search_openalex": lambda args: search_openalex(
        query=args["query"],
        limit=args.get("limit", 5),
    ),
    "search_crossref": lambda args: search_crossref(
        query=args["query"],
        limit=args.get("limit", 5),
    ),
    "search_opencorporates": lambda args: search_opencorporates(
        query=args["query"],
    ),
    "search_sec_edgar": lambda args: search_sec_edgar(
        query=args["query"],
        limit=args.get("limit", 5),
    ),
    "geocode_address": lambda args: geocode_address(
        address=args["address"],
        limit=args.get("limit", 3),
    ),
    "search_gdelt_news": lambda args: search_gdelt_news(
        query=args["query"],
        limit=args.get("limit", 5),
    ),
    # YouTube
    "search_youtube": lambda args: yt_search_execute(
        query=args["query"],
        max_results=args.get("max_results", 5),
    ),
    "get_youtube_transcript": lambda args: yt_transcript_execute(
        video_url=args["video_url"],
        include_timestamps=args.get("include_timestamps", False),
    ),
    # Video processing
    "download_video": lambda args: ytdlp_execute(
        url=args["url"],
        output_dir=args.get("output_dir", ""),
        format=args.get("format", "best"),
        audio_only=args.get("audio_only", False),
    ),
    "process_video": lambda args: ffmpeg_execute(
        input_path=args["input_path"],
        operation=args["operation"],
        output_path=args.get("output_path", ""),
        start=args.get("start", ""),
        end=args.get("end", ""),
        width=args.get("width", 0),
        height=args.get("height", 0),
        format=args.get("format", "mp4"),
    ),
    # WhatsApp
    "send_whatsapp": lambda args: whatsapp_execute(
        to=args["to"],
        message=args["message"],
    ),
    # Google Docs
    "list_google_docs": lambda args: _gdocs_list(args),
    "read_google_doc": lambda args: _gdocs_read(args),
    "create_google_doc": lambda args: _gdocs_create(args),
    "append_google_doc": lambda args: _gdocs_append(args),
    "update_google_doc_title": lambda args: _gdocs_update_title(args),
    # Google Sheets
    "list_google_sheets": lambda args: _gsheets_list(args),
    "read_google_sheet": lambda args: _gsheets_read(args),
    "create_google_sheet": lambda args: _gsheets_create(args),
    "append_google_sheet": lambda args: _gsheets_append(args),
    "update_google_sheet_cell": lambda args: _gsheets_update_cell(args),
    # Clutter fixer
    "analyze_directory": lambda args: _clutter_analyze(args),
    "suggest_organization": lambda args: _clutter_suggest(args),
    "apply_organization": lambda args: _clutter_apply(args),
    # Research report
    "compile_research_report": lambda args: _report_execute(args),
    "api_fetch": lambda args: api_fetch(domain=args["domain"], endpoint=args["endpoint"], params=args.get("params", {})),
    "api_discover": lambda args: api_discover(domain=args["domain"]),
    "api_list_domains": lambda args: api_list_domains(),
    "api_config_from_curl": lambda args: api_config_from_curl(domain=args["domain"], curl_command=args["curl_command"]),
    "vault_save": lambda args: vault_save(service=args["service"], data=args["data"]),
    "vault_get": lambda args: vault_get(service=args["service"]),
    "vault_list": lambda args: vault_list(),
    "vault_delete": lambda args: vault_delete(service=args["service"]),
    "get_my_doordash_orders": lambda args: get_my_doordash_orders(),
    "get_my_doordash_cart": lambda args: get_my_doordash_cart(),
    "get_doordash_order_status": lambda args: get_doordash_order_status(order_id=args.get("order_id", "")),
    "get_my_uber_trips": lambda args: get_my_uber_trips(),
    "estimate_uber_ride": lambda args: estimate_uber_ride(
        destination=args.get("destination", "3 Manning Terrace, Everett MA"), pickup=args.get("pickup", "")
    ),
    "call_my_phone": lambda args: call_my_phone(message=args.get("message", "")),
    "sofascore_search_teams": lambda args: search_teams(query=args["query"], sport=args.get("sport", "football")),
    "sofascore_get_team": lambda args: get_team(team_id=args["team_id"]),
    "sofascore_scheduled_events": lambda args: get_scheduled_events(sport=args.get("sport", "football"), date_str=args.get("date")),
    "sofascore_live_scores": lambda args: get_live_scores(sport=args.get("sport", "football")),
    "sofascore_get_event": lambda args: get_event(event_id=args["event_id"]),
    "sofascore_get_standings": lambda args: get_standings(tournament_id=args["tournament_id"], season_id=args.get("season_id")),
    "sofascore_get_tournaments": lambda args: get_tournaments(sport=args.get("sport", "football")),
    "local_vision_snap": lambda args: local_vision_snap(),
    "local_vision_highlight": lambda args: local_vision_highlight(),
    "session_login": lambda args: session_login(
        service=args["service"],
        timeout_s=args.get("timeout_s", 120),
    ),
    "session_check": lambda args: session_check(
        service=args["service"],
    ),
    "x_search": lambda args: x_bird_search_execute(
        query=args["query"], count=args.get("count", 10)
    ),
    "x_read_tweet": lambda args: x_bird_read_execute(tweet_id=args["tweet_id"]),
    "x_user_tweets": lambda args: x_bird_user_execute(
        handle=args["handle"], count=args.get("count", 10)
    ),
}

