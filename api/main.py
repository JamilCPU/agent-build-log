import os
import re
import json
import uuid
import sqlite3
import hashlib
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import httpx
from fastapi import FastAPI, HTTPException, Header, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dotenv import load_dotenv

load_dotenv()

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
GITHUB_USERNAME = os.getenv("GITHUB_USERNAME", "JamilCPU")
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL_MINUTES", "5"))
INGEST_SECRET = os.getenv("INGEST_SECRET", "")
DB_PATH = Path("/data/events.db")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

app = FastAPI(title="Agent Build Log")


# ── Database ──────────────────────────────────────────────────────────────────

def get_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS events (
                id TEXT PRIMARY KEY,
                repo TEXT NOT NULL,
                branch TEXT DEFAULT 'main',
                agent TEXT,
                commit_sha TEXT,
                commit_message TEXT,
                author_name TEXT,
                author_email TEXT,
                files_changed TEXT DEFAULT '[]',
                timestamp TEXT NOT NULL,
                source TEXT NOT NULL,
                github_url TEXT,
                created_at TEXT NOT NULL
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_timestamp ON events(timestamp DESC)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_agent ON events(agent)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_repo ON events(repo)")
        conn.commit()


def event_id(sha: str, repo: str) -> str:
    return hashlib.sha256(f"{sha}:{repo}".encode()).hexdigest()[:16]


def insert_event(data: dict) -> bool:
    """Insert event, return True if new, False if duplicate."""
    try:
        with get_db() as conn:
            conn.execute("""
                INSERT INTO events
                  (id, repo, branch, agent, commit_sha, commit_message,
                   author_name, author_email, files_changed, timestamp,
                   source, github_url, created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                data["id"], data["repo"], data.get("branch", "main"),
                data.get("agent"), data.get("commit_sha"),
                data.get("commit_message"), data.get("author_name"),
                data.get("author_email"),
                json.dumps(data.get("files_changed", [])),
                data["timestamp"], data["source"],
                data.get("github_url"),
                datetime.now(timezone.utc).isoformat()
            ))
            conn.commit()
            return True
    except sqlite3.IntegrityError:
        return False


def extract_agent(message: str, author: str) -> Optional[str]:
    """Parse [agent: name] from commit message, or infer from author."""
    m = re.search(r'\[agent:\s*([^\]]+)\]', message or "", re.IGNORECASE)
    if m:
        return m.group(1).strip()
    # Known bot author patterns
    if author and any(x in author.lower() for x in ["openclaw", "agent", "bot", "pi1"]):
        return author.split("@")[0]
    return None


# ── GitHub Poller ─────────────────────────────────────────────────────────────

async def poll_github():
    if not GITHUB_TOKEN:
        log.warning("No GITHUB_TOKEN set, skipping poll")
        return

    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    async with httpx.AsyncClient(timeout=15) as client:
        try:
            resp = await client.get(
                f"https://api.github.com/users/{GITHUB_USERNAME}/events",
                headers=headers,
                params={"per_page": 100}
            )
            resp.raise_for_status()
        except Exception as e:
            log.error(f"GitHub API error: {e}")
            return

        events = resp.json()
        new_count = 0

        for ev in events:
            if ev.get("type") != "PushEvent":
                continue

            payload = ev.get("payload", {})
            repo = ev.get("repo", {}).get("name", "").replace(f"{GITHUB_USERNAME}/", "")
            branch = payload.get("ref", "refs/heads/main").replace("refs/heads/", "")
            commits = payload.get("commits", [])

            for commit in commits:
                sha = commit.get("sha", "")
                message = commit.get("message", "")
                author = commit.get("author", {})
                author_name = author.get("name", "")
                author_email = author.get("email", "")

                eid = event_id(sha, repo)
                agent = extract_agent(message, author_name)

                inserted = insert_event({
                    "id": eid,
                    "repo": repo,
                    "branch": branch,
                    "agent": agent,
                    "commit_sha": sha[:7],
                    "commit_message": message.split("\n")[0][:200],
                    "author_name": author_name,
                    "author_email": author_email,
                    "files_changed": [],
                    "timestamp": ev.get("created_at", datetime.now(timezone.utc).isoformat()),
                    "source": "github_api",
                    "github_url": f"https://github.com/{GITHUB_USERNAME}/{repo}/commit/{sha}",
                })
                if inserted:
                    new_count += 1

        if new_count:
            log.info(f"Polled GitHub: {new_count} new events")


# ── Ingest endpoint ───────────────────────────────────────────────────────────

class IngestPayload(BaseModel):
    repo: str
    branch: str = "main"
    agent: Optional[str] = None
    commit_sha: Optional[str] = None
    commit_message: Optional[str] = None
    author_name: Optional[str] = None
    author_email: Optional[str] = None
    files_changed: list[str] = []
    timestamp: Optional[str] = None
    github_url: Optional[str] = None


@app.post("/api/ingest")
async def ingest(payload: IngestPayload, x_ingest_secret: Optional[str] = Header(None)):
    if INGEST_SECRET and x_ingest_secret != INGEST_SECRET:
        raise HTTPException(status_code=401, detail="Invalid secret")

    sha = payload.commit_sha or str(uuid.uuid4())
    eid = event_id(sha, payload.repo)
    agent = payload.agent or extract_agent(payload.commit_message or "", payload.author_name or "")

    inserted = insert_event({
        "id": eid,
        "repo": payload.repo,
        "branch": payload.branch,
        "agent": agent,
        "commit_sha": (payload.commit_sha or "")[:7],
        "commit_message": (payload.commit_message or "")[:200],
        "author_name": payload.author_name,
        "author_email": payload.author_email,
        "files_changed": payload.files_changed,
        "timestamp": payload.timestamp or datetime.now(timezone.utc).isoformat(),
        "source": "agent_post",
        "github_url": payload.github_url,
    })

    return {"ok": True, "new": inserted, "id": eid}


# ── Query endpoints ───────────────────────────────────────────────────────────

@app.get("/api/events")
async def list_events(
    agent: Optional[str] = None,
    repo: Optional[str] = None,
    limit: int = 50,
    offset: int = 0
):
    query = "SELECT * FROM events WHERE 1=1"
    params = []
    if agent:
        query += " AND agent = ?"
        params.append(agent)
    if repo:
        query += " AND repo = ?"
        params.append(repo)
    query += f" ORDER BY timestamp DESC LIMIT ? OFFSET ?"
    params += [min(limit, 200), offset]

    with get_db() as conn:
        rows = conn.execute(query, params).fetchall()
        events = []
        for r in rows:
            e = dict(r)
            e["files_changed"] = json.loads(e.get("files_changed") or "[]")
            events.append(e)
    return {"events": events, "count": len(events)}


@app.get("/api/stats")
async def stats():
    with get_db() as conn:
        total = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        agents = conn.execute(
            "SELECT agent, COUNT(*) as cnt FROM events WHERE agent IS NOT NULL GROUP BY agent ORDER BY cnt DESC"
        ).fetchall()
        repos = conn.execute(
            "SELECT repo, COUNT(*) as cnt FROM events GROUP BY repo ORDER BY cnt DESC LIMIT 10"
        ).fetchall()
        recent = conn.execute(
            "SELECT timestamp FROM events ORDER BY timestamp DESC LIMIT 1"
        ).fetchone()

    return {
        "total_events": total,
        "agents": [dict(r) for r in agents],
        "repos": [dict(r) for r in repos],
        "last_event": recent[0] if recent else None,
    }


@app.get("/api/filters")
async def filters():
    with get_db() as conn:
        agents = conn.execute(
            "SELECT DISTINCT agent FROM events WHERE agent IS NOT NULL ORDER BY agent"
        ).fetchall()
        repos = conn.execute(
            "SELECT DISTINCT repo FROM events ORDER BY repo"
        ).fetchall()
    return {
        "agents": [r[0] for r in agents],
        "repos": [r[0] for r in repos],
    }


# ── Static frontend ───────────────────────────────────────────────────────────

WEB_DIR = Path(__file__).parent.parent / "web"

@app.get("/")
async def index():
    return FileResponse(WEB_DIR / "index.html")

if WEB_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")


# ── Startup ───────────────────────────────────────────────────────────────────

@app.on_event("startup")
async def startup():
    init_db()
    scheduler = AsyncIOScheduler()
    scheduler.add_job(poll_github, "interval", minutes=POLL_INTERVAL, next_run_time=datetime.now())
    scheduler.start()
    log.info(f"Polling GitHub every {POLL_INTERVAL} minutes")
