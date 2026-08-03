import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tools.filesystem import find_files, grep_files, list_directory, read_file
from tools.git import git_add, git_commit, git_log, git_status
from tools.shell import bash_exec, edit_file, write_file


def test_filesystem_and_git_tools_smoke(tmp_path):
    repo = tmp_path / "repo"
    src = repo / "src"
    target = src / "hello.py"

    src.mkdir(parents=True)

    assert "Written" in write_file(
        str(target),
        "def greet(name):\n    return f'Hello, {name}!'\n",
    )
    assert "greet" in read_file(str(target))

    assert "Edited" in edit_file(
        str(target),
        "return f'Hello, {name}!'",
        "return f'Hi, {name}!'",
    )
    assert "Hi" in grep_files("Hi", str(repo), glob="*.py")
    assert "hello.py" in find_files("*.py", str(repo))
    assert "src" in list_directory(str(repo))

    init = bash_exec(
        "git init -b main && git config user.email t@example.com && git config user.name Test",
        cwd=str(repo),
    )
    assert "[exit code: 0]" in init

    assert git_add(str(repo), ".") == "[no output]"
    assert "initial smoke commit" in git_commit("initial smoke commit", str(repo))
    assert "initial smoke commit" in git_log(str(repo), 1)
    assert "Branch: main" in git_status(str(repo))
