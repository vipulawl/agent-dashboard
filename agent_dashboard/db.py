import sqlite3
import json
import uuid
import time
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

_db_path = "agent_dashboard.db"


def set_db_path(path: str) -> None:
    global _db_path
    _db_path = path


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(_db_path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with get_conn() as conn:
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
        for migration in [
            "ALTER TABLE agent_runs ADD COLUMN metadata_json TEXT DEFAULT '{}'",
        ]:
            try:
                conn.execute(migration)
            except Exception:
                pass


# ── Read: overview ────────────────────────────────────────────────────────────

def get_kpis() -> dict:
    with get_conn() as conn:
        total = conn.execute("SELECT COUNT(*) FROM agent_runs").fetchone()[0]
        success = conn.execute("SELECT COUNT(*) FROM agent_runs WHERE status='success'").fetchone()[0]
        failed = conn.execute("SELECT COUNT(*) FROM agent_runs WHERE status='failed'").fetchone()[0]
        running = conn.execute("SELECT COUNT(*) FROM agent_runs WHERE status='running'").fetchone()[0]
        tokens = conn.execute("SELECT SUM(tokens_input + tokens_output) FROM agent_runs").fetchone()[0] or 0
        avg_dur = conn.execute(
            "SELECT AVG(duration_seconds) FROM agent_runs WHERE status IN ('success','failed')"
        ).fetchone()[0] or 0
        total_tool_calls = conn.execute("SELECT COUNT(*) FROM agent_tool_calls").fetchone()[0]
        tool_errors = conn.execute("SELECT COUNT(*) FROM agent_tool_calls WHERE success=0").fetchone()[0]
    return {
        "total_runs": total,
        "success_rate": round(success / total * 100, 1) if total > 0 else 0,
        "failed_runs": failed,
        "running_count": running,
        "total_tokens": tokens,
        "avg_duration_seconds": round(avg_dur, 1),
        "total_tool_calls": total_tool_calls,
        "tool_error_rate": round(tool_errors / total_tool_calls * 100, 1) if total_tool_calls > 0 else 0,
    }


def get_recent_runs(limit: int = 10) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM agent_runs ORDER BY started_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]


def get_timeline(days: int = 7) -> list[dict]:
    result = []
    today = datetime.utcnow().date()
    with get_conn() as conn:
        for i in range(days - 1, -1, -1):
            d = (today - timedelta(days=i)).isoformat()
            row = conn.execute("""
                SELECT
                    SUM(CASE WHEN status='success' THEN 1 ELSE 0 END) as success,
                    SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) as failed,
                    SUM(CASE WHEN status='running' THEN 1 ELSE 0 END) as running
                FROM agent_runs
                WHERE date(started_at) = ?
            """, (d,)).fetchone()
            result.append({
                "date": d[5:],  # MM-DD
                "success": row["success"] or 0,
                "failed": row["failed"] or 0,
                "running": row["running"] or 0,
            })
    return result


def get_top_errors(limit: int = 5) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT error_message FROM agent_runs WHERE status='failed' AND error_message IS NOT NULL"
        ).fetchall()
    patterns = [r["error_message"].split("\n")[0][:120] for r in rows]
    counter = Counter(patterns)
    return [{"pattern": p, "count": c} for p, c in counter.most_common(limit)]


def get_agent_stats() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT
                agent_name,
                COUNT(*) as total_runs,
                SUM(CASE WHEN status='success' THEN 1 ELSE 0 END) as successes,
                SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) as failures,
                ROUND(AVG(duration_seconds), 1) as avg_duration_seconds,
                ROUND(AVG(iterations), 1) as avg_iterations,
                SUM(tokens_input) as total_tokens_input,
                SUM(tokens_output) as total_tokens_output
            FROM agent_runs
            WHERE status IN ('success', 'failed')
            GROUP BY agent_name
            ORDER BY total_runs DESC
        """).fetchall()
        return [dict(r) for r in rows]


# ── Read: runs list ────────────────────────────────────────────────────────────

def get_runs(page: int = 1, limit: int = 20, agent: str = "",
             status: str = "", search: str = "") -> dict:
    offset = (page - 1) * limit
    clauses, params = [], []
    if agent:
        clauses.append("agent_name = ?")
        params.append(agent)
    if status:
        clauses.append("status = ?")
        params.append(status)
    if search:
        clauses.append("(run_id LIKE ? OR agent_name LIKE ? OR topic_title LIKE ? OR run_summary LIKE ?)")
        s = f"%{search}%"
        params.extend([s, s, s, s])
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    with get_conn() as conn:
        total = conn.execute(f"SELECT COUNT(*) FROM agent_runs {where}", params).fetchone()[0]
        rows = conn.execute(
            f"SELECT * FROM agent_runs {where} ORDER BY started_at DESC LIMIT ? OFFSET ?",
            [*params, limit, offset],
        ).fetchall()
    return {"runs": [dict(r) for r in rows], "total": total, "page": page}


def get_run_by_id(run_id: str) -> dict | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM agent_runs WHERE run_id = ?", (run_id,)).fetchone()
        return dict(row) if row else None


# ── Read: run detail ──────────────────────────────────────────────────────────

def get_iterations_for_run(run_id: str) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM agent_iterations WHERE run_id = ? ORDER BY iteration_num",
            (run_id,),
        ).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        d["tool_names"] = json.loads(d.get("tool_names_json") or "[]")
        result.append(d)
    return result


def get_tools_for_run(run_id: str) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM agent_tool_calls WHERE run_id = ? ORDER BY seq_num",
            (run_id,),
        ).fetchall()
        return [dict(r) for r in rows]


# ── Read: failures ────────────────────────────────────────────────────────────

def get_failures() -> dict:
    with get_conn() as conn:
        runs = conn.execute(
            "SELECT * FROM agent_runs WHERE status='failed' ORDER BY started_at DESC LIMIT 100"
        ).fetchall()
    runs_list = [dict(r) for r in runs]
    errors = [r["error_message"] or "Unknown error" for r in runs_list]
    patterns = [e.split("\n")[0][:120] for e in errors]
    error_groups = dict(Counter(patterns).most_common(10))
    return {"runs": runs_list, "error_groups": error_groups}


# ── Read: tool analytics ──────────────────────────────────────────────────────

def get_tool_stats() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT
                tool_name,
                COUNT(*) as total_calls,
                SUM(CASE WHEN success=0 THEN 1 ELSE 0 END) as errors,
                ROUND(AVG(duration_ms), 0) as avg_duration_ms
            FROM agent_tool_calls
            GROUP BY tool_name
            ORDER BY total_calls DESC
            LIMIT 60
        """).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            qrows = conn.execute("""
                SELECT quality_signal, COUNT(*) as cnt
                FROM agent_tool_calls WHERE tool_name = ?
                GROUP BY quality_signal
            """, (d["tool_name"],)).fetchall()
            d["quality_breakdown"] = {q["quality_signal"]: q["cnt"] for q in qrows}
            d["error_rate"] = round(d["errors"] / d["total_calls"], 3) if d["total_calls"] else 0
            result.append(d)
    return result


def get_agent_names() -> list[str]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT DISTINCT agent_name FROM agent_runs ORDER BY agent_name"
        ).fetchall()
        return [r["agent_name"] for r in rows]


# ── Write: SDK operations ─────────────────────────────────────────────────────

def create_agent_run(run_id: str, agent_name: str, started_at: str,
                     topic_id: int = None, topic_title: str = None,
                     trigger: str = "manual", metadata: dict = None) -> None:
    with get_conn() as conn:
        conn.execute(
            """INSERT OR IGNORE INTO agent_runs
               (run_id, agent_name, started_at, topic_id, topic_title, trigger, metadata_json)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (run_id, agent_name, started_at, topic_id, topic_title, trigger,
             json.dumps(metadata or {})),
        )


def finish_agent_run(run_id: str, status: str, finished_at: str,
                     duration_seconds: float, iterations: int,
                     tokens_input: int, tokens_output: int,
                     error_message: str = None, run_summary: str = None) -> None:
    with get_conn() as conn:
        conn.execute(
            """UPDATE agent_runs SET
               status=?, finished_at=?, duration_seconds=?, iterations=?,
               tokens_input=?, tokens_output=?, error_message=?, run_summary=?
               WHERE run_id=?""",
            (status, finished_at, duration_seconds, iterations,
             tokens_input, tokens_output, error_message, run_summary, run_id),
        )


def log_tool_call(run_id: str, seq_num: int, tool_name: str,
                  inputs_json: str, result_json: str, result_preview: str,
                  success: bool, error_message: str | None, started_at: str,
                  duration_ms: int, quality_signal: str = "ok",
                  iteration_num: int = 0) -> None:
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO agent_tool_calls
               (run_id, seq_num, tool_name, inputs_json, result_json,
                result_preview, success, error_message, started_at, duration_ms,
                quality_signal, iteration_num)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (run_id, seq_num, tool_name, inputs_json, result_json,
             result_preview, int(success), error_message, started_at, duration_ms,
             quality_signal, iteration_num),
        )


def log_agent_iteration(run_id: str, iteration_num: int, tokens_input: int,
                        tokens_output: int, stop_reason: str, assistant_preview: str,
                        tool_names: list, started_at: str, duration_ms: int) -> None:
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO agent_iterations
               (run_id, iteration_num, tokens_input, tokens_output, stop_reason,
                assistant_preview, tool_names_json, started_at, duration_ms)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (run_id, iteration_num, tokens_input, tokens_output, stop_reason,
             assistant_preview, json.dumps(tool_names), started_at, duration_ms),
        )
