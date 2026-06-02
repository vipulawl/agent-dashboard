# Agent Dashboard

Self-hosted observability dashboard for agentic flows. Captures every LLM turn, every tool call, every failure with full inputs/outputs — displayed in a searchable, real-time web UI.

```
Overview → All Runs → Run Detail (iteration timeline + tool call table)
         → Failures & Skips  → Tool Analytics
```

---

## Quickstart — plug into any Anthropic agent in 3 lines

```bash
pip install llm-agent-dashboard
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
pip install llm-agent-dashboard                    # core
pip install "llm-agent-dashboard[anthropic]"       # + auto-instrumented Anthropic client
```

**From source** (for local development):

```bash
cd ~/Projects/agent-dashboard
python -m venv .venv
source .venv/bin/activate
pip install -e ".[anthropic]"   # or: pip install llm-agent-dashboard[anthropic]
```

---

## Wiring up an agent that runs on GitHub Actions

Three steps. Steps 1–2 happen in your **agent's repo**. Step 3 happens locally after a run.

---

### Step 1 — Install and instrument your agent

Open your agent's main Python file. Import `RunContext` and wrap your existing agent loop with it. You only need to add lines — do not change your existing tool-calling or LLM logic.

Add to your `requirements.txt`:

```
llm-agent-dashboard[anthropic]
```

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

### Step 2 — Add the reusable persist action to your workflow

At the end of your job in `.github/workflows/your-workflow.yml`, replace the multi-line bash persist block with one line:

```yaml
- uses: vipulawl/agent-dashboard/.github/actions/persist-db@main
  if: always()
```

That's it. Full minimal workflow:

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
        run: python main.py

      - uses: vipulawl/agent-dashboard/.github/actions/persist-db@main
        if: always()
```

Optional inputs (all have defaults):

```yaml
- uses: vipulawl/agent-dashboard/.github/actions/persist-db@main
  if: always()
  with:
    db_path: agent_runs.db                        # default
    commit_message: "chore: persist run data [skip ci]"   # default
```

---

### Step 3 — Pull and open the dashboard in one command

```bash
agent-dashboard pull-and-serve --repo ~/your-agent-project --db agent_runs.db
```

Open `http://localhost:7777`.

To keep it live — auto-pull every 30 seconds while the dashboard is open:

```bash
agent-dashboard pull-and-serve --repo ~/your-agent-project --db agent_runs.db --interval 30
```

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

After `pip install llm-agent-dashboard` the `agent-dashboard` command is available globally:

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
├── pyproject.toml             # Package metadata — pip install llm-agent-dashboard
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

These are equivalent to:

```bash
agent-dashboard serve --db ~/blogging-agent/blogging_agent.db
agent-dashboard pull-and-serve --repo ~/blogging-agent --db blogging_agent.db
agent-dashboard pull-and-serve --repo ~/blogging-agent --db blogging_agent.db --interval 30
agent-dashboard serve
```
