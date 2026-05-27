# Agent Dashboard

Self-hosted observability dashboard for agentic flows. Captures every LLM turn, every tool call, every failure with full inputs/outputs — and displays it in a searchable, real-time web UI.

```
Overview → All Runs → Run Detail (iteration timeline + tool call table)
         → Failures  → Tool Analytics
```

---

## What it shows

- **KPI cards**: total runs, success rate, token usage, tool call error rate
- **7-day timeline chart**: stacked bar of success/failed/running per day
- **Per-run drilldown**: every LLM iteration with tokens, stop reason, tool calls used, and the full assistant text
- **Tool call inspection**: expandable inputs/results (JSON), quality signal, duration, error message
- **Failure analysis**: all failed runs grouped by error pattern
- **Tool analytics**: per-tool call counts, error rates, avg duration, quality breakdown

Auto-refreshes every 30 seconds. Live indicator for running agents.

---

## Setup

```bash
cd ~/Projects/agent-dashboard
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

---

## Usage

### Point at your blogging-agent DB (zero config)

```bash
python main.py serve --db ~/blogging-agent/blogging_agent.db
```

### Start with a fresh DB

```bash
python main.py serve
# → http://127.0.0.1:7777
```

### Custom port / public binding

```bash
python main.py serve --db ~/blogging-agent/blogging_agent.db --port 8080 --host 0.0.0.0
```

---

## Integrate into any new agent

```python
from agent_dashboard import RunContext

with RunContext("my_agent", db_path="agent_dashboard.db") as ctx:
    # Log a tool call manually
    import time
    t = time.monotonic()
    result = call_some_tool(inputs)
    ctx.log_tool_call(
        tool_name="some_tool",
        inputs=inputs,
        result=result,
        duration_ms=int((time.monotonic() - t) * 1000),
    )

    # Log an LLM turn
    ctx.add_tokens(inp=1200, out=400)
    ctx.increment_iteration()
    ctx.log_iteration(
        tokens_input=1200, tokens_output=400,
        stop_reason="tool_use",
        assistant_preview="I'll search for...",
        tool_names=["some_tool"],
        started_at=datetime.now().isoformat(),
        duration_ms=3400,
    )

    # Mark failure (optional — __exit__ catches exceptions automatically)
    # ctx.mark_failed("Connection timeout")
```

### RunContext options

| Arg | Default | Description |
|---|---|---|
| `agent_name` | required | Short identifier shown in the dashboard |
| `db_path` | `agent_dashboard.db` | SQLite file path (absolute or relative) |
| `topic_id` | None | Correlation with blogging-agent topic table |
| `topic_title` | None | Human-readable label shown in the dashboard |
| `trigger` | auto-detected | `"manual"` or `"actions"` |
| `metadata` | `{}` | Arbitrary dict stored as JSON |

---

## Drop-in for blogging-agent

The SDK is interface-compatible with `run_logger.RunContext`. To use the dashboard with the blogging-agent, just point `main.py serve` at its SQLite file — no changes to the blogging-agent needed:

```bash
python main.py serve --db ~/blogging-agent/blogging_agent.db
```

---

## File structure

```
agent-dashboard/
├── main.py                    # CLI entry point
├── requirements.txt
├── agent_dashboard/
│   ├── __init__.py            # exports RunContext, set_db_path, init_db
│   ├── sdk.py                 # RunContext — drop-in for any agent
│   ├── db.py                  # SQLite schema + all queries
│   └── api.py                 # FastAPI REST endpoints
└── static/
    └── index.html             # Single-page dashboard (Alpine.js + Chart.js)
```

---

## API endpoints

| Endpoint | Description |
|---|---|
| `GET /api/overview` | KPIs, timeline, recent runs, top errors |
| `GET /api/runs` | Paginated run list (filters: agent, status, search) |
| `GET /api/runs/{run_id}` | Single run detail |
| `GET /api/runs/{run_id}/iterations` | LLM turns for a run |
| `GET /api/runs/{run_id}/tools` | Tool calls for a run |
| `GET /api/failures` | All failed runs + error grouping |
| `GET /api/tool-stats` | Per-tool analytics |
| `GET /api/agent-stats` | Per-agent analytics |
