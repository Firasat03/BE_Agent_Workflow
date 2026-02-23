"""
tools/shell_tools.py — Shell command runner for agents (used by Tester)
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def run_command(
    cmd: str | list[str],
    cwd: str | None = None,
    timeout: int = 120,
    env: dict | None = None,
) -> dict:
    """
    Run a shell command and return a result dict:
        {
            "returncode": int,
            "stdout": str,
            "stderr": str,
            "command": str,
        }

    Args:
        cmd:      Command string or list of args.
        cwd:      Working directory (defaults to current dir).
        timeout:  Max seconds to wait (default 120).
        env:      Optional environment variable overrides.
    """
    import os

    if isinstance(cmd, str):
        cmd_display = cmd
        shell = True
    else:
        cmd_display = " ".join(cmd)
        shell = False

    merged_env = {**os.environ, **(env or {})}

    try:
        result = subprocess.run(
            cmd,
            shell=shell,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=merged_env,
        )
        return {
            "returncode": result.returncode,
            "stdout": result.stdout.strip(),
            "stderr": result.stderr.strip(),
            "command": cmd_display,
        }
    except subprocess.TimeoutExpired:
        return {
            "returncode": -1,
            "stdout": "",
            "stderr": f"Command timed out after {timeout}s: {cmd_display}",
            "command": cmd_display,
        }
    except Exception as e:
        return {
            "returncode": -1,
            "stdout": "",
            "stderr": f"Failed to run command '{cmd_display}': {e}",
            "command": cmd_display,
        }


def run_pytest(project_root: str, test_path: str = "tests/") -> dict:
    """
    Convenience wrapper to run pytest on a project.
    Returns the same dict as run_command.
    """
    return run_command(
        cmd=[sys.executable, "-m", "pytest", test_path, "-v", "--tb=short"],
        cwd=project_root,
        timeout=180,
    )


def python_version() -> str:
    """Return the current Python version string."""
    return sys.version
