"""Deterministic lifecycle hooks around tool calls and model boundaries."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable


@dataclass(frozen=True)
class ToolHookContext:
    name: str
    arguments: dict


_pre_tool: list[Callable[[ToolHookContext], str | None]] = []
_post_tool: list[Callable[[ToolHookContext, str], str]] = []


def register_pre_tool(hook: Callable[[ToolHookContext], str | None]) -> None:
    _pre_tool.append(hook)


def register_post_tool(hook: Callable[[ToolHookContext, str], str]) -> None:
    _post_tool.append(hook)


def run_pre_tool(name: str, arguments: dict) -> str | None:
    context = ToolHookContext(name, arguments or {})
    for hook in tuple(_pre_tool):
        result = hook(context)
        if result:
            return result
    return None


def run_post_tool(name: str, arguments: dict, result: str) -> str:
    context = ToolHookContext(name, arguments or {})
    for hook in tuple(_post_tool):
        result = hook(context, result)
    return result


def _quarantine_guard(context: ToolHookContext) -> str | None:
    if context.name in {"read_document", "read_file", "document_retrieve", "semantic_file_search", "fulltext_search"}:
        path = context.arguments.get("path")
        if path:
            from tools.document_manifest import is_quarantined
            if is_quarantined(path):
                return f"[blocked] {context.name}: document is quarantined: {Path(path).expanduser().resolve()}"
    return None


def _external_content_boundary(context: ToolHookContext, result: str) -> str:
    from tools.injection_guard import wrap_external_output
    if result.startswith(f'<external_content source="{context.name}">'):
        return result
    return wrap_external_output(context.name, result)


def install_default_hooks() -> None:
    if _quarantine_guard not in _pre_tool:
        register_pre_tool(_quarantine_guard)
    if _external_content_boundary not in _post_tool:
        register_post_tool(_external_content_boundary)


install_default_hooks()
