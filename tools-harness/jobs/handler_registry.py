"""
Handler registry for DB-driven workflow dispatch.

Maps automation_id → handler function.
Each handler receives a decoded workflow_instance dict and returns a result dict.
"""
from __future__ import annotations
import logging
from typing import Callable

_log = logging.getLogger(__name__)

_REGISTRY: dict[str, Callable[[dict], dict]] = {}


def register(automation_id: str, fn: Callable[[dict], dict]) -> None:
    _REGISTRY[automation_id] = fn
    _log.debug("handler registered: %s", automation_id)


def dispatch(instance: dict) -> dict:
    automation_id = instance.get("automation_id", "")
    fn = _REGISTRY.get(automation_id)
    if fn is None:
        raise KeyError(f"No handler registered for automation_id={automation_id!r}")
    return fn(instance)


def registered_ids() -> list[str]:
    return list(_REGISTRY.keys())


def describe_all() -> dict[str, str]:
    """automation_id -> description, pulled from each handler module's docstring
    plus its handle() function's own docstring. Backs the create_workflow
    discovery tool — the model previously had no way to see which automation_id
    values are legal to schedule fresh (only already-deployed instances via
    list_automations), so it couldn't reuse an existing pipeline for a new
    config and had to ask the user or guess (confirmed live 2026-07-11: a
    "watch James's email, save attachments" request correctly identified that
    inbox.monitor_attachments-style logic already existed but had no way to
    confirm it could point create_workflow at it).

    2026-07-11 follow-up: the module-doc summary alone wasn't enough — the
    model then guessed a plausible-but-wrong config key ("destination_folder"
    instead of the handler's actual "dest_dir"), silently falling back to the
    handler's default instead of erroring, because nothing told it what config
    keys the pipeline actually reads. Several handlers already document those
    keys in handle()'s own docstring ("instance[\"config\"] keys: ..." — see
    inbox_monitor_attachments.py, email_watch_sender.py,
    email_attachment_watch.py) — append that when present.
    """
    import inspect
    out = {}
    for automation_id, fn in _REGISTRY.items():
        module_doc = inspect.getmodule(fn).__doc__ or ""
        lines = [ln.strip() for ln in module_doc.strip().splitlines() if ln.strip()]
        # First line is usually "Handler: <id>" — skip it. The remaining lines
        # are a hard-wrapped paragraph in source, not separate sentences, so
        # join them into one line instead of truncating at the first newline.
        body = [ln for ln in lines if not ln.lower().startswith("handler:")]
        desc = " ".join(body)

        fn_doc = (fn.__doc__ or "").strip()
        if fn_doc:
            desc += " | config: " + " ".join(ln.strip() for ln in fn_doc.splitlines() if ln.strip())

        out[automation_id] = desc
    return out
