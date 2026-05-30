.PHONY: blog blog-pull blog-live fresh

# Watch live local daemon runs
blog:
	source .venv/bin/activate && python main.py serve --db ~/blogging-agent/blogging_agent.db

# Pull latest from GitHub Actions first, then open dashboard
blog-pull:
	cd ~/blogging-agent && git checkout blogging_agent.db 2>/dev/null || true && git pull --rebase
	source .venv/bin/activate && python main.py serve --db ~/blogging-agent/blogging_agent.db

# Near-live mode: pulls DB from GitHub every 30s in background, dashboard auto-refreshes
# Requires mid-run DB commits in your workflow YAMLs (see README)
blog-live:
	@echo "Starting git puller (every 30s) + dashboard..."
	@while true; do \
		cd ~/blogging-agent && git checkout blogging_agent.db 2>/dev/null; git pull --rebase -q 2>/dev/null; \
		sleep 30; \
	done &
	@echo "Puller PID: $$!"
	source .venv/bin/activate && python main.py serve --db ~/blogging-agent/blogging_agent.db; \
	kill %1 2>/dev/null; true

# Standalone dashboard with its own DB
fresh:
	source .venv/bin/activate && python main.py serve
