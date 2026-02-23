"""
agents/devops_agent.py — DevOps Agent (OPT-IN)

Role: "Senior DevOps Engineer"

This agent is ONLY invoked when state.devops_mode is not None.
It is triggered by the --devops CLI flag:
  --devops docker  → Dockerfile + docker-compose.yml
  --devops k8s     → Kubernetes manifests (Deployment, Service, ConfigMap, HPA, Ingress)
  --devops all     → All of the above
  --devops         → defaults to "all"

The agent inspects state.generated_files to auto-detect the language/framework
and produces production-ready infrastructure files accordingly.

All generated files are written to state.devops_files (in memory) and flushed
to state.project_root on disk.
"""

from __future__ import annotations

import os
import re

from agents.base_agent import BaseAgent
from config import Status
from state import PipelineState
from tools.file_tools import write_file


class DevOpsAgent(BaseAgent):
    name = "DevOps"
    system_role = (
        "You are a Senior DevOps Engineer with deep expertise in Docker, Kubernetes, "
        "and cloud-native infrastructure. You produce production-ready infrastructure "
        "files that follow industry best practices:\n\n"
        "Docker:\n"
        "  - Multi-stage builds to minimise image size\n"
        "  - Non-root user for security\n"
        "  - COPY only what is needed (no COPY . .)\n"
        "  - Pin base image versions (never use 'latest')\n"
        "  - .dockerignore-aware patterns\n\n"
        "Kubernetes:\n"
        "  - Resource requests AND limits on every container\n"
        "  - Liveness and readiness probes on every Deployment\n"
        "  - ConfigMap for environment-specific config\n"
        "  - HorizontalPodAutoscaler targeting 70% CPU\n"
        "  - NGINX Ingress with TLS termination placeholder\n"
        "  - SecurityContext: runAsNonRoot, readOnlyRootFilesystem where possible\n"
        "  - Appropriate replicas (2 minimum for HA)\n\n"
        "docker-compose:\n"
        "  - Includes healthchecks\n"
        "  - Named volumes for persistent data\n"
        "  - Environment variables via .env file reference\n\n"
        "Output every file in a fenced code block, preceded by # FILE: <path>.\n"
        "Never output placeholder comments like '# TODO' — every field must have a real value or a clearly documented default."
    )

    def run(self, state: PipelineState) -> PipelineState:
        state.status = Status.DEVOPS

        mode = state.devops_mode or "all"
        lang = _detect_language(state.generated_files)

        total_tokens = 0

        if mode in ("docker", "all"):
            state, tokens = self._generate_docker(state, lang)
            total_tokens += tokens

        if mode in ("k8s", "all"):
            state, tokens = self._generate_k8s(state, lang)
            total_tokens += tokens

        # Flush all devops files to disk
        if state.project_root:
            self._flush_to_disk(state)

        state.log(
            self.name,
            tokens=total_tokens,
            notes=f"mode={mode}, lang={lang}, {len(state.devops_files)} file(s) generated",
        )
        return state

    # ── Docker generation ─────────────────────────────────────────────────

    def _generate_docker(self, state: PipelineState, lang: str) -> tuple[PipelineState, int]:
        files_summary = _summarise_files(state.generated_files)

        prompt = f"""
Generate production-ready Docker infrastructure for a {lang} backend application.

TASK: {state.task_prompt}

FILES IN THE APPLICATION:
{files_summary}

ARCHITECT'S PLAN:
{state.plan_summary}

Generate ALL of the following files:

1. Dockerfile — multi-stage build, non-root user, pinned base image version
2. docker-compose.yml — full local dev stack with healthchecks, named volumes, .env reference
3. .dockerignore — exclude all non-essential files

For each file:
# FILE: <filename>
```<lang>
<complete content>
```

Requirements:
- The app must start with a single `docker compose up` with zero manual steps
- Include a realistic HEALTHCHECK instruction
- If the app uses a database, include a db service in docker-compose.yml with correct env vars
- Document every non-obvious choice with an inline comment
"""
        response_text, tokens = self._call_llm(state, prompt)
        _parse_and_store(response_text, state.devops_files)
        return state, tokens

    # ── Kubernetes generation ─────────────────────────────────────────────

    def _generate_k8s(self, state: PipelineState, lang: str) -> tuple[PipelineState, int]:
        files_summary = _summarise_files(state.generated_files)

        # Infer app name from task prompt (slug)
        app_name = re.sub(r"[^a-z0-9]+", "-", state.task_prompt.lower())[:32].strip("-")
        if not app_name:
            app_name = "backend-app"

        prompt = f"""
Generate a complete, production-ready Kubernetes manifest set for a {lang} backend application.

APP NAME: {app_name}
TASK: {state.task_prompt}

FILES IN THE APPLICATION:
{files_summary}

Generate ALL of the following files inside the k8s/ directory:

1. k8s/namespace.yaml      — dedicated namespace for the app
2. k8s/configmap.yaml      — ConfigMap for non-secret environment config
3. k8s/deployment.yaml     — Deployment with:
                               - 2 replicas minimum (HA)
                               - resource requests + limits (CPU & memory)
                               - liveness probe (HTTP GET /health, initialDelaySeconds: 30)
                               - readiness probe (HTTP GET /ready, initialDelaySeconds: 10)
                               - securityContext: runAsNonRoot: true
                               - envFrom: configMapRef
4. k8s/service.yaml        — ClusterIP Service exposing the app port
5. k8s/ingress.yaml        — NGINX Ingress with TLS termination (cert-manager placeholder)
6. k8s/hpa.yaml            — HorizontalPodAutoscaler targeting 70% CPU, min=2, max=10

For each file:
# FILE: k8s/<filename>.yaml
```yaml
<complete content>
```

Requirements:
- Use the namespace from namespace.yaml in every manifest
- All labels must include: app, version, managed-by: be-agent-workflow
- Resource limits: realistic for a typical backend pod (e.g. 250m CPU, 256Mi memory)
- Document every non-trivial field with an inline comment
- The Ingress host should be a placeholder: {app_name}.example.com
"""
        response_text, tokens = self._call_llm(state, prompt)
        _parse_and_store(response_text, state.devops_files)
        return state, tokens

    # ── Disk flush ────────────────────────────────────────────────────────

    def _flush_to_disk(self, state: PipelineState) -> None:
        """Write all devops_files to state.project_root on disk."""
        root = state.project_root
        for rel_path, content in state.devops_files.items():
            abs_path = os.path.join(root, rel_path)
            write_file(abs_path, content)


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _detect_language(generated_files: dict[str, str]) -> str:
    """Heuristically detect the primary language from generated file extensions."""
    ext_counts: dict[str, int] = {}
    for path in generated_files:
        ext = os.path.splitext(path)[-1].lower()
        ext_counts[ext] = ext_counts.get(ext, 0) + 1

    ext_to_lang = {
        ".py":   "Python",
        ".java": "Java",
        ".ts":   "Node.js/TypeScript",
        ".js":   "Node.js/JavaScript",
        ".go":   "Go",
        ".rs":   "Rust",
        ".kt":   "Kotlin/JVM",
        ".rb":   "Ruby",
        ".cs":   "C#/.NET",
        ".php":  "PHP",
    }
    if not ext_counts:
        return "Python"  # sensible default

    dominant_ext = max(ext_counts, key=ext_counts.__getitem__)
    return ext_to_lang.get(dominant_ext, "Python")


def _summarise_files(generated_files: dict[str, str]) -> str:
    """Produce a compact file list for use in prompts (path + first line only)."""
    lines = []
    for path, content in generated_files.items():
        first_line = content.split("\n")[0][:80] if content else ""
        lines.append(f"  {path}  ({first_line}...)")
    return "\n".join(lines) or "(no files)"


def _parse_and_store(response_text: str, target: dict[str, str]) -> None:
    """
    Parse LLM response for FILE blocks and store in target dict.
    Handles: Dockerfile, docker-compose.yml, .dockerignore, k8s/*.yaml
    """
    pattern = r"#\s*FILE:\s*([^\n]+)\n```\w*\n(.*?)```"
    matches = re.findall(pattern, response_text, re.DOTALL)
    for file_path, content in matches:
        target[file_path.strip()] = content.strip()
