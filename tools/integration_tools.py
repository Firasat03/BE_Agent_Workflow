"""
integration_tools.py — Live integration testing for the BE Multi-Agent Workflow

Builds the generated project, starts the server as a background process,
runs HTTP assertions against every API endpoint declared in the plan, then
kills the server and returns a structured result.

Supported languages
-------------------
  java    → mvn package -DskipTests  → java -jar target/<app>.jar
  nodejs  → npm run build (if exists) → node dist/index.js  OR  node src/index.js
  go      → go build -o ./app        → ./app
  python  → (no build)               → python -m uvicorn / gunicorn / python main.py
  others  → skipped (returns passed=True with a warning)

Health check
------------
All servers are expected to expose one of these paths:
  /actuator/health (Spring Boot Actuator)
  /health
  /healthz
  /  (fallback)

The tool polls up to ``MAX_STARTUP_SECS`` seconds, then times out.
"""

from __future__ import annotations

import os
import re
import sys
import signal
import subprocess
import time
import json
import urllib.request
from pathlib import Path
from typing import Optional


MAX_STARTUP_SECS   = 45   # seconds to wait for the server to become ready
HEALTH_POLL_SECS   = 1    # interval between health polls
_DEFAULT_PORT      = 8080

_HEALTH_PATHS = [
    "/actuator/health",
    "/health",
    "/healthz",
    "/",
]


# ─── helpers ──────────────────────────────────────────────────────────────────

def _write_files_to_disk(files: dict[str, str], project_root: str) -> None:
    """Write all generated files to disk so the build tools can see them."""
    for rel_path, content in files.items():
        abs_path = Path(project_root) / rel_path
        abs_path.parent.mkdir(parents=True, exist_ok=True)
        abs_path.write_text(content, encoding="utf-8")


def _find_jar(project_root: str) -> Optional[str]:
    target = Path(project_root) / "target"
    if not target.exists():
        return None
    jars = sorted(target.glob("*.jar"), key=lambda p: p.stat().st_size, reverse=True)
    # skip the sources / tests jars
    jars = [j for j in jars if "sources" not in j.name and "tests" not in j.name]
    return str(jars[0]) if jars else None


def _poll_health(port: int) -> bool:
    """Return True once the server responds with 2xx on any health path."""
    import urllib.request
    for path in _HEALTH_PATHS:
        url = f"http://localhost:{port}{path}"
        try:
            with urllib.request.urlopen(url, timeout=2) as r:
                if r.status < 300:
                    return True
        except Exception:
            pass
    return False


def _curl(method: str, url: str, body: Optional[str] = None,
          headers: Optional[dict] = None) -> dict:
    """Run a single HTTP request with subprocess curl and return a result dict."""
    cmd = ["curl", "-s", "-w", "\n%{http_code}", "-X", method.upper(), url]
    if headers:
        for k, v in headers.items():
            cmd += ["-H", f"{k}: {v}"]
    if body:
        cmd += ["-d", body]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        lines = r.stdout.strip().rsplit("\n", 1)
        body_out = lines[0] if len(lines) > 1 else ""
        status_code = int(lines[-1]) if lines[-1].isdigit() else 0
        return {"status_code": status_code, "body": body_out, "error": r.stderr.strip()}
    except Exception as exc:
        return {"status_code": 0, "body": "", "error": str(exc)}


# ─── build helpers ────────────────────────────────────────────────────────────

def _build_java(project_root: str) -> dict:
    r = subprocess.run(
        ["mvn", "package", "-DskipTests", "-q"],
        cwd=project_root, capture_output=True, text=True, timeout=300,
    )
    return {"ok": r.returncode == 0, "output": r.stdout + r.stderr}


def _build_nodejs(project_root: str) -> dict:
    pkg = Path(project_root) / "package.json"
    import json
    scripts = {}
    if pkg.exists():
        try:
            scripts = json.loads(pkg.read_text()).get("scripts", {})
        except Exception:
            pass
    if "build" in scripts:
        r = subprocess.run(
            ["npm", "run", "build"], cwd=project_root,
            capture_output=True, text=True, timeout=120,
        )
        return {"ok": r.returncode == 0, "output": r.stdout + r.stderr}
    return {"ok": True, "output": "no build script — running source directly"}


def _build_go(project_root: str) -> dict:
    r = subprocess.run(
        ["go", "build", "-o", "app", "./..."],
        cwd=project_root, capture_output=True, text=True, timeout=120,
    )
    return {"ok": r.returncode == 0, "output": r.stdout + r.stderr}


def _build_python(project_root: str) -> dict:
    # Nothing to compile; just dependency check
    req = Path(project_root) / "requirements.txt"
    if req.exists():
        r = subprocess.run(
            [sys.executable, "-m", "pip", "install", "-r", "requirements.txt", "-q"],
            cwd=project_root, capture_output=True, text=True, timeout=120,
        )
        return {"ok": r.returncode == 0, "output": r.stdout + r.stderr}
    return {"ok": True, "output": "no requirements.txt — skipping install"}


# ─── server start helpers ─────────────────────────────────────────────────────

def _start_java_server(project_root: str, port: int) -> Optional[subprocess.Popen]:
    jar = _find_jar(project_root)
    if not jar:
        return None
    env = os.environ.copy()
    env["SERVER_PORT"] = str(port)
    return subprocess.Popen(
        ["java", f"-Dserver.port={port}", "-jar", jar],
        cwd=project_root, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        env=env, text=True,
    )


def _start_nodejs_server(project_root: str, port: int) -> Optional[subprocess.Popen]:
    env = os.environ.copy()
    env["PORT"] = str(port)
    # prefer dist/index.js, then src/index.js, then index.js
    for entry in ["dist/index.js", "src/index.js", "index.js"]:
        p = Path(project_root) / entry
        if p.exists():
            return subprocess.Popen(
                ["node", str(p)], cwd=project_root, env=env,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            )
    return None


def _start_go_server(project_root: str, port: int) -> Optional[subprocess.Popen]:
    env = os.environ.copy()
    env["PORT"] = str(port)
    exe = str(Path(project_root) / "app")
    if sys.platform == "win32":
        exe += ".exe"
    if not Path(exe).exists():
        return None
    return subprocess.Popen(
        [exe], cwd=project_root, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )


def _start_python_server(project_root: str, port: int) -> Optional[subprocess.Popen]:
    env = os.environ.copy()
    env["PORT"] = str(port)
    # Try uvicorn → gunicorn → plain python main.py
    for cmd in [
        [sys.executable, "-m", "uvicorn", "main:app", f"--port={port}"],
        [sys.executable, "-m", "gunicorn", "-b", f"0.0.0.0:{port}", "main:app"],
        [sys.executable, "main.py"],
    ]:
        if Path(project_root, cmd[-1].replace("main:app", "main.py")).exists() \
                or "--port" in " ".join(cmd):
            return subprocess.Popen(
                cmd, cwd=project_root, env=env,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            )
    return None


# ─── API contract parser ───────────────────────────────────────────────────────

def _parse_contracts(plan_items) -> list[dict]:
    """
    Extract HTTP test cases from PlanItem.api_contract strings.
    Example contract string: "POST /api/products → 201 {id, name}"
    Returns list of {method, path, expected_status}.
    """
    tests = []
    seen = set()
    pattern = re.compile(
        r"(GET|POST|PUT|PATCH|DELETE)\s+(/[\w/{}.-]*)\s*[→>-]+\s*(\d{3})",
        re.IGNORECASE,
    )
    for item in (plan_items or []):
        contract = getattr(item, "api_contract", "") or ""
        for m in pattern.finditer(contract):
            method, path, status = m.group(1).upper(), m.group(2), int(m.group(3))
            key = (method, path)
            if key not in seen:
                seen.add(key)
                tests.append({"method": method, "path": path, "expected_status": status})
    return tests


def _make_sample_body(method: str, path: str) -> Optional[str]:
    """Generate a minimal valid JSON body for write operations."""
    if method in ("GET", "DELETE"):
        return None
    path_lower = path.lower()
    if "product" in path_lower:
        return '{"name":"Test Product","description":"auto-generated","price":9.99,"stock":10}'
    if "user" in path_lower:
        return '{"username":"testuser","email":"test@example.com","password":"TestPass1!"}'
    if "order" in path_lower:
        return '{"productId":1,"quantity":2}'
    # generic fallback
    return '{"name":"test","value":"auto"}'


# ─── public API ───────────────────────────────────────────────────────────────

def run_integration_tests(
    project_root: str,
    language: str,
    plan_items,
    generated_files: dict[str, str],
    port: int = _DEFAULT_PORT,
) -> dict:
    """
    Build the project, start the server, run curl assertions, stop server.

    Returns
    -------
    dict with keys:
        passed       bool
        results      list[dict]  — per-test {method, path, expected, actual, passed, body}
        build_output str
        error        str | None
    """
    lang = language.lower().strip() if language else "unknown"
    results: list[dict] = []
    proc: Optional[subprocess.Popen] = None

    # ── 1. Write files to disk ─────────────────────────────────────────────
    _write_files_to_disk(generated_files, project_root)

    # ── 2. Build ───────────────────────────────────────────────────────────
    build_result = {"ok": True, "output": ""}
    if lang == "java":
        build_result = _build_java(project_root)
    elif lang == "nodejs":
        build_result = _build_nodejs(project_root)
    elif lang == "go":
        build_result = _build_go(project_root)
    elif lang == "python":
        build_result = _build_python(project_root)
    else:
        return {
            "passed": True,
            "results": [],
            "build_output": f"Integration tests skipped — no build strategy for language '{lang}'",
            "error": None,
        }

    if not build_result["ok"]:
        return {
            "passed": False,
            "results": [],
            "build_output": build_result["output"],
            "error": "BUILD FAILED — cannot start server",
        }

    # ── 3. Start server ────────────────────────────────────────────────────
    starters = {
        "java":   _start_java_server,
        "nodejs": _start_nodejs_server,
        "go":     _start_go_server,
        "python": _start_python_server,
    }
    proc = starters[lang](project_root, port)
    if proc is None:
        return {
            "passed": False,
            "results": [],
            "build_output": build_result["output"],
            "error": f"Could not determine server start command for language '{lang}'",
        }

    # ── 4. Wait for health ─────────────────────────────────────────────────
    ready = False
    for _ in range(int(MAX_STARTUP_SECS / HEALTH_POLL_SECS)):
        time.sleep(HEALTH_POLL_SECS)
        if proc.poll() is not None:
            # Server died
            break
        if _poll_health(port):
            ready = True
            break

    if not ready:
        try:
            proc.terminate()
        except Exception:
            pass
        return {
            "passed": False,
            "results": [],
            "build_output": build_result["output"],
            "error": f"Server did not become healthy within {MAX_STARTUP_SECS}s on port {port}",
        }

    # ── 5. Run curl tests ──────────────────────────────────────────────────
    base_url = f"http://localhost:{port}"
    tests = _parse_contracts(plan_items)

    # If plan has no contracts, at least hit health
    if not tests:
        tests = [{"method": "GET", "path": "/actuator/health", "expected_status": 200}]

    # Inject created IDs for GET/{id} / PUT/{id} / DELETE/{id} calls
    created_ids: dict[str, str] = {}  # resource_name → id

    for test in tests:
        method = test["method"]
        raw_path = test["path"]
        expected = test["expected_status"]

        # Replace {id} / {productId} with a real ID from a previous POST
        path = raw_path
        id_placeholder = re.search(r"\{(\w+)[Ii]d\}", raw_path)
        if id_placeholder:
            resource = id_placeholder.group(1)
            real_id = created_ids.get(resource, "1")
            path = re.sub(r"\{[^}]+\}", real_id, raw_path)

        body = _make_sample_body(method, path)
        url = base_url + path
        headers = {"Content-Type": "application/json"} if body else {}

        resp = _curl(method, url, body=body, headers=headers)
        passed = (resp["status_code"] == expected)

        # Capture ID from POST responses (e.g. {"id":3,...})
        if method == "POST" and resp["status_code"] in (200, 201):
            id_match = re.search(r'"id"\s*:\s*(\d+)', resp["body"])
            if id_match:
                # derive resource name from path: /api/products → product
                seg = [s for s in path.split("/") if s and s != "api"]
                resource_name = seg[-1].rstrip("s") if seg else "item"
                created_ids[resource_name] = id_match.group(1)

        results.append({
            "method":          method,
            "path":            path,
            "expected_status": expected,
            "actual_status":   resp["status_code"],
            "passed":          passed,
            "response_body":   resp["body"][:500],
        })

    # ── 6. Teardown ────────────────────────────────────────────────────────
    try:
        proc.terminate()
        proc.wait(timeout=10)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass

    all_passed = all(t["passed"] for t in results)
    return {
        "passed":       all_passed,
        "results":      results,
        "build_output": build_result["output"],
        "error":        None,
    }
