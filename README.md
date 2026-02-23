# BE Multi-Agent Workflow

A **rule-based multi-agent pipeline** for backend code creation, updates, and modifications — powered by any LLM (Gemini, OpenAI, Anthropic, Ollama).

---

## Architecture

```
Task Prompt
     ↓
🏛️ Architect  →  👤 Human Approval Gate
                         ↓
                   💻 Coder  →  🔍 Reviewer
                                      ↓
                               🧪 Tester ──FAIL──→ 🐛 Debugger → 🔄 retry
                                      │
                                    PASS
                                      ↓
                               📝 Writer  →  ✅ Done
```

### Agents

| Agent | Role |
|-------|------|
| 🏛️ **Architect** | Analyses the task + project; produces a structured implementation plan |
| 💻 **Coder** | Generates complete source files from the plan (or applies Debugger fixes) |
| 🔍 **Reviewer** | Code review — correctness, security, style, user-rule compliance |
| 🧪 **Tester** | Writes pytest suites, flushes files to disk, runs tests |
| 🐛 **Debugger** | Diagnoses failures, emits precise fix instructions for Coder |
| 📝 **Writer** | Updates docstrings, README, CHANGELOG; optional git commit |

### Key Design Principles

- **Orchestrator is rule-based** — pure `if/while` logic, no LLM involved in routing
- **User rules injected into every agent** — define once in `rules/RULES.md`, enforced everywhere
- **Checkpointing after every agent** — crash-safe; resume with `--resume <run-id>`
- **MCP for external tools** — filesystem, knowledge base, Postgres, GitHub, SonarQube (pluggable)
- **LLM-agnostic** — switch providers via one env var, no code changes

---

## Quickstart

### 1. Install

```bash
cd be-agent-workflow
py -3 -m pip install -r requirements.txt
```

### 2. Set your API key

```bash
# Windows PowerShell
$env:GEMINI_API_KEY = "your-key-here"

# macOS / Linux
export GEMINI_API_KEY="your-key-here"
```

### 3. Run

```bash
# New task
py -3 main.py --task "Add POST /login endpoint" --project-root ./my_api

# With a specific coding rules profile
py -3 main.py --task "Add payment service" --rules rules/spring-boot.md --project-root ./billing

# Resume a crashed run
py -3 main.py --resume <run-id>

# List all past runs
py -3 main.py --list-runs
```

---

## Switching LLM Providers

Change one environment variable — no code changes needed:

```bash
# OpenAI
$env:LLM_PROVIDER = "openai"
$env:LLM_MODEL    = "gpt-4o"
$env:OPENAI_API_KEY = "sk-..."

# Anthropic Claude
$env:LLM_PROVIDER = "anthropic"
$env:LLM_MODEL    = "claude-3-5-sonnet-20241022"
$env:ANTHROPIC_API_KEY = "..."

# Ollama (local — free)
$env:LLM_PROVIDER = "ollama"
$env:LLM_MODEL    = "llama3.1"

# Any OpenAI-compatible API (Groq, Together, etc.)
$env:LLM_PROVIDER = "openai_compat"
$env:LLM_BASE_URL = "https://api.groq.com/openai/v1"
$env:LLM_MODEL    = "llama-3.1-70b-versatile"
$env:OPENAI_API_KEY = "gsk_..."
```

---

## User Coding Rules

Edit `rules/RULES.md` to define your team's standards.
Rules are **injected into every agent's system prompt** before each run.

```bash
# Use default rules
py -3 main.py --task "..."

# Use a specific profile
py -3 main.py --task "..." --rules rules/spring-boot.md
```

Available profiles: `rules/RULES.md` · `rules/spring-boot.md` · `rules/fastapi.md` · `rules/security-strict.md`

---

## Project Structure

```
be-agent-workflow/
├── main.py                    ← CLI entry point
├── orchestrator.py            ← Pipeline controller (no LLM)
├── state.py                   ← PipelineState shared by all agents
├── config.py                  ← All settings & env vars
│
├── agents/
│   ├── base_agent.py          ← Gemini/LLM call + rules injection (base class)
│   ├── architect_agent.py
│   ├── coder_agent.py
│   ├── reviewer_agent.py
│   ├── tester_agent.py
│   ├── debugger_agent.py
│   └── writer_agent.py
│
├── tools/
│   ├── llm_provider.py        ← Pluggable LLM factory
│   ├── file_tools.py          ← read / write / list / tree
│   ├── shell_tools.py         ← run commands, pytest runner
│   ├── git_tools.py           ← diff, stage, commit
│   ├── checkpoint_tools.py    ← crash-safe state persistence
│   ├── rules_loader.py        ← load RULES.md
│   └── mcp_client.py          ← MCP client factory (per-agent access)
│
├── rules/                     ← Coding standards profiles
├── mcp/agent_mcp_config.json  ← Per-agent MCP server permissions
├── docs/                      ← Developer guides
└── .workflow/                 ← Checkpoints (gitignored)
```

---

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `GEMINI_API_KEY` | *(required for gemini)* | Gemini API key |
| `OPENAI_API_KEY` | *(required for openai)* | OpenAI API key |
| `ANTHROPIC_API_KEY` | *(required for anthropic)* | Anthropic API key |
| `LLM_PROVIDER` | `gemini` | `gemini` \| `openai` \| `anthropic` \| `ollama` \| `openai_compat` |
| `LLM_MODEL` | `gemini-2.0-flash` | Model name for the chosen provider |
| `LLM_BASE_URL` | — | Base URL for `openai_compat` / Ollama |
| `LLM_TEMPERATURE` | `0.2` | Generation temperature |
| `LLM_MAX_TOKENS` | `8192` | Max output tokens |
| `MAX_DEBUG_RETRIES` | `3` | Max Debugger→Coder retry cycles |
| `MAX_REVIEW_RETRIES` | `1` | Max Reviewer→Coder retry cycles |

---

## Docs

| Guide | Description |
|-------|-------------|
| [`docs/code_guide.md`](docs/code_guide.md) | Full code walkthrough — what every file does |
| [`docs/mcp_integration_guide.md`](docs/mcp_integration_guide.md) | How to connect real MCP servers & knowledge bases |
