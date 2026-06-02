import argparse
import subprocess
import sys
import threading
import time
from pathlib import Path


def _do_pull(repo_dir: str, db_path: str) -> None:
    subprocess.run(
        ["git", "-C", repo_dir, "checkout", db_path],
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", repo_dir, "pull", "--rebase", "-q"],
        capture_output=True,
    )


def _pull_loop(repo_dir: str, db_path: str, interval: int) -> None:
    while True:
        time.sleep(interval)
        _do_pull(repo_dir, db_path)


def main():
    p = argparse.ArgumentParser(
        description="Agent Dashboard — self-hosted observability for agentic flows"
    )
    sub = p.add_subparsers(dest="cmd")

    # ── serve ──────────────────────────────────────────────────────────────────
    srv = sub.add_parser("serve", help="Start the dashboard web server")
    srv.add_argument("--db", default="agent_dashboard.db",
                     help="SQLite database path (default: ./agent_dashboard.db)")
    srv.add_argument("--port", type=int, default=7777, help="Port (default: 7777)")
    srv.add_argument("--host", default="127.0.0.1", help="Host (default: 127.0.0.1)")

    # ── pull-and-serve ─────────────────────────────────────────────────────────
    ps = sub.add_parser(
        "pull-and-serve",
        help="Git-pull the agent repo to get the latest DB, then start the dashboard",
    )
    ps.add_argument(
        "--repo", required=True,
        help="Local path to the git repo that contains the DB (e.g. ~/my-agent)",
    )
    ps.add_argument(
        "--db", required=True,
        help="Path to the SQLite DB file — absolute, or relative to --repo",
    )
    ps.add_argument("--port", type=int, default=7777, help="Port (default: 7777)")
    ps.add_argument("--host", default="127.0.0.1", help="Host (default: 127.0.0.1)")
    ps.add_argument(
        "--interval", type=int, default=0, metavar="SECONDS",
        help="Re-pull every N seconds in the background (0 = pull once then serve)",
    )

    args = p.parse_args()

    # ── serve handler ──────────────────────────────────────────────────────────
    if args.cmd == "serve":
        db_path = str(Path(args.db).expanduser().resolve())
        from agent_dashboard.db import set_db_path, init_db
        set_db_path(db_path)
        init_db()
        from agent_dashboard.api import app
        import uvicorn
        print(f"\n  Agent Dashboard  →  http://{args.host}:{args.port}")
        print(f"  Database         →  {db_path}\n")
        uvicorn.run(app, host=args.host, port=args.port, log_level="warning")

    # ── pull-and-serve handler ─────────────────────────────────────────────────
    elif args.cmd == "pull-and-serve":
        repo_dir = str(Path(args.repo).expanduser().resolve())
        db_path_raw = args.db

        # resolve DB path: absolute as-is, relative → relative to repo
        db_p = Path(db_path_raw).expanduser()
        if not db_p.is_absolute():
            db_p = Path(repo_dir) / db_p
        db_path = str(db_p.resolve())

        # path of the DB relative to the repo root (for git checkout)
        try:
            db_rel = str(db_p.resolve().relative_to(repo_dir))
        except ValueError:
            db_rel = db_path  # absolute path outside repo — git checkout will no-op

        print(f"\n  Pulling {repo_dir} …")
        _do_pull(repo_dir, db_rel)
        print(f"  Done. Watching {db_path}")

        if args.interval > 0:
            print(f"  Auto-pulling every {args.interval}s in background\n")
            t = threading.Thread(
                target=_pull_loop,
                args=(repo_dir, db_rel, args.interval),
                daemon=True,
            )
            t.start()
        else:
            print()

        from agent_dashboard.db import set_db_path, init_db
        set_db_path(db_path)
        init_db()
        from agent_dashboard.api import app
        import uvicorn
        print(f"  Agent Dashboard  →  http://{args.host}:{args.port}")
        print(f"  Database         →  {db_path}\n")
        uvicorn.run(app, host=args.host, port=args.port, log_level="warning")

    else:
        p.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
