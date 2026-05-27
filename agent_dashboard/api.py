from pathlib import Path
from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles

from agent_dashboard import db

_STATIC = Path(__file__).parent.parent / "static"

app = FastAPI(title="Agent Dashboard", docs_url=None, redoc_url=None)


@app.get("/", response_class=HTMLResponse)
async def index():
    html = (_STATIC / "index.html").read_text()
    return HTMLResponse(html)


@app.get("/api/overview")
async def overview():
    return {
        "kpis": db.get_kpis(),
        "recent_runs": db.get_recent_runs(limit=10),
        "timeline": db.get_timeline(days=7),
        "top_errors": db.get_top_errors(limit=5),
        "agent_stats": db.get_agent_stats(),
    }


@app.get("/api/runs")
async def runs(
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    agent: str = Query(""),
    status: str = Query(""),
    search: str = Query(""),
):
    return db.get_runs(page=page, limit=limit, agent=agent, status=status, search=search)


@app.get("/api/runs/{run_id}")
async def run_detail(run_id: str):
    run = db.get_run_by_id(run_id)
    if not run:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Run not found")
    return run


@app.get("/api/runs/{run_id}/iterations")
async def run_iterations(run_id: str):
    return db.get_iterations_for_run(run_id)


@app.get("/api/runs/{run_id}/tools")
async def run_tools(run_id: str):
    return db.get_tools_for_run(run_id)


@app.get("/api/failures")
async def failures():
    return db.get_failures()


@app.get("/api/tool-stats")
async def tool_stats():
    return db.get_tool_stats()


@app.get("/api/agent-stats")
async def agent_stats():
    return db.get_agent_stats()


@app.get("/api/agent-names")
async def agent_names():
    return db.get_agent_names()
