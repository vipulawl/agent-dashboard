import argparse
import sys
from pathlib import Path


def main():
    p = argparse.ArgumentParser(
        description="Agent Dashboard — self-hosted observability for agentic flows"
    )
    sub = p.add_subparsers(dest="cmd")

    srv = sub.add_parser("serve", help="Start the dashboard web server")
    srv.add_argument("--db", default="agent_dashboard.db",
                     help="SQLite database path (default: ./agent_dashboard.db)")
    srv.add_argument("--port", type=int, default=7777, help="Port (default: 7777)")
    srv.add_argument("--host", default="127.0.0.1", help="Host (default: 127.0.0.1)")

    args = p.parse_args()

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
    else:
        p.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
