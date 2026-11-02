"""Minimal GitHub REST client (PAT or GitHub App installation token)."""
from __future__ import annotations

import time
from typing import Any

import httpx

from .config import ConfigError, Settings

API = "https://api.github.com"


class GitHubError(RuntimeError):
    def __init__(self, status: int, message: str):
        super().__init__(f"GitHub API {status}: {message}")
        self.status = status


class GitHubClient:
    def __init__(self, token: str, base_url: str = API, client: httpx.Client | None = None):
        self.base_url = base_url.rstrip("/")
        self._client = client or httpx.Client(timeout=30.0)
        self._headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "ai-code-review-bot",
        }

    def _request(self, method: str, path: str, *, accept: str | None = None,
                 **kwargs: Any) -> httpx.Response:
        headers = dict(self._headers)
        if accept:
            headers["Accept"] = accept
        resp = self._client.request(method, f"{self.base_url}{path}", headers=headers, **kwargs)
        if resp.status_code >= 400:
            raise GitHubError(resp.status_code, resp.text[:300])
        return resp

    # --- reads ---------------------------------------------------------------------
    def get_pull(self, repo: str, number: int) -> dict:
        return self._request("GET", f"/repos/{repo}/pulls/{number}").json()

    def get_diff(self, repo: str, number: int) -> str:
        return self._request("GET", f"/repos/{repo}/pulls/{number}",
                             accept="application/vnd.github.v3.diff").text

    def get_file_content(self, repo: str, path: str, ref: str) -> str | None:
        try:
            return self._request("GET", f"/repos/{repo}/contents/{path}", params={"ref": ref},
                                 accept="application/vnd.github.raw+json").text
        except GitHubError as exc:
            if exc.status in (403, 404):
                return None
            raise

    def list_review_comments(self, repo: str, number: int, review_id: int) -> list[dict]:
        out: list[dict] = []
        page = 1
        while True:
            batch = self._request(
                "GET", f"/repos/{repo}/pulls/{number}/reviews/{review_id}/comments",
                params={"per_page": 100, "page": page}).json()
            out += batch
            if len(batch) < 100:
                return out
            page += 1

    def get_comment_reactions(self, repo: str, comment_id: int) -> dict[str, int]:
        items = self._request("GET", f"/repos/{repo}/pulls/comments/{comment_id}/reactions",
                              params={"per_page": 100}).json()
        return {"up": sum(1 for r in items if r.get("content") == "+1"),
                "down": sum(1 for r in items if r.get("content") == "-1")}

    # --- writes --------------------------------------------------------------------
    def create_review(self, repo: str, number: int, commit_id: str, body: str,
                      comments: list[dict]) -> dict:
        payload: dict[str, Any] = {"commit_id": commit_id, "body": body, "event": "COMMENT"}
        if comments:
            payload["comments"] = comments
        return self._request("POST", f"/repos/{repo}/pulls/{number}/reviews", json=payload).json()

    def post_issue_comment(self, repo: str, number: int, body: str) -> dict:
        return self._request("POST", f"/repos/{repo}/issues/{number}/comments",
                             json={"body": body}).json()


def github_app_installation_token(app_id: str, private_key: str, installation_id: int,
                                  base_url: str = API) -> str:
    """GitHub App auth: sign a short-lived JWT, exchange it for an installation token."""
    import jwt  # PyJWT (needs the `cryptography` extra for RS256)

    now = int(time.time())
    app_jwt = jwt.encode({"iat": now - 60, "exp": now + 540, "iss": app_id}, private_key,
                         algorithm="RS256")
    resp = httpx.post(
        f"{base_url}/app/installations/{installation_id}/access_tokens",
        headers={"Authorization": f"Bearer {app_jwt}", "Accept": "application/vnd.github+json",
                 "User-Agent": "ai-code-review-bot"},
        timeout=30.0)
    if resp.status_code >= 400:
        raise GitHubError(resp.status_code, resp.text[:300])
    return resp.json()["token"]


def make_github_client(settings: Settings, installation_id: int | None = None) -> GitHubClient:
    if settings.github_app_id and settings.github_app_private_key and installation_id:
        token = github_app_installation_token(
            settings.github_app_id, settings.github_app_private_key, installation_id)
        return GitHubClient(token)
    if settings.github_token:
        return GitHubClient(settings.github_token)
    raise ConfigError("Set GITHUB_TOKEN (PAT) or GITHUB_APP_ID + GITHUB_APP_PRIVATE_KEY")
