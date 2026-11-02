import dataclasses

from app.github_client import GitHubError
from app.llm import MockClient
from app.pipeline import analyze_diff, review_pull_request, sync_feedback
from app.reviewer import Reviewer


class FakeGitHub:
    def __init__(self, diff, sha="a" * 40, draft=False, reject_inline=False):
        self.diff, self.sha, self.draft, self.reject_inline = diff, sha, draft, reject_inline
        self.reviews = []

    def get_pull(self, repo, n):
        return {"head": {"sha": self.sha}, "title": "t", "body": "", "draft": self.draft,
                "state": "open"}

    def get_diff(self, repo, n):
        return self.diff

    def get_file_content(self, repo, path, ref):
        return None

    def create_review(self, repo, n, commit_id, body, comments):
        if comments and self.reject_inline:
            raise GitHubError(422, "line not in diff")
        self.reviews.append({"body": body, "comments": comments})
        return {"id": 77}

    def list_review_comments(self, repo, n, review_id):
        return [{"id": 9000 + i, "path": c["path"], "line": c["line"], "body": c["body"]}
                for i, c in enumerate(self.reviews[-1]["comments"])]

    def get_comment_reactions(self, repo, comment_id):
        return {"up": 3, "down": 1}


def _reviewer(settings):
    return Reviewer(MockClient(), settings.prompt_version, settings.max_retries)


def test_analyze_diff_finds_sqli_on_the_right_line(settings, sqli_diff):
    a = analyze_diff(sqli_diff, _reviewer(settings), settings)
    assert len(a.kept) == 1
    assert a.kept[0].category == "security" and a.kept[0].line == 10


def test_full_flow_posts_once_and_is_idempotent(settings, db, sqli_diff):
    gh = FakeGitHub(sqli_diff)
    first = review_pull_request(gh, db, _reviewer(settings), settings, "o/r", 1)
    assert first.status == "done" and len(gh.reviews) == 1
    review = gh.reviews[0]
    assert review["comments"][0]["side"] == "RIGHT" and "AI Code Review" in review["body"]

    again = review_pull_request(gh, db, _reviewer(settings), settings, "o/r", 1)  # webhook retry
    assert again.status == "skipped" and len(gh.reviews) == 1

    gh.sha = "b" * 40  # new commit pushed -> reviewed again, but same finding not repeated
    third = review_pull_request(gh, db, _reviewer(settings), settings, "o/r", 1)
    assert third.status == "done" and gh.reviews[-1]["comments"] == []

    stats = db.stats()
    assert stats["runs"] == 2 and stats["posted"] == 1


def test_feedback_sync_updates_counts(settings, db, sqli_diff):
    gh = FakeGitHub(sqli_diff)
    review_pull_request(gh, db, _reviewer(settings), settings, "o/r", 1)
    assert sync_feedback(db, lambda _i: gh) == 1
    s = db.stats()
    assert (s["thumbs_up"], s["thumbs_down"], s["helpful_rate"]) == (3, 1, 0.75)


def test_falls_back_to_summary_when_github_rejects_inline(settings, db, sqli_diff):
    gh = FakeGitHub(sqli_diff, reject_inline=True)
    out = review_pull_request(gh, db, _reviewer(settings), settings, "o/r", 1)
    assert out.status == "done" and gh.reviews[0]["comments"] == []
    assert "SQL query is built" in gh.reviews[0]["body"]


def test_dry_run_and_draft(settings, db, sqli_diff):
    gh = FakeGitHub(sqli_diff)
    assert review_pull_request(gh, db, _reviewer(settings), settings, "o/r", 1,
                               dry_run=True).status == "dry_run"
    assert gh.reviews == [] and db.stats()["runs"] == 0
    draft = FakeGitHub(sqli_diff, draft=True)
    assert review_pull_request(draft, db, _reviewer(settings), settings, "o/r", 2).status == "skipped"


def test_llm_failure_marks_run_failed_and_allows_retry(settings, db, sqli_diff):
    class Broken:
        name, model = "broken", "b"

        def complete(self, *a, **k):
            return "garbage"

    s = dataclasses.replace(settings, max_retries=0)
    gh = FakeGitHub(sqli_diff)
    out = review_pull_request(gh, db, Reviewer(Broken(), "v2", 0), s, "o/r", 1)
    assert out.status == "failed" and gh.reviews == []
    retry = review_pull_request(gh, db, _reviewer(settings), settings, "o/r", 1)
    assert retry.status == "done"  # failed runs are retried on redelivery


def test_prompt_injection_is_flagged_and_ignored(settings):
    from tests.conftest import DATASET
    diff = (DATASET / "prompt_injection" / "pr.diff").read_text()
    a = analyze_diff(diff, _reviewer(settings), settings)
    assert a.injection_suspected
    assert any("eval" in f.message.lower() for f in a.kept)
