"""
tools/shell_tools.py — Shell command runner for agents

Provides:
  - run_command()         : Generic subprocess runner
  - run_pytest()          : Convenience wrapper for pytest
  - run_static_analysis() : AST + optional pyflakes static checker
  - python_version()      : Python version string
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path


# ─── Generic command runner ───────────────────────────────────────────────────

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


# ─── Pytest runner (Python-specific) ────────────────────────────────────────

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


# ─── Language detection ───────────────────────────────────────────────────────

# Extension → canonical language name
_EXT_LANG: dict[str, str] = {
    ".py":    "python",
    ".java":  "java",
    ".kt":    "kotlin",
    ".kts":   "kotlin",
    ".ts":    "nodejs",   # TypeScript → Node.js toolchain
    ".js":    "nodejs",
    ".mjs":   "nodejs",
    ".go":    "go",
    ".rs":    "rust",
    ".cs":    "csharp",
    ".rb":    "ruby",
    ".php":   "php",
}


def detect_language(files: dict[str, str]) -> str:
    """
    Infer backend language from the file extensions in `files`.
    Returns the canonical language name, or 'unknown' if none matches.
    """
    from collections import Counter
    import os
    counts: Counter[str] = Counter()
    for path in files:
        ext = os.path.splitext(path)[-1].lower()
        lang = _EXT_LANG.get(ext)
        if lang:
            counts[lang] += 1
    return counts.most_common(1)[0][0] if counts else "unknown"


# ─── Language-aware test runner ───────────────────────────────────────────────

def run_tests(project_root: str, language: str, test_path: str = "") -> dict:
    """
    Run the appropriate test suite based on the target language.

    Supports:
      python  → pytest
      java    → mvn test (falls back to gradle test)
      kotlin  → gradle test
      nodejs  → npm test (expects jest/mocha in package.json)
      go      → go test ./...
      rust    → cargo test
      csharp  → dotnet test
      ruby    → bundle exec rspec (or ruby -Ilib -Ispec)
      php     → composer run-script test
      unknown → pytest fallback
    """
    lang = language.lower().strip()

    if lang == "python":
        tp = test_path or "tests/"
        cmd = [sys.executable, "-m", "pytest", tp, "-v", "--tb=short"]

    elif lang == "java":
        import os
        if os.path.exists(os.path.join(project_root, "pom.xml")):
            cmd = ["mvn", "test", "-q"]
        else:
            cmd = ["gradle", "test"]

    elif lang == "kotlin":
        cmd = ["gradle", "test"]

    elif lang == "nodejs":
        cmd = ["npm", "test"]

    elif lang == "go":
        cmd = ["go", "test", "./..."]

    elif lang == "rust":
        cmd = ["cargo", "test"]

    elif lang == "csharp":
        cmd = ["dotnet", "test"]

    elif lang == "ruby":
        import os
        if os.path.exists(os.path.join(project_root, "Gemfile")):
            cmd = ["bundle", "exec", "rspec"]
        else:
            cmd = ["ruby", "-Ilib", "-Ispec", test_path or "spec"]

    elif lang == "php":
        cmd = ["composer", "run-script", "test"]

    else:
        # Fallback: try pytest (won't harm non-python projects, just exit quickly)
        tp = test_path or "tests/"
        cmd = [sys.executable, "-m", "pytest", tp, "-v", "--tb=short"]

    return run_command(cmd=cmd, cwd=project_root, timeout=300)


# ─── Static analysis ─────────────────────────────────────────────────────────

def run_static_analysis(files: dict[str, str], language: str = "auto") -> dict:
    """
    Run static analysis on a dict of {relative_path: content} source files.

    Python  → ast.parse() + pyflakes
    Java    → javac (compile-check only, not run)
    Node.js → tsc --noEmit (if tsconfig present) or eslint --no-eslintrc
    Go      → go vet (via subprocess on written temp files)
    Others  → skipped (returns no errors)

    Returns:
        {
            "errors":     list[str],
            "has_errors": bool,
        }
    """
    import tempfile, os

    # Auto-detect from file extensions when language is "auto"
    effective_lang = language if language != "auto" else detect_language(files)

    errors: list[str] = []

    # ── Python ───────────────────────────────────────────────────────────────
    if effective_lang == "python":
        py_files = {p: c for p, c in files.items() if p.endswith(".py")}
        if not py_files:
            return {"errors": [], "has_errors": False}

        for rel_path, content in py_files.items():
            try:
                ast.parse(content, filename=rel_path)
            except SyntaxError as e:
                errors.append(
                    f"[SYNTAX ERROR] {rel_path}:{e.lineno}: {e.msg} "
                    f"(text: {e.text!r})"
                )
            except Exception as e:
                errors.append(f"[PARSE ERROR] {rel_path}: {e}")

        try:
            from pyflakes import api as pyflakes_api        # type: ignore
            from pyflakes import reporter as pyflakes_rpt   # type: ignore
            import io

            for rel_path, content in py_files.items():
                if any(rel_path in e for e in errors):
                    continue
                buf = io.StringIO()

                class _Reporter(pyflakes_rpt.Reporter):
                    def unexpectedError(self, filename, msg):
                        buf.write(f"[PYFLAKES UNEXPECTED] {filename}: {msg}\n")
                    def syntaxError(self, filename, msg, lineno, offset, text):
                        buf.write(f"[PYFLAKES SYNTAX] {filename}:{lineno}: {msg}\n")
                    def flake(self, message):
                        buf.write(f"[PYFLAKES] {message}\n")

                result = pyflakes_api.check(content, rel_path, reporter=_Reporter(buf, buf))
                output = buf.getvalue().strip()
                if result > 0 and output:
                    errors.extend(output.splitlines())

        except ImportError:
            pass

    # ── Java — use mvn test-compile (respects pom.xml classpath) ─────────────
    elif effective_lang == "java":
        import tempfile
        java_files = {p: c for p, c in files.items() if p.endswith(".java")}
        pom_content = files.get("pom.xml") or files.get("./pom.xml")
        if java_files and pom_content:
            with tempfile.TemporaryDirectory() as tmpdir:
                # Write pom.xml + java sources to temp dir preserving directory structure
                import os as _os
                _pom = _os.path.join(tmpdir, "pom.xml")
                with open(_pom, "w", encoding="utf-8") as f:
                    f.write(pom_content)
                for rel, content in java_files.items():
                    dest = _os.path.join(tmpdir, rel)
                    _os.makedirs(_os.path.dirname(dest), exist_ok=True)
                    with open(dest, "w", encoding="utf-8") as f:
                        f.write(content)
                # mvn test-compile downloads deps, compiles src + test
                r = run_command(
                    ["mvn", "test-compile", "-q", "--batch-mode",
                     "-Dmaven.test.skip=true"],
                    cwd=tmpdir, timeout=300,
                )
                if r["returncode"] != 0:
                    for line in (r["stdout"] + r["stderr"]).splitlines():
                        if line.strip():
                            errors.append(f"[MVN] {line}")
        elif java_files:
            # No pom.xml yet (coder hasn't produced it) — defer to integration build
            errors.append(
                "[MVN] pom.xml not found — Java compilation check deferred to integration build"
            )

    # ── Node.js / TypeScript ─────────────────────────────────────────────────
    elif effective_lang == "nodejs":
        ts_files = {p: c for p, c in files.items() if p.endswith(".ts")}
        if ts_files:
            with tempfile.TemporaryDirectory() as tmpdir:
                for rel, content in ts_files.items():
                    dest = os.path.join(tmpdir, rel)
                    os.makedirs(os.path.dirname(dest), exist_ok=True)
                    with open(dest, "w", encoding="utf-8") as f:
                        f.write(content)
                result = run_command(
                    ["npx", "--yes", "tsc", "--noEmit", "--strict", "--allowJs",
                     "--target", "ES2020", "--module", "commonjs"],
                    cwd=tmpdir, timeout=60,
                )
                if result["returncode"] != 0:
                    for line in (result["stdout"] + result["stderr"]).splitlines():
                        errors.append(f"[TSC] {line}")

    # ── Go ───────────────────────────────────────────────────────────────────
    elif effective_lang == "go":
        go_files = {p: c for p, c in files.items() if p.endswith(".go")}
        if go_files:
            with tempfile.TemporaryDirectory() as tmpdir:
                for rel, content in go_files.items():
                    dest = os.path.join(tmpdir, rel)
                    os.makedirs(os.path.dirname(dest), exist_ok=True)
                    with open(dest, "w", encoding="utf-8") as f:
                        f.write(content)
                result = run_command(["go", "vet", "./..."], cwd=tmpdir, timeout=60)
                if result["returncode"] != 0:
                    for line in (result["stdout"] + result["stderr"]).splitlines():
                        errors.append(f"[GO VET] {line}")

    # ── Other languages: skip static analysis ────────────────────────────────
    # (Compiler errors will surface during test run)

    return {
        "errors": errors,
        "has_errors": bool(errors),
    }



# ─── Pyflakes auto-fix (Python only) ─────────────────────────────────────────

import re as _re

_UNUSED_IMPORT_RE = _re.compile(
    r"\[PYFLAKES\] (.+?):(\d+):\d+: .+ imported but unused"
)
_UNUSED_VAR_RE = _re.compile(
    r"\[PYFLAKES\] (.+?):(\d+):\d+: local variable .+ is assigned to but never used"
)
_REDEF_UNUSED_RE = _re.compile(
    r"\[PYFLAKES\] (.+?):(\d+):\d+: redefinition of unused .+ from line \d+"
)


def auto_fix_pyflakes(
    files: dict[str, str],
    errors: list[str],
) -> tuple[dict[str, str], list[str]]:
    """
    Deterministically fix simple pyflakes errors without an LLM call.

    Handles:
      - 'X imported but unused'              → comment out the import line
      - 'redefinition of unused X'           → comment out the duplicate import line
      - 'local variable X assigned but never used' → comment out the assignment

    Args:
        files:  dict of {relative_path: source_code}
        errors: list of pyflakes error strings from run_static_analysis()

    Returns:
        (patched_files, remaining_errors) — remaining_errors need a Debugger LLM call.
    """
    lines_to_comment: dict[str, set[int]] = {}
    fixed_indices: set[int] = set()

    for idx, err in enumerate(errors):
        for pattern in (_UNUSED_IMPORT_RE, _REDEF_UNUSED_RE, _UNUSED_VAR_RE):
            m = pattern.search(err)
            if m:
                rel_path = m.group(1).strip()
                lineno   = int(m.group(2))
                lines_to_comment.setdefault(rel_path, set()).add(lineno)
                fixed_indices.add(idx)
                break

    patched = dict(files)
    for rel_path, line_nums in lines_to_comment.items():
        if rel_path not in patched:
            continue
        source_lines = patched[rel_path].splitlines(keepends=True)
        for lineno in line_nums:
            i0 = lineno - 1  # 0-based
            if 0 <= i0 < len(source_lines):
                orig = source_lines[i0].rstrip("\n\r")
                source_lines[i0] = f"# [auto-fixed] {orig}\n"
        patched[rel_path] = "".join(source_lines)

    remaining = [e for i, e in enumerate(errors) if i not in fixed_indices]
    return patched, remaining


# ─── Misc ─────────────────────────────────────────────────────────────────────


def python_version() -> str:
    """Return the current Python version string."""
    return sys.version
