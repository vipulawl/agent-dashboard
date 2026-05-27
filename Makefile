.PHONY: blog blog-pull fresh

# Watch live local daemon runs
blog:
	source .venv/bin/activate && python main.py serve --db ~/blogging-agent/blogging_agent.db

# Pull latest from GitHub Actions first, then open dashboard
blog-pull:
	cd ~/blogging-agent && git pull --rebase
	source .venv/bin/activate && python main.py serve --db ~/blogging-agent/blogging_agent.db

# Standalone dashboard with its own DB
fresh:
	source .venv/bin/activate && python main.py serve
