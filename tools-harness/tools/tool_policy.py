"""
Tool-name/command-level security boundary: which CLI binaries, Python
built-ins/imports, and tool-result content are allowed. Split out of
security_utils.py (which conflated this with path-based policy in one file)
— this module is the one place bash_exec's command string and inline `python
-c` code get checked before running, plus output sanitization keyed by tool
name. See path_policy.py for the separate, unrelated concern of which
filesystem *paths* a tool's arguments are allowed to touch.
"""
import ast
import logging
import os
import re
import shlex

log = logging.getLogger(__name__)

# Dangerous CLI binaries forbidden in shell execution
DANGEROUS_COMMANDS = {
    "sudo", "security", "osascript", "curl", "wget", "nc", "netcat",
    "ssh", "scp", "sftp", "telnet", "crontab", "sh", "bash", "zsh"
}

# Prompt injection keywords to sanitize
PROMPT_INJECTION_KEYWORDS = [
    r"\bignore\s+(all\s+)?previous\s+instructions\b",
    r"\bsystem\s+instructions?\b",
    r"\bassistant\s+instructions?\b",
    r"\bexecute\s+the\s+following\s+command\b",
    r"\brun\s+this\s+command\b",
    r"\bcall\s+the\s+tool\b",
    r"\bexecute\s+this\s+shell\b",
    r"\byou\s+must\s+now\b",
    r"\boverride\s+instructions\b",
]


def check_python_code_safety(code: str) -> None:
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return  # Let interpreter handle syntax errors

    for node in ast.walk(tree):
        # 1. Block dangerous imports
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = [alias.name for alias in node.names]
            if isinstance(node, ast.ImportFrom) and node.module:
                names.append(node.module)
            for name in names:
                first_part = name.split(".")[0].lower()
                if first_part in ("subprocess", "pty", "socket", "webbrowser"):
                    raise ValueError(f"Importing '{first_part}' is forbidden for security reasons.")

        # 2. Block dangerous call attributes / built-ins
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
                module_name = func.value.id.lower()
                attr_name = func.attr.lower()
                if module_name == "os" and attr_name in ("system", "popen", "spawn", "exec", "fork", "kill", "posix_spawn", "posix_spawnp"):
                    raise ValueError(f"Calling dangerous function 'os.{func.attr}' is forbidden.")
                if module_name == "sys" and attr_name in ("modules",):
                    raise ValueError(f"Accessing 'sys.{func.attr}' is forbidden.")
            elif isinstance(func, ast.Name):
                if func.id in ("eval", "exec", "execfile", "compile"):
                    raise ValueError(f"Calling dangerous built-in '{func.id}' is forbidden.")


def validate_command(command: str) -> None:
    if "`" in command:
        raise ValueError("Command substitution using backticks is forbidden.")
    if "$(" in command:
        raise ValueError("Command substitution using $() is forbidden.")
    if ";" in command:
        raise ValueError("Command chaining using semicolons (;) is forbidden.")

    # Parse command using shlex
    try:
        cmd_clean = command.replace("&&", " ").replace("||", " ").replace("|", " ").replace(">", " ").replace("<", " ")
        tokens = shlex.split(cmd_clean)
    except Exception:
        log.debug("shlex.split failed, falling back to str.split", exc_info=True)
        tokens = command.replace("&&", " ").replace("||", " ").replace("|", " ").split()

    for i, token in enumerate(tokens):
        base_name = os.path.basename(token).lower()
        if base_name in DANGEROUS_COMMANDS:
            raise ValueError(f"Execution of dangerous command '{base_name}' is forbidden.")

        # Check for inline python execution (e.g. python -c "code")
        if base_name in ("python", "python3") and i + 2 < len(tokens):
            if tokens[i + 1] == "-c":
                check_python_code_safety(tokens[i + 2])


def sanitize_output(name: str, content: str) -> str:
    # Only sanitize log reading, file reading, and web scraping output
    if name not in (
        "read_file", "grep_files", "find_files", "list_directory", "file_tree",
        "read_many_files", "web_search", "searxng_search", "scrapling_fetch",
        "scrapling_stealthy_fetch", "scrapling_fetch_and_extract", "parse_document",
        "parse_email_file", "archive_grep"
    ):
        return content

    sanitized = content
    for pattern in PROMPT_INJECTION_KEYWORDS:
        def repl(match):
            return f"[SANITIZED_INJECTION: {match.group(0)}]"
        sanitized = re.sub(pattern, repl, sanitized, flags=re.IGNORECASE)

    return sanitized
