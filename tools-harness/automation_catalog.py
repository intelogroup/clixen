"""
Automation starter pack for common hands-free user workflows.

Each automation is intentionally lightweight:
  - small parameter surface
  - maps to an existing queue task
  - suitable for one-click dispatch from the UI
"""

from __future__ import annotations

import os
from copy import deepcopy


AUTOMATIONS: list[dict] = [
    {
        "id": "inbox_autopilot",
        "title": "Inbox autopilot",
        "summary": "Summarize urgent mail, pull out action items, and send a Telegram recap.",
        "task_name": "inbox_autopilot",
        "params": {"focus": "urgent unread email follow-ups"},
        "icon": "mail",
        "category": "Inbox",
        "approval": "notify-only",
        "outputs": ["Telegram recap", "Tasks"],
        "primary_field": {
            "key": "focus",
            "label": "Focus",
            "placeholder": "vip senders, invoices, follow-ups",
        },
    },
    {
        "id": "calendar_copilot",
        "title": "Calendar copilot",
        "summary": "Review upcoming meetings, conflicts, and prep notes for today.",
        "task_name": "calendar_copilot",
        "params": {"window": "today and tomorrow"},
        "icon": "calendar",
        "category": "Calendar",
        "approval": "notify-only",
        "outputs": ["Telegram briefing"],
        "primary_field": {
            "key": "window",
            "label": "Window",
            "placeholder": "today, next 48 hours, next week",
        },
    },
    {
        "id": "admin_cleanup",
        "title": "Personal admin cleanup",
        "summary": "Turn loose obligations into a prioritized admin checklist.",
        "task_name": "admin_cleanup",
        "params": {"focus": "bills subscriptions receipts forms"},
        "icon": "clipboard",
        "category": "Admin",
        "approval": "task-creation",
        "outputs": ["Prioritized checklist", "Tasks"],
        "primary_field": {
            "key": "focus",
            "label": "Focus",
            "placeholder": "expenses, reimbursements, subscriptions",
        },
    },
    {
        "id": "watch_and_tell",
        "title": "Watch and tell me",
        "summary": "Check one topic and push back only the important changes.",
        "task_name": "watch_and_alert",
        "params": {"topic": "AI launches and price changes"},
        "icon": "radar",
        "category": "Monitoring",
        "approval": "notify-only",
        "outputs": ["Telegram alert"],
        "primary_field": {
            "key": "topic",
            "label": "Topic",
            "placeholder": "GPU prices, job postings, competitors",
        },
    },
    {
        "id": "browser_chore_bot",
        "title": "Browser chore bot",
        "summary": "Open a site, inspect the page, and outline the next manual approval step.",
        "task_name": "browser_chore",
        "params": {"goal": "complete a repetitive web chore with approval before submit"},
        "icon": "browser",
        "category": "Browser",
        "approval": "human-in-loop",
        "outputs": ["Approval-ready next step"],
        "primary_field": {
            "key": "goal",
            "label": "Goal",
            "placeholder": "renew a subscription, prep a form, inspect a portal",
        },
    },
    {
        "id": "family_logistics",
        "title": "Family logistics",
        "summary": "Bundle errands, school reminders, and household tasks into one update.",
        "task_name": "family_logistics",
        "params": {"household": "family errands and school notices"},
        "icon": "home",
        "category": "Home",
        "approval": "task-creation",
        "outputs": ["Shared checklist", "Tasks"],
        "primary_field": {
            "key": "household",
            "label": "Scope",
            "placeholder": "school notices, chores, pickups",
        },
    },
    {
        "id": "founder_autopilot",
        "title": "Founder autopilot",
        "summary": "Turn inbox, tasks, and search signals into a founder daily brief.",
        "task_name": "founder_pipeline",
        "params": {"focus": "leads customer follow-ups competitor watch"},
        "icon": "briefcase",
        "category": "Founder",
        "approval": "task-creation",
        "outputs": ["Founder brief", "Tasks"],
        "primary_field": {
            "key": "focus",
            "label": "Focus",
            "placeholder": "pipeline, renewals, hiring, product feedback",
        },
    },
    {
        "id": "knowledge_to_action",
        "title": "Knowledge to action",
        "summary": "Convert docs, notes, or research into tasks and a short brief.",
        "task_name": "knowledge_to_action",
        "params": {"source": "recent docs and notes"},
        "icon": "spark",
        "category": "Knowledge",
        "approval": "task-creation",
        "outputs": ["Brief", "Tasks"],
        "primary_field": {
            "key": "source",
            "label": "Source",
            "placeholder": "meeting notes, roadmap doc, research PDF",
        },
    },
    {
        "id": "travel_mode",
        "title": "Device context automation",
        "summary": "Prepare a travel-mode checklist with timing, reminders, and key info.",
        "task_name": "travel_mode",
        "params": {"context": "travel day and arrival checklist"},
        "icon": "plane",
        "category": "Travel",
        "approval": "reminder-creation",
        "outputs": ["Checklist", "Reminder"],
        "primary_field": {
            "key": "context",
            "label": "Context",
            "placeholder": "airport departure, hotel check-in, landing day",
        },
    },
    {
        "id": "closed_loop_reminder",
        "title": "Closed-loop reminder",
        "summary": "Create a reminder flow that keeps nudging until the task is done.",
        "task_name": "stubborn_reminder",
        "params": {"task": "follow up until complete"},
        "icon": "loop",
        "category": "Reminder",
        "approval": "reminder-creation",
        "outputs": ["Reminder", "Task"],
        "primary_field": {
            "key": "task",
            "label": "Task",
            "placeholder": "submit taxes, chase invoice, send draft",
        },
    },
    {
        "id": "tech_brief",
        "title": "Tech news briefing",
        "summary": "AI, tech, and startup news delivered at 7AM and 6PM daily.",
        "task_name": "tech_brief",
        "params": {"email": os.environ.get("CLIXEN_EMAIL", "owner@local.dev")},
        "icon": "radar",
        "category": "Monitoring",
        "approval": "notify-only",
        "outputs": ["Email briefing"],
        "primary_field": {
            "key": "email",
            "label": "Email",
            "placeholder": os.environ.get("CLIXEN_EMAIL", "owner@local.dev"),
        },
    },
]


def list_automations() -> list[dict]:
    return deepcopy(AUTOMATIONS)


def get_automation(automation_id: str) -> dict | None:
    for automation in AUTOMATIONS:
        if automation["id"] == automation_id:
            return deepcopy(automation)
    return None


def build_dispatch_payload(automation_id: str, overrides: dict | None = None) -> dict:
    automation = get_automation(automation_id)
    if automation is None:
        raise KeyError(automation_id)

    params = deepcopy(automation.get("params", {}))
    if overrides:
        params.update(overrides)

    return {
        "automation_id": automation["id"],
        "task_name": automation["task_name"],
        "params": params,
    }
