from tools.shell import bash_exec, set_shell_workspace


def test_bash_exec_defaults_to_active_workspace(tmp_path):
    set_shell_workspace(str(tmp_path))
    result = bash_exec("pwd")

    assert str(tmp_path) in result


def test_bash_exec_rejects_blocked_cwd():
    set_shell_workspace("/private/tmp")
    result = bash_exec("pwd", cwd="/etc")

    assert "security validation failed" in result.lower()
