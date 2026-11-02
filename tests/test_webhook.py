import hashlib
import hmac
import json

from fastapi.testclient import TestClient

from app.main import create_app


def _headers(secret, body, event="pull_request"):
    sig = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return {"X-Hub-Signature-256": sig, "X-GitHub-Event": event, "Content-Type": "application/json"}


def _payload(action="opened", draft=False):
    return {"action": action, "installation": {"id": 5},
            "repository": {"full_name": "o/r"},
            "pull_request": {"number": 7, "draft": draft, "head": {"sha": "abc123"}}}


def _client(settings, db):
    jobs = []
    return TestClient(create_app(settings, db, review_runner=jobs.append)), jobs


def test_rejects_bad_signature(settings, db):
    client, jobs = _client(settings, db)
    body = json.dumps(_payload()).encode()
    r = client.post("/webhook", content=body, headers=_headers("wrong", body))
    assert r.status_code == 401 and jobs == []


def test_rejects_when_secret_not_configured(db):
    from app.config import Settings
    client = TestClient(create_app(Settings(webhook_secret=""), db, review_runner=lambda j: None))
    assert client.post("/webhook", content=b"{}").status_code == 500


def test_ping_and_ignored_events(settings, db):
    client, jobs = _client(settings, db)
    body = b"{}"
    assert client.post("/webhook", content=body, headers=_headers("s3cret", body, "ping")).json() == {"status": "pong"}
    assert client.post("/webhook", content=body, headers=_headers("s3cret", body, "push")).json()["status"] == "ignored"
    assert jobs == []


def test_pull_request_event_is_queued(settings, db):
    client, jobs = _client(settings, db)
    body = json.dumps(_payload()).encode()
    r = client.post("/webhook", content=body, headers=_headers("s3cret", body))
    assert r.status_code == 202 and r.json()["status"] == "queued"
    assert (jobs[0].repo, jobs[0].pr_number, jobs[0].head_sha, jobs[0].installation_id) == ("o/r", 7, "abc123", 5)


def test_ignores_draft_and_irrelevant_actions(settings, db):
    client, jobs = _client(settings, db)
    for p in (_payload(draft=True), _payload(action="closed"), _payload(action="labeled")):
        body = json.dumps(p).encode()
        assert client.post("/webhook", content=body, headers=_headers("s3cret", body)).json()["status"] == "ignored"
    assert jobs == []


def test_health_stats_and_admin_guard(settings, db):
    client, _ = _client(settings, db)
    assert client.get("/health").json()["status"] == "ok"
    assert client.get("/stats").json()["runs"] == 0
    assert client.post("/feedback/sync").status_code == 403  # ADMIN_TOKEN not set
