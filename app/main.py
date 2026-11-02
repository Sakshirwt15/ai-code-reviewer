"""FastAPI webhook server.  Run:  uvicorn app.main:create_app --factory --port 8000"""
from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request

from . import __version__
from .config import Settings
from .db import Database
from .github_client import make_github_client
from .llm import make_llm_client
from .pipeline import review_pull_request, sync_feedback
from .reviewer import Reviewer
from .security import verify_signature

log = logging.getLogger("ai-review")
HANDLED_ACTIONS = {"opened", "synchronize", "reopened", "ready_for_review"}


@dataclass
class ReviewJob:
    repo: str
    pr_number: int
    head_sha: str
    installation_id: int | None = None


def make_default_runner(settings: Settings, db: Database) -> Callable[[ReviewJob], None]:
    def run(job: ReviewJob) -> None:
        try:
            gh = make_github_client(settings, job.installation_id)
            reviewer = Reviewer(make_llm_client(settings), settings.prompt_version,
                                settings.max_retries)
            outcome = review_pull_request(gh, db, reviewer, settings, job.repo, job.pr_number,
                                          installation_id=job.installation_id)
            log.info("%s#%s -> %s (%s)", job.repo, job.pr_number, outcome.status, outcome.message)
        except Exception:  # background task: never crash the worker
            log.exception("review job crashed: %s#%s", job.repo, job.pr_number)

    return run


def create_app(settings: Settings | None = None, db: Database | None = None,
               review_runner: Callable[[ReviewJob], None] | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    db = db or Database(settings.database_url)
    runner = review_runner or make_default_runner(settings, db)
    app = FastAPI(title="AI Code Review Bot", version=__version__)

    @app.get("/")
    async def root():
        return {
        "message": "AI Code Review Bot is running!",
        "status": "success"
    }

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok", "version": __version__, "provider": settings.llm_provider}

    @app.get("/stats")
    def stats() -> dict:
        return db.stats()

    @app.post("/webhook", status_code=202)
    async def webhook(request: Request, background: BackgroundTasks) -> dict:
        body = await request.body()
        if settings.webhook_secret:
            if not verify_signature(settings.webhook_secret, body,
                                    request.headers.get("X-Hub-Signature-256")):
                raise HTTPException(401, "invalid signature")
        elif not settings.allow_unsigned_webhooks:
            raise HTTPException(500, "GITHUB_WEBHOOK_SECRET is not configured")

        event = request.headers.get("X-GitHub-Event", "")
        if event == "ping":
            return {"status": "pong"}
        if event != "pull_request":
            return {"status": "ignored", "reason": f"event '{event}' not handled"}
        try:
            payload = json.loads(body)
            action = payload["action"]
            pr = payload["pull_request"]
            job = ReviewJob(repo=payload["repository"]["full_name"], pr_number=pr["number"],
                            head_sha=pr["head"]["sha"],
                            installation_id=(payload.get("installation") or {}).get("id"))
        except (ValueError, KeyError, TypeError) as exc:
            raise HTTPException(400, f"malformed payload: {exc}") from exc
        if action not in HANDLED_ACTIONS:
            return {"status": "ignored", "reason": f"action '{action}' not handled"}
        if pr.get("draft"):
            return {"status": "ignored", "reason": "draft pull request"}

        # Respond immediately (GitHub times out after 10s); review runs in the background.
        background.add_task(runner, job)
        return {"status": "queued", "repo": job.repo, "pr": job.pr_number}

    @app.post("/feedback/sync")
    def feedback_sync(request: Request) -> dict:
        if not settings.admin_token:
            raise HTTPException(403, "ADMIN_TOKEN not configured")
        if request.headers.get("X-Admin-Token") != settings.admin_token:
            raise HTTPException(401, "bad admin token")
        updated = sync_feedback(db, lambda inst: make_github_client(settings, inst))
        return {"updated": updated}

    return app
