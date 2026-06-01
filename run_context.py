"""
Standalone RunContext — drop this single file into any Python agent project.

Usage:
    from run_context import RunContext

    with RunContext("my_agent", db_path="./agent_runs.db") as ctx:
        for turn in agent_loop():
            result = call_tool(turn.tool_name, turn.inputs)
            ctx.log_tool_call(turn.tool_name, turn.inputs, result, duration_ms=turn.ms)
            ctx.log_iteration(
                tokens_input=turn.usage.input_tokens,
                tokens_output=turn.usage.output_tokens,
                stop_reason=turn.stop_reason,
                assistant_preview=turn.text[:200],
                tool_names=[turn.tool_name],
                started_at=turn.started_at,
                duration_ms=turn.ms,
            )

Then point the dashboard at the same DB:
    python main.py serve --db ./agent_runs.db

No other dependencies — stdlib only (sqlite3, json, uuid, time, os, datetime).
"""

import json
import os
import sqlite3
import time
import uuid
from datetime import datetime
from pathlib import Path


# ── DB helpers ────────────────────────────────────────────────────────────────

def _conn(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _init_db(db_path: str) -> None:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    with _conn(db_path) as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS agent_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT UNIQUE NOT NULL,
                agent_name TEXT NOT NULL,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                duration_seconds REAL,
                status TEXT DEFAULT 'running',
                error_message TEXT,
                iterations INTEGER DEFAULT 0,
                tokens_input INTEGER DEFAULT 0,
                tokens_output INTEGER DEFAULT 0,
                topic_id INTEGER,
                topic_title TEXT,
                trigger TEXT DEFAULT 'manual',
                run_summary TEXT,
                metadata_json TEXT DEFAULT '{}'
            );
            CREATE TABLE IF NOT EXISTS agent_tool_calls (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                seq_num INTEGER NOT NULL,
                tool_name TEXT NOT NULL,
                inputs_json TEXT,
                result_json TEXT,
                result_preview TEXT,
                success INTEGER DEFAULT 1,
                error_message TEXT,
                started_at TEXT,
                duration_ms INTEGER DEFAULT 0,
                quality_signal TEXT DEFAULT 'ok',
                iteration_num INTEGER DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS agent_iterations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                iteration_num INTEGER NOT NULL,
                tokens_input INTEGER DEFAULT 0,
                tokens_output INTEGER DEFAULT 0,
                stop_reason TEXT,
                assistant_preview TEXT,
                tool_names_json TEXT DEFAULT '[]',
                started_at TEXT,
                duration_ms INTEGER DEFAULT 0
            );
        """)


def _result_preview(result) -> str:
    if result is None:
        return "null"
    if isinstance(result, dict):
        if "error" in result:
            return f"ERROR: {result['error'][:120]}"
        parts = []
        for k, v in list(result.items())[:4]:
            if isinstance(v, list):
                parts.append(f"{k}=[{len(v)} items]")
            elif isinstance(v, str) and len(v) > 60:
                parts.append(f"{k}=[{len(v)} chars]")
            else:
                parts.append(f"{k}={repr(str(v))[:40]}")
        return ", ".join(parts) or "{}"
    if isinstance(result, list):
        return f"[] (empty)" if not result else f"[{len(result)} items]"
    return str(result)[:120]


# ── RunContext ────────────────────────────────────────────────────────────────

class RunContext:
    """
    Wraps one agent run. Logs every tool call and LLM turn to SQLite.

    Args:
        agent_name:   Short label for this agent (e.g. "researcher", "writer").
        db_path:      Path to the SQLite file. Created automatically if missing.
                      Defaults to ./agent_runs.db in the current directory.
        topic_title:  Optional human-readable label shown in the dashboard.
        metadata:     Any extra dict you want stored (e.g. {"model": "claude-3-5"}).
    """

    def __init__(
        self,
        agent_name: str,
        db_path: str = "./agent_runs.db",
        topic_title: str = None,
        metadata: dict = None,
    ):
        self.run_id = uuid.uuid4().hex[:12]
        self.agent_name = agent_name
        self.topic_title = topic_title
        self.metadata = metadata or {}
        self.trigger = "actions" if os.getenv("GITHUB_ACTIONS") else "manual"
        self._db_path = str(Path(db_path).expanduser().resolve())
        self._started_at = datetime.now().isoformat()
        self._start_mono = time.monotonic()
        self._iterations = 0
        self._tokens_input = 0
        self._tokens_output = 0
        self._seq = 0
        self._error: str | None = None

    def __enter__(self) -> "RunContext":
        _init_db(self._db_path)
        with _conn(self._db_path) as conn:
            conn.execute(
                """INSERT OR IGNORE INTO agent_runs
                   (run_id, agent_name, started_at, topic_title, trigger, metadata_json)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (self.run_id, self.agent_name, self._started_at,
                 self.topic_title, self.trigger, json.dumps(self.metadata)),
            )
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        if exc_type is not None:
            self._error = f"{exc_type.__name__}: {exc_val}"
        status = "failed" if self._error else "success"
        with _conn(self._db_path) as conn:
            conn.execute(
                """UPDATE agent_runs SET
                   status=?, finished_at=?, duration_seconds=?, iterations=?,
                   tokens_input=?, tokens_output=?, error_message=?
                   WHERE run_id=?""",
                (status, datetime.now().isoformat(),
                 round(time.monotonic() - self._start_mono, 2),
                 self._iterations, self._tokens_input, self._tokens_output,
                 self._error, self.run_id),
            )
        return False

    def mark_failed(self, error: str) -> None:
        """Call this to mark the run as failed without raising an exception."""
        self._error = error

    def add_tokens(self, inp: int, out: int) -> None:
        """Accumulate token counts for the run total."""
        self._tokens_input += inp
        self._tokens_output += out

    def log_tool_call(
        self,
        tool_name: str,
        inputs: dict,
        result,
        duration_ms: int = 0,
        error: str = None,
    ) -> None:
        """
        Log a single tool call.

        Args:
            tool_name:   Name of the tool/function called.
            inputs:      Dict of arguments passed to the tool.
            result:      Whatever the tool returned (dict, list, str, None).
            duration_ms: How long the call took.
            error:       Error string if the call failed (or None).
        """
        self._seq += 1
        success = error is None and not (isinstance(result, dict) and "error" in result)
        if not success and error is None and isinstance(result, dict):
            error = result.get("error")
        with _conn(self._db_path) as conn:
            conn.execute(
                """INSERT INTO agent_tool_calls
                   (run_id, seq_num, tool_name, inputs_json, result_json,
                    result_preview, success, error_message, started_at,
                    duration_ms, quality_signal, iteration_num)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (self.run_id, self._seq, tool_name,
                 json.dumps(inputs, default=str)[:6000],
                 json.dumps(result, default=str)[:4000],
                 _result_preview(result)[:300],
                 int(success), error,
                 datetime.now().isoformat(), duration_ms,
                 "error" if not success else "ok",
                 self._iterations),
            )

    def log_iteration(
        self,
        tokens_input: int,
        tokens_output: int,
        stop_reason: str,
        assistant_preview: str,
        tool_names: list,
        started_at: str = None,
        duration_ms: int = 0,
    ) -> None:
        """
        Log one LLM turn (one call to client.messages.create or equivalent).

        Args:
            tokens_input:      Input tokens from usage object.
            tokens_output:     Output tokens from usage object.
            stop_reason:       e.g. "tool_use", "end_turn", "max_tokens".
            assistant_preview: First ~200 chars of the assistant's text response.
            tool_names:        List of tool names called in this turn.
            started_at:        ISO timestamp when this turn started (optional).
            duration_ms:       How long the LLM call took.
        """
        self._iterations += 1
        self._tokens_input += tokens_input
        self._tokens_output += tokens_output
        with _conn(self._db_path) as conn:
            conn.execute(
                """INSERT INTO agent_iterations
                   (run_id, iteration_num, tokens_input, tokens_output,
                    stop_reason, assistant_preview, tool_names_json,
                    started_at, duration_ms)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (self.run_id, self._iterations, tokens_input, tokens_output,
                 stop_reason, assistant_preview[:500] if assistant_preview else "",
                 json.dumps(tool_names),
                 started_at or datetime.now().isoformat(), duration_ms),
            )
