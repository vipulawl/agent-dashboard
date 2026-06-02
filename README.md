# Agent Dashboard

Self-hosted observability dashboard for agentic flows. Captures every LLM turn, every tool call, every failure with full inputs/outputs — displayed in a searchable, real-time web UI.

```
Overview → All Runs → Run Detail (iteration timeline + tool call table)
         → Failures & Skips  → Tool Analytics
```

---

## Quickstart — plug into any Anthropic agent in 3 lines

```bash
pip install agent-dashboard
```

```python
from agent_dashboard import RunContext
from agent_dashboard.anthropic import Anthropic   # drop-in for anthropic.Anthropic

client = Anthropic()   # same constructor args as anthropic.Anthropic()

with RunContext("my_agent", db_path="./agent_runs.db") as ctx:
    client.set_context(ctx)          # attach — all messages.create() calls auto-logged
    # ... your existing agent code, zero other changes ...
    response = client.messages.create(model=..., messages=..., tools=...)
    # tokens, stop reason, tool names, duration — all captured automatically
```

Start the dashboard:

```bash
agent-dashboard serve --db ./agent_runs.db
# → http://localhost:7777
```

---

## What it shows

- **KPI cards**: total runs, success rate, token usage, tool call error rate
- **7-day timeline chart**: stacked bar of success/failed/running per day
- **Per-run drilldown**: every LLM iteration with tokens, stop reason, tool calls used, and the full assistant text
- **Tool call inspection**: expandable inputs/results (JSON), quality signal, duration, error message
- **Failure analysis**: all failed runs grouped by error pattern
- **Tool analytics**: per-tool call counts, error rates, avg duration, quality breakdown

Auto-refreshes every 30 seconds. Live indicator for currently-running agents.

---

## Installation

**From PyPI** (recommended):

```bash
pip install agent-dashboard                    # core
pip install "agent-dashboard[anthropic]"       # + auto-instrumented Anthropic client
```

**From source** (for local development):

```bash
cd ~/Projects/agent-dashboard
python -m venv .venv
source .venv/bin/activate
pip install -e ".[anthropic]"
```

---

## Wiring up an agent that runs on GitHub Actions

This is a 4-step process. Steps 1–3 happen in your **agent's repo**. Step 4 happens locally after a run completes.

---

### Step 1 — Copy `run_context.py` into your agent repo

`run_context.py` is a single self-contained file in the root of this repo. It has no dependencies beyond the Python standard library — just `sqlite3`, `json`, `uuid`, `time`, `os`, and `datetime`.

Copy it into the root of your agent project:

```bash
cp ~/Projects/agent-dashboard/run_context.py ~/your-agent-project/
```

Commit it:

```bash
cd ~/your-agent-project
git add run_context.py
git commit -m "add: agent dashboard run_context SDK"
git push
```

---

### Step 2 — Wrap your agent loop with `RunContext`

Open your agent's main Python file. Import `RunContext` and wrap your existing agent loop with it. You only need to add lines — do not change your existing tool-calling or LLM logic.

#### Full example for an Anthropic `client.messages.create` loop

```python
import time
from datetime import datetime
from run_context import RunContext

DB_PATH = "./agent_runs.db"   # SQLite file that will be committed to git

def run_my_agent(user_prompt: str):
    with RunContext(
        agent_name="my_agent",          # short label shown in the dashboard
        db_path=DB_PATH,
        topic_title=user_prompt[:80],   # optional — human-readable label
        metadata={"model": MODEL},      # optional — any extra info
    ) as ctx:

        messages = [{"role": "user", "content": user_prompt}]

        while True:
            turn_start = time.time()

            response = client.messages.create(
                model=MODEL,
                max_tokens=4096,
                tools=tools,
                messages=messages,
            )

            # ── log the LLM turn ──────────────────────────────────────────
            text = next(
                (b.text for b in response.content if hasattr(b, "text")), ""
            )
            tool_names = [
                b.name for b in response.content if b.type == "tool_use"
            ]
            ctx.log_iteration(
                tokens_input=response.usage.input_tokens,
                tokens_output=response.usage.output_tokens,
                stop_reason=response.stop_reason,
                assistant_preview=text[:200],
                tool_names=tool_names,
                duration_ms=int((time.time() - turn_start) * 1000),
            )
            # ─────────────────────────────────────────────────────────────

            if response.stop_reason == "end_turn":
                break

            # run tool calls
            tool_results = []
            for block in response.content:
                if block.type != "tool_use":
                    continue

                # ── log each tool call ────────────────────────────────────
                t0 = time.time()
                result = execute_tool(block.name, block.input)   # YOUR existing function
                ctx.log_tool_call(
                    tool_name=block.name,
                    inputs=dict(block.input),
                    result=result,
                    duration_ms=int((time.time() - t0) * 1000),
                )
                # ─────────────────────────────────────────────────────────

                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": str(result),
                })

            messages = messages + [
                {"role": "assistant", "content": response.content},
                {"role": "user",      "content": tool_results},
            ]
```

#### What each method does

| Method | When to call | What it records |
|--------|-------------|-----------------|
| `ctx.log_iteration(...)` | Once per `client.messages.create` call | Token counts, stop reason, assistant text preview, which tools were called in this turn |
| `ctx.log_tool_call(...)` | Once per tool execution | Tool name, full inputs, full result (truncated if large), duration, success/error |
| `ctx.mark_failed(error)` | If you want to flag failure without raising | Sets run status to `failed` with your message |

The `with RunContext(...) as ctx:` block automatically:
- Creates the `agent_runs`, `agent_tool_calls`, `agent_iterations` tables in the SQLite file if they don't exist
- Writes a run record with `status='running'` at the start
- Updates it to `status='success'` or `status='failed'` (with the exception message) at the end

---

### Step 3 — Add a "Persist DB" step to your GitHub Actions workflow

At the end of your job in `.github/workflows/your-workflow.yml`, add this step **after** the step that runs your agent. The `if: always()` ensures the DB is committed even if the agent fails.

```yaml
- name: Persist agent run DB
  if: always()
  run: |
    git config user.name  "agent-bot"
    git config user.email "bot@noreply.github.com"
    git add -f agent_runs.db
    git diff --staged --quiet || git commit -m "chore: persist agent run data [skip ci]"
    git push
```

Also make sure your workflow job has write permissions. Add this at the top level of your workflow file if it isn't already there:

```yaml
permissions:
  contents: write
```

Full minimal workflow example:

```yaml
name: Run Agent

on:
  workflow_dispatch:
  schedule:
    - cron: "0 9 * * *"

permissions:
  contents: write

jobs:
  run:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
          cache: pip
          cache-dependency-path: requirements.txt

      - name: Install dependencies
        run: pip install -r requirements.txt

      - name: Run agent
        env:
          OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}
        run: python main.py   # or however you run your agent

      - name: Persist agent run DB
        if: always()
        run: |
          git config user.name  "agent-bot"
          git config user.email "bot@noreply.github.com"
          git add -f agent_runs.db
          git diff --staged --quiet || git commit -m "chore: persist agent run data [skip ci]"
          git push
```

---

### Step 4 — Pull the DB locally and open the dashboard

After a GitHub Actions run finishes, pull the committed DB and start the dashboard:

```bash
# In your agent's repo — pull the latest DB
cd ~/your-agent-project
git pull

# In the agent-dashboard repo — start the dashboard
cd ~/Projects/agent-dashboard
source .venv/bin/activate
python main.py serve --db ~/your-agent-project/agent_runs.db
```

Open `http://localhost:7777`.

Every time you want to see fresh data from a new run, just `git pull` in your agent repo and refresh the browser — no need to restart the dashboard.

---

## `RunContext` full reference

```python
from run_context import RunContext

with RunContext(
    agent_name="researcher",        # required — label shown in dashboard
    db_path="./agent_runs.db",      # SQLite path, created if missing
    topic_title="My task label",    # optional — human-readable run label
    metadata={"model": "gpt-4o"},   # optional — any JSON-serialisable dict
) as ctx:
    ...
```

### `ctx.log_iteration(...)`

```python
ctx.log_iteration(
    tokens_input=response.usage.input_tokens,   # int
    tokens_output=response.usage.output_tokens, # int
    stop_reason=response.stop_reason,           # "tool_use" | "end_turn" | "max_tokens"
    assistant_preview=text[:200],               # str — first ~200 chars of text response
    tool_names=["search", "write_file"],        # list[str] — tools called in this turn
    duration_ms=1234,                           # int — how long the LLM call took
    started_at=datetime.now().isoformat(),      # str — optional, defaults to now
)
```

### `ctx.log_tool_call(...)`

```python
ctx.log_tool_call(
    tool_name="search",             # str
    inputs={"query": "..."},        # dict — the arguments passed to the tool
    result={"results": [...]},      # any — what the tool returned
    duration_ms=320,                # int — how long the tool took
    error=None,                     # str | None — pass error string if it failed
)
```

### Other methods

```python
ctx.mark_failed("Timeout after 30s")              # flag run as failed without raising
ctx.mark_skipped("No topics ready to publish")    # record a no-op run as 'skipped'
ctx.add_tokens(inp=500, out=200)                  # manually accumulate tokens (if not using log_iteration)
```

#### Capturing silent skips — important pattern

Wrap your agent at the **outermost level**, before any conditional logic. This ensures every invocation is recorded — including runs where the agent decides there is nothing to do:

```python
with RunContext("scheduler", db_path=DB_PATH) as ctx:
    topics = get_ready_topics()
    if not topics:
        ctx.mark_skipped("No topics ready to publish")
        # returns here — run is recorded as 'skipped', visible on the dashboard
    else:
        for topic in topics:
            process(topic, ctx)
```

Without this pattern, "nothing to do" runs are invisible — you can't tell if the agent ran and skipped, or never ran at all. With it, every run shows up in the Failures & Skips page.

---

## CLI reference

After `pip install agent-dashboard` the `agent-dashboard` command is available globally:

```bash
# Serve dashboard pointing at a specific DB
agent-dashboard serve --db /path/to/agent_runs.db

# Custom port
agent-dashboard serve --db /path/to/agent_runs.db --port 8080

# Bind to all interfaces (e.g. accessible from another machine on your network)
agent-dashboard serve --db /path/to/agent_runs.db --host 0.0.0.0 --port 7777

# Fresh DB in current directory
agent-dashboard serve
```

Or keep using `python main.py serve` if running from source.

---

## File structure

```
agent-dashboard/
├── pyproject.toml             # Package metadata — pip install agent-dashboard
├── main.py                    # CLI entry point (python main.py serve)
├── run_context.py             # Standalone SDK — copy this into any agent project
├── requirements.txt
├── Makefile                   # Shortcuts for blogging-agent integration
├── agent_dashboard/
│   ├── __init__.py            # exports RunContext, set_db_path, init_db
│   ├── sdk.py                 # RunContext implementation
│   ├── anthropic.py           # Auto-instrumented Anthropic client (drop-in)
│   ├── cli.py                 # agent-dashboard CLI entry point
│   ├── db.py                  # SQLite schema + all read/write queries
│   └── api.py                 # FastAPI REST endpoints
└── static/
    └── index.html             # Single-page dashboard (Alpine.js + Chart.js + Tailwind)
```

---

## API endpoints

| Endpoint | Description |
|---|---|
| `GET /api/overview` | KPIs, 7-day timeline, recent runs, top errors |
| `GET /api/runs` | Paginated run list (filters: `agent`, `status`, `search`) |
| `GET /api/runs/{run_id}` | Single run detail |
| `GET /api/runs/{run_id}/iterations` | All LLM turns for a run |
| `GET /api/runs/{run_id}/tools` | All tool calls for a run |
| `GET /api/failures` | Failed runs grouped by error pattern |
| `GET /api/tool-stats` | Per-tool call counts, error rates, avg duration |
| `GET /api/agent-stats` | Per-agent aggregated stats |
| `GET /api/agent-names` | List of distinct agent names in the DB |

---

## Makefile shortcuts (for blogging-agent)

```bash
make blog        # serve dashboard using local blogging-agent DB
make blog-pull   # git pull blogging-agent DB first, then serve
make blog-live   # pull DB every 30s in background + serve (near-live mode)
make fresh       # serve with a brand-new empty DB
```
