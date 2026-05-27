"""
Drop-in RunContext SDK for any agentic flow.

Usage:
    from agent_dashboard.sdk import RunContext

    with RunContext("my_agent") as ctx:
        result = my_tool(args)
        ctx.log_tool_call("my_tool", args, result, duration_ms=120)
        # iterations are logged automatically when you call log_iteration()

Compatible with blogging-agent's run_logger.RunContext interface.
"""
import json
import os
import time
import uuid
from datetime import datetime
from pathlib import Path

from agent_dashboard import db as _db

_CONTENT_KEYS = {"content", "original_content", "refreshed_content"}


def _detect_trigger() -> str:
    return "actions" if os.getenv("GITHUB_ACTIONS") else "manual"


def _sanitize_inputs(inputs: dict) -> dict:
    result = {}
    for k, v in inputs.items():
        v_str = str(v)
        if k in _CONTENT_KEYS and len(v_str) > 400:
            result[k] = f"{v_str[:400]}…  [{len(v_str)} chars total]"
        else:
            result[k] = v
    return result


def _sanitize_result(result) -> object:
    if not isinstance(result, dict):
        return result
    out = {}
    for k, v in result.items():
        v_str = str(v)
        if k in _CONTENT_KEYS and len(v_str) > 200:
            out[k] = f"{v_str[:200]}…  [{len(v_str)} chars total]"
        else:
            out[k] = v
    return out


def _result_preview(result) -> str:
    if result is None:
        return "null"
    if isinstance(result, dict):
        if "error" in result:
            return f"ERROR: {result['error'][:120]}"
        if result.get("skipped"):
            return f"SKIPPED: {result.get('reason', '')[:100]}"
        parts = []
        for k, v in list(result.items())[:4]:
            if k in _CONTENT_KEYS:
                parts.append(f"{k}=[{len(str(v))} chars]")
            elif isinstance(v, list):
                parts.append(f"{k}=[{len(v)} items]")
            else:
                parts.append(f"{k}={repr(str(v))[:40]}")
        return ", ".join(parts) or "{}"
    if isinstance(result, list):
        if not result:
            return "[] (empty)"
        if isinstance(result[0], dict) and "error" in result[0]:
            return f"ERROR: {result[0]['error'][:100]}"
        return f"[{len(result)} items]"
    return str(result)[:120]


def _quality_signal(tool_name: str, inputs: dict, result) -> str:
    if isinstance(result, dict) and "error" in result:
        return "error"
    if isinstance(result, list) and result and isinstance(result[0], dict) and "error" in result[0]:
        return "error"
    if isinstance(result, dict) and result.get("skipped"):
        return "skipped"
    _gsc_ga4 = {"get_gsc_queries", "get_gsc_rising", "get_ga4_top_pages", "get_ga4_declining"}
    if isinstance(result, list) and len(result) == 0:
        return "no_data" if tool_name in _gsc_ga4 else "empty"
    if tool_name in ("web_search", "analyze_serp", "discover_keywords", "get_google_trends"):
        if isinstance(result, list) and len(result) < 3:
            return "sparse"
    if tool_name == "check_competitor_new_posts":
        if isinstance(result, dict) and result.get("new_count", 1) == 0:
            return "no_new"
    return "ok"


class RunContext:
    """
    Context manager that wraps one agent run and logs every tool call and LLM turn.

    Args:
        agent_name:   Short identifier for the agent (e.g. "research", "writer").
        db_path:      SQLite file path. Defaults to "agent_dashboard.db" in cwd.
                      Pass the blogging-agent's path to use its DB directly.
        topic_id:     Optional topic ID for correlation with blogging-agent tables.
        topic_title:  Human-readable topic label for display in the dashboard.
        trigger:      "manual" or "actions" (auto-detected from GITHUB_ACTIONS env).
        metadata:     Arbitrary dict stored as JSON for custom fields.
    """

    def __init__(self, agent_name: str, db_path: str = None,
                 topic_id: int = None, topic_title: str = None,
                 trigger: str = None, metadata: dict = None):
        self.run_id = uuid.uuid4().hex[:12]
        self.agent_name = agent_name
        self.topic_id = topic_id
        self.topic_title = topic_title
        self.trigger = trigger or _detect_trigger()
        self.metadata = metadata or {}
        self._db_path = db_path
        self._started_at = datetime.now().isoformat()
        self._start_mono = time.monotonic()
        self._iterations = 0
        self._tokens_input = 0
        self._tokens_output = 0
        self._seq = 0
        self._error: str | None = None

    def __enter__(self) -> "RunContext":
        if self._db_path:
            _db.set_db_path(self._db_path)
        _db.init_db()
        _db.create_agent_run(
            run_id=self.run_id,
            agent_name=self.agent_name,
            started_at=self._started_at,
            topic_id=self.topic_id,
            topic_title=self.topic_title,
            trigger=self.trigger,
            metadata=self.metadata,
        )
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        if exc_type is not None:
            self._error = f"{exc_type.__name__}: {exc_val}"
        status = "failed" if self._error else "success"
        _db.finish_agent_run(
            run_id=self.run_id,
            status=status,
            finished_at=datetime.now().isoformat(),
            duration_seconds=round(time.monotonic() - self._start_mono, 2),
            iterations=self._iterations,
            tokens_input=self._tokens_input,
            tokens_output=self._tokens_output,
            error_message=self._error,
        )
        return False

    def mark_failed(self, error: str) -> None:
        self._error = error

    def increment_iteration(self) -> None:
        self._iterations += 1

    def add_tokens(self, inp: int, out: int) -> None:
        self._tokens_input += inp
        self._tokens_output += out

    def log_iteration(self, tokens_input: int, tokens_output: int,
                      stop_reason: str, assistant_preview: str,
                      tool_names: list, started_at: str, duration_ms: int) -> None:
        _db.log_agent_iteration(
            run_id=self.run_id,
            iteration_num=self._iterations,
            tokens_input=tokens_input,
            tokens_output=tokens_output,
            stop_reason=stop_reason,
            assistant_preview=assistant_preview,
            tool_names=tool_names,
            started_at=started_at,
            duration_ms=duration_ms,
        )

    def log_tool_call(self, tool_name: str, inputs: dict, result,
                      duration_ms: int, error: str = None) -> None:
        self._seq += 1
        success = error is None and not (isinstance(result, dict) and "error" in result)
        if not success and error is None and isinstance(result, dict):
            error = result.get("error")
        signal = _quality_signal(tool_name, inputs, result)
        if not success:
            signal = "error"
        inputs_safe = _sanitize_inputs(inputs)
        result_safe = _sanitize_result(result)
        _db.log_tool_call(
            run_id=self.run_id,
            seq_num=self._seq,
            tool_name=tool_name,
            inputs_json=json.dumps(inputs_safe, default=str)[:6000],
            result_json=json.dumps(result_safe, default=str)[:4000],
            result_preview=_result_preview(result)[:300],
            success=success,
            error_message=error,
            started_at=datetime.now().isoformat(),
            duration_ms=duration_ms,
            quality_signal=signal,
            iteration_num=self._iterations,
        )
