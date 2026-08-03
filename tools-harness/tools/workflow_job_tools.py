from store import workflow_store
from tools.automation_tools import _pipelines_checked_this_turn

def create_workflow(args: dict) -> str:
    """Create a new scheduled workflow instance."""
    if not _pipelines_checked_this_turn("list_available_pipelines"):
        return (
            "[guidance] Call list_available_pipelines first — check whether an existing builtin "
            "pipeline's own description already matches this request (name, content, schedule) "
            "before creating a new workflow instance. If none match, call create_workflow again."
        )
    workflow_id = args["workflow_id"]
    automation_id = args["automation_id"]
    task_name = args["task_name"]
    schedule_type = args["schedule_type"]
    schedule_expr = args["schedule_expr"]
    params = args.get("params") or {}
    timezone_name = args.get("timezone", "UTC")
    status = args.get("status", "active")
    force = bool(args.get("force", False))

    existing = workflow_store.list_workflow_instances(automation_id=automation_id, status="active")
    fyi = ""
    if existing and not force:
        conflict = next((i for i in existing if not params or params == (i.get("config") or {})), None)
        if conflict:
            return (
                f"Error: an active workflow already uses pipeline '{automation_id}' — "
                f"'{conflict.get('task_name')}' (id={conflict.get('id')}, "
                f"next_run={conflict.get('next_run_at')}). Pass force=true to create "
                f"another instance anyway, or use update_automation/pause_automation on "
                f"the existing one instead of duplicating it."
            )
    if existing:
        others = ", ".join(f"'{i.get('task_name')}' (id={i.get('id')})" for i in existing)
        fyi = f"Note: pipeline '{automation_id}' already has other active instance(s): {others}.\n"

    if schedule_type == "cron":
        # 2026-07-11: previously wrote {"type": "cron", "expr": ...} — the store's
        # _initial_next_run()/_compute_next_run() only ever read "cron"/
        # "interval_seconds" keys, so every workflow made via this tool silently
        # got next_run_at=None and claim_due_workflows() (WHERE next_run_at IS
        # NOT NULL) never picked it up — "created successfully" but dead on
        # arrival. Use the keys the store actually reads, and validate first.
        cron_err = workflow_store.validate_cron(schedule_expr, timezone_name)
        if cron_err:
            return f"Error: {cron_err}"
        schedule = {"cron": schedule_expr, "timezone": timezone_name}
    elif schedule_type == "interval":
        try:
            seconds = int(schedule_expr)
        except ValueError:
            return f"Error: schedule_expr for 'interval' must be an integer (seconds), got '{schedule_expr}'"
        schedule = {"interval_seconds": seconds}
    else:
        return f"Error: Invalid schedule_type '{schedule_type}'. Must be 'cron' or 'interval'."

    try:
        instance = workflow_store.create_workflow_instance(
            workflow_id=workflow_id,
            automation_id=automation_id,
            task_name=task_name,
            schedule=schedule,
            config=params,
            status=status,
        )
        return f"{fyi}Workflow '{workflow_id}' created successfully. Next run: {instance.get('next_run_at')}"
    except Exception as e:
        return f"Error creating workflow: {e}"

def list_available_pipelines(args: dict) -> str:
    """List registered handler pipeline IDs usable as create_workflow's automation_id,
    each annotated with how many active instances already exist."""
    from jobs import handler_registry
    described = handler_registry.describe_all()
    if not described:
        return "No pipelines registered (handler registry not populated in this process)."
    lines = []
    for aid, desc in sorted(described.items()):
        active = workflow_store.list_workflow_instances(automation_id=aid, status="active")
        note = (
            f" [{len(active)} active instance(s), next: {active[0].get('next_run_at')}]"
            if active else " [0 active instances]"
        )
        lines.append(f"• {aid} — {desc}{note}")
    return "\n".join(lines)

def list_workflows(args: dict) -> str:
    """List all workflow instances."""
    limit = args.get("limit", 50)
    try:
        instances = workflow_store.list_workflow_instances(limit=limit)
        if not instances:
            return "No workflow instances found."
        
        output = []
        for inst in instances:
            output.append(
                f"- ID: {inst['id']}\n"
                f"  Automation: {inst['automation_id']}\n"
                f"  Task: {inst['task_name']}\n"
                f"  Schedule: {inst['schedule']}\n"
                f"  Status: {inst['status']}\n"
                f"  Last Run: {inst.get('last_run_at') or 'Never'}\n"
                f"  Next Run: {inst.get('next_run_at') or 'N/A'}"
            )
        return "\n".join(output)
    except Exception as e:
        return f"Error listing workflows: {e}"
