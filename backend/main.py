#!/usr/bin/env python3
"""
FastAPI backend for the Job Crawler dashboard.

Runs Ashby, Lever, and Greenhouse watchers concurrently, stores each run's
new jobs in Postgres, and serves the data to the React frontend.

Environment:
    DATABASE_URL  postgresql://user:pass@host/dbname  (default: localhost/jobcrawler)

Run:
    uvicorn main:app --reload --port 8000
"""

import asyncio
import datetime
import json
import os
import subprocess
import sys
import traceback
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path

import asyncpg
from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

CRAWLER_DIR = Path(__file__).parent.parent
DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql://postgres:root@localhost:5432/jobcrawler"
)

# (ats_name, script_filename, output_json_file)
WATCHERS = [
    ("ashby",       "ashby_watch.py",       "latest.json"),
    ("lever",       "LeverWatch.py",         "latest_lever.json"),
    ("greenhouse",  "GreenHouseWatch.py",    "latest_greenhouse.json"),
]

# ---------------------------------------------------------------------------
# App state
# ---------------------------------------------------------------------------

pool: asyncpg.Pool | None = None
_is_running = False
_active_run_id: int | None = None

# ---------------------------------------------------------------------------
# DB setup
# ---------------------------------------------------------------------------

DDL = """
CREATE TABLE IF NOT EXISTS runs (
    id           SERIAL PRIMARY KEY,
    run_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ashby_new    INT         NOT NULL DEFAULT 0,
    lever_new    INT         NOT NULL DEFAULT 0,
    greenhouse_new INT       NOT NULL DEFAULT 0,
    total_new    INT         NOT NULL DEFAULT 0,
    in_progress  BOOLEAN     NOT NULL DEFAULT TRUE
);

CREATE TABLE IF NOT EXISTS jobs (
    id           SERIAL PRIMARY KEY,
    run_id       INT         NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    ats          VARCHAR(20) NOT NULL,
    company      VARCHAR(255) NOT NULL DEFAULT '',
    title        VARCHAR(500) NOT NULL DEFAULT '',
    location     VARCHAR(255) NOT NULL DEFAULT '',
    department   VARCHAR(255) NOT NULL DEFAULT '',
    url          TEXT        NOT NULL DEFAULT '',
    published_at VARCHAR(50) NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_jobs_run_id ON jobs(run_id);
CREATE INDEX IF NOT EXISTS idx_runs_run_at ON runs(run_at DESC);
"""


@asynccontextmanager
async def lifespan(app: FastAPI):
    global pool
    pool = await asyncpg.create_pool(DATABASE_URL)
    async with pool.acquire() as conn:
        await conn.execute(DDL)
    yield
    await pool.close()


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Runner logic
# ---------------------------------------------------------------------------

_executor = ThreadPoolExecutor(max_workers=3)


def _run_watcher_sync(script: str, json_file: str) -> list[dict]:
    """Blocking: run one watcher script in-process thread, return its new jobs."""
    result = subprocess.run(
        [sys.executable, str(CRAWLER_DIR / script)],
        cwd=str(CRAWLER_DIR),
        capture_output=True,
        text=True,
        timeout=600,
    )
    if result.stderr:
        print(f"[{script}]\n{result.stderr.strip()}", flush=True)
    if result.returncode != 0:
        raise RuntimeError(f"{script} exited with code {result.returncode}")

    json_path = CRAWLER_DIR / json_file
    if not json_path.exists():
        return []
    with open(json_path, encoding="utf-8") as f:
        return json.load(f).get("new", [])


async def _run_watcher(script: str, json_file: str) -> list[dict]:
    """Async wrapper: runs the blocking watcher in the thread pool."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(_executor, _run_watcher_sync, script, json_file)


async def _execute_run(run_id: int) -> None:
    global _is_running, _active_run_id
    try:
        async def run_one(ats: str, script: str, json_tmpl: str):
            try:
                return ats, await _run_watcher(script, json_tmpl)
            except Exception:
                traceback.print_exc()
                return ats, []

        results = await asyncio.gather(*(run_one(*w) for w in WATCHERS))

        try:
            async with pool.acquire() as conn:
                async with conn.transaction():
                    counts: dict[str, int] = {}
                    for ats, jobs in results:
                        counts[ats] = len(jobs)
                        for job in jobs:
                            await conn.execute(
                                """INSERT INTO jobs
                                   (run_id, ats, company, title, location, department, url, published_at)
                                   VALUES ($1,$2,$3,$4,$5,$6,$7,$8)""",
                                run_id, ats,
                                job.get("company", ""),
                                job.get("title", ""),
                                job.get("location", ""),
                                job.get("department", ""),
                                job.get("url", ""),
                                job.get("publishedAt", ""),
                            )
                    await conn.execute(
                        """UPDATE runs
                           SET ashby_new=$1, lever_new=$2, greenhouse_new=$3,
                               total_new=$4, in_progress=FALSE
                           WHERE id=$5""",
                        counts.get("ashby", 0),
                        counts.get("lever", 0),
                        counts.get("greenhouse", 0),
                        sum(counts.values()),
                        run_id,
                    )
        except Exception:
            # Make sure a failure here can never leave the run stuck at
            # in_progress=TRUE forever (the frontend polls this forever).
            traceback.print_exc()
            async with pool.acquire() as conn:
                await conn.execute(
                    "UPDATE runs SET in_progress=FALSE WHERE id=$1", run_id
                )
    finally:
        _is_running = False
        _active_run_id = None


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.post("/api/run")
async def trigger_run(background_tasks: BackgroundTasks):
    """Start a new crawl run. Returns immediately; job is background."""
    global _is_running, _active_run_id
    if _is_running:
        return {"run_id": _active_run_id, "status": "already_running"}

    _is_running = True
    async with pool.acquire() as conn:
        run_id = await conn.fetchval(
            "INSERT INTO runs (in_progress) VALUES (TRUE) RETURNING id"
        )
    _active_run_id = run_id
    background_tasks.add_task(_execute_run, run_id)
    return {"run_id": run_id, "status": "started"}


@app.get("/api/status")
async def get_status():
    """Poll this to check if a run is in progress."""
    return {"running": _is_running, "active_run_id": _active_run_id}


@app.get("/api/runs")
async def list_runs():
    """Returns all runs from the past 24 hours, newest first."""
    cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=1)
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT id, run_at, ashby_new, lever_new, greenhouse_new,
                      total_new, in_progress
               FROM runs
               WHERE run_at >= $1
               ORDER BY run_at DESC""",
            cutoff,
        )
    return [dict(r) for r in rows]


@app.get("/api/runs/latest")
async def latest_run():
    """Returns the most recently completed run id."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id FROM runs WHERE in_progress=FALSE ORDER BY run_at DESC LIMIT 1"
        )
    if not row:
        raise HTTPException(404, "No completed runs yet")
    return {"run_id": row["id"]}


@app.get("/api/runs/{run_id}/jobs")
async def get_jobs(run_id: int):
    """Returns jobs for a specific run plus its in_progress flag."""
    async with pool.acquire() as conn:
        run = await conn.fetchrow(
            "SELECT id, in_progress FROM runs WHERE id=$1", run_id
        )
        if not run:
            raise HTTPException(404, "Run not found")
        rows = await conn.fetch(
            """SELECT ats, company, title, location, department, url, published_at
               FROM jobs WHERE run_id=$1 ORDER BY ats, company, title""",
            run_id,
        )
    return {"in_progress": run["in_progress"], "jobs": [dict(r) for r in rows]}


