# Agent Build Log

A small self-hosted web app that gives a single timeline of commits made by AI coding agents across a GitHub account. It polls the GitHub Events API for push events, accepts commits posted directly by agents, stores everything in SQLite and serves a dark, terminal-style dashboard grouped by agent and repository. Commits are attributed to an agent from an explicit `[agent: name]` tag, a `Co-Authored-By` trailer, or a known bot-style author name; plain human commits are not shown.

## Stack

- Python 3.11, FastAPI, Uvicorn
- httpx (GitHub API client), APScheduler (polling), SQLite
- Single-page frontend in plain HTML/CSS/JS (`web/index.html`)
- Docker / Docker Compose
- Playwright spec for a basic UI check (`check.spec.js`)

## Running

```bash
cp .env.example .env     # then edit the values
docker compose up --build
```

The dashboard is then at http://localhost:3456.

Configuration (environment variables in `.env`):

| Variable | Purpose |
|----------|---------|
| `GITHUB_TOKEN` | Personal access token used to read the Events API. Without it, polling is skipped. |
| `GITHUB_USERNAME` | GitHub account to poll. |
| `POLL_INTERVAL_MINUTES` | Polling interval (default 5). |
| `INGEST_SECRET` | If set, `POST /api/ingest` requires it in the `X-Ingest-Secret` header. |

Data is stored in `./data/events.db` (mounted into the container; git-ignored).

### API

- `GET /api/events` - list events, with filters
- `GET /api/stats` - summary counts
- `GET /api/filters` - available agents and repos
- `GET /api/backfill` - search GitHub for historical agent commits
- `POST /api/ingest` - agents post a commit event directly

### UI check

`check.spec.js` is a Playwright test that expects the app running on port 3456 with data present. Install Playwright yourself to run it (`npx playwright test check.spec.js`).

## Project layout

```
api/             FastAPI app, Dockerfile, requirements
web/             static frontend
docker-compose.yml
requirements.md  original product brief
```
