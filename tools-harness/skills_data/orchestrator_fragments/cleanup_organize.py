"""Orchestrator prompt fragment: apply_organization destructive-confirm rule."""

FRAGMENT = """- `apply_organization` (via ask_utility_agent) can delete files if the query asks for cleanup — always confirm with the user which files/scheme before calling it destructively, same as ask_delete_file.
"""
