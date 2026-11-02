"""SQLAlchemy persistence: review runs, findings, feedback. SQLite by default, Postgres-ready."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    delete,
    func,
    select,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker
from sqlalchemy.pool import StaticPool

from .filters import fingerprint
from .schemas import Finding

STALE_RUN_AFTER = timedelta(minutes=15)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class ReviewRun(Base):
    __tablename__ = "review_runs"
    __table_args__ = (UniqueConstraint("repo", "pr_number", "head_sha", name="uq_run_pr_sha"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    repo: Mapped[str] = mapped_column(String(200), index=True)
    pr_number: Mapped[int] = mapped_column(Integer)
    head_sha: Mapped[str] = mapped_column(String(64))
    installation_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="running")  # running|done|failed
    summary: Mapped[str] = mapped_column(Text, default="")
    model: Mapped[str] = mapped_column(String(100), default="")
    prompt_version: Mapped[str] = mapped_column(String(20), default="")
    files_reviewed: Mapped[int] = mapped_column(Integer, default=0)
    chunks: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class FindingRow(Base):
    __tablename__ = "findings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("review_runs.id", ondelete="CASCADE"), index=True)
    file: Mapped[str] = mapped_column(String(500))
    line: Mapped[int] = mapped_column(Integer)
    severity: Mapped[str] = mapped_column(String(10))
    category: Mapped[str] = mapped_column(String(20))
    message: Mapped[str] = mapped_column(Text)
    suggestion: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence: Mapped[float] = mapped_column(Float, default=0.7)
    source: Mapped[str] = mapped_column(String(10), default="llm")
    fingerprint: Mapped[str] = mapped_column(String(32), index=True, default="")
    posted: Mapped[bool] = mapped_column(Boolean, default=False)
    github_comment_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    thumbs_up: Mapped[int] = mapped_column(Integer, default=0)
    thumbs_down: Mapped[int] = mapped_column(Integer, default=0)


class Database:
    def __init__(self, url: str):
        url = url.replace("postgres://", "postgresql://", 1) if url.startswith("postgres://") else url
        kwargs: dict = {}
        if url.startswith("sqlite"):
            kwargs["connect_args"] = {"check_same_thread": False}
            if url in ("sqlite://", "sqlite:///:memory:"):
                kwargs["poolclass"] = StaticPool
            else:
                Path(url.split("///", 1)[1]).parent.mkdir(parents=True, exist_ok=True)
        self.engine = create_engine(url, **kwargs)
        Base.metadata.create_all(self.engine)
        self._Session = sessionmaker(self.engine, expire_on_commit=False)

    @contextmanager
    def session(self):
        s = self._Session()
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()

    # --- runs (idempotency lives here) ---------------------------------------------
    def start_run(self, repo: str, pr_number: int, head_sha: str, *, model: str,
                  prompt_version: str, installation_id: int | None = None,
                  force: bool = False) -> int | None:
        """Return a run id, or None if this (repo, PR, commit) was already reviewed."""
        with self.session() as s:
            run = s.scalar(select(ReviewRun).where(
                ReviewRun.repo == repo, ReviewRun.pr_number == pr_number,
                ReviewRun.head_sha == head_sha))
            if run is not None:
                stale = run.status == "running" and _utcnow() - run.created_at > STALE_RUN_AFTER
                if not (force or run.status == "failed" or stale):
                    return None
                s.execute(delete(FindingRow).where(FindingRow.run_id == run.id))
                run.status, run.error, run.summary = "running", None, ""
                run.finished_at, run.created_at = None, _utcnow()
                run.model, run.prompt_version = model, prompt_version
                return run.id
            run = ReviewRun(repo=repo, pr_number=pr_number, head_sha=head_sha, model=model,
                            prompt_version=prompt_version, installation_id=installation_id)
            s.add(run)
            try:
                s.flush()
            except IntegrityError:  # a concurrent webhook delivery won the race
                s.rollback()
                return None
            return run.id

    def finish_run(self, run_id: int, *, summary: str, files_reviewed: int, chunks: int,
                   items: list[tuple[Finding, bool, int | None]]) -> None:
        with self.session() as s:
            run = s.get(ReviewRun, run_id)
            run.status, run.summary = "done", summary
            run.files_reviewed, run.chunks, run.finished_at = files_reviewed, chunks, _utcnow()
            for f, posted, comment_id in items:
                s.add(FindingRow(
                    run_id=run_id, file=f.file, line=f.line, severity=f.severity,
                    category=f.category, message=f.message, suggestion=f.suggestion,
                    confidence=f.confidence, source=f.source, fingerprint=fingerprint(f),
                    posted=posted, github_comment_id=comment_id))

    def fail_run(self, run_id: int, error: str) -> None:
        with self.session() as s:
            run = s.get(ReviewRun, run_id)
            run.status, run.error, run.finished_at = "failed", error[:2000], _utcnow()

    def previously_posted(self, repo: str, pr_number: int) -> set[str]:
        """Fingerprints already commented on this PR (avoid repeating on new commits)."""
        with self.session() as s:
            rows = s.scalars(
                select(FindingRow.fingerprint).join(ReviewRun)
                .where(ReviewRun.repo == repo, ReviewRun.pr_number == pr_number,
                       FindingRow.posted.is_(True)))
            return set(rows)

    # --- feedback ------------------------------------------------------------------
    def findings_for_feedback(self, limit: int = 200) -> list[dict]:
        with self.session() as s:
            rows = s.execute(
                select(FindingRow.id, ReviewRun.repo, FindingRow.github_comment_id,
                       ReviewRun.installation_id)
                .join(ReviewRun)
                .where(FindingRow.github_comment_id.is_not(None))
                .order_by(FindingRow.id.desc()).limit(limit)).all()
        return [{"id": r[0], "repo": r[1], "comment_id": r[2], "installation_id": r[3]}
                for r in rows]

    def record_feedback(self, finding_id: int, up: int, down: int) -> None:
        with self.session() as s:
            row = s.get(FindingRow, finding_id)
            if row:
                row.thumbs_up, row.thumbs_down = up, down

    # --- reporting (dashboard) -----------------------------------------------------
    def all_runs(self) -> list[dict]:
        with self.session() as s:
            runs = s.scalars(select(ReviewRun).order_by(ReviewRun.id.desc())).all()
            return [{c.name: getattr(r, c.name) for c in ReviewRun.__table__.columns} for r in runs]

    def all_findings(self) -> list[dict]:
        with self.session() as s:
            rows = s.execute(select(FindingRow, ReviewRun.repo, ReviewRun.pr_number)
                             .join(ReviewRun).order_by(FindingRow.id.desc())).all()
            return [{**{c.name: getattr(f, c.name) for c in FindingRow.__table__.columns},
                     "repo": repo, "pr_number": pr} for f, repo, pr in rows]

    def stats(self) -> dict:
        with self.session() as s:
            runs = s.scalar(select(func.count(ReviewRun.id))) or 0
            findings = s.scalar(select(func.count(FindingRow.id))) or 0
            posted = s.scalar(select(func.count(FindingRow.id)).where(FindingRow.posted.is_(True))) or 0
            up = s.scalar(select(func.coalesce(func.sum(FindingRow.thumbs_up), 0))) or 0
            down = s.scalar(select(func.coalesce(func.sum(FindingRow.thumbs_down), 0))) or 0
        voted = up + down
        return {"runs": runs, "findings": findings, "posted": posted, "thumbs_up": up,
                "thumbs_down": down, "helpful_rate": (up / voted) if voted else None}
