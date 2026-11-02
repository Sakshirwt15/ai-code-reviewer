# 🤖 AI Code Review Bot

When someone opens a Pull Request on GitHub, this bot reads the diff, runs static analysis
(ruff, bandit), asks an LLM to review it, filters the noise, and posts **inline comments + a
short summary** on the PR. Everything is stored in a database for a dashboard and for measuring
quality (👍/👎 reactions + an offline evaluation set).

It is a small version of tools like GitHub Copilot code review or CodeRabbit.

```
Developer opens PR
  → GitHub sends webhook (or a GitHub Action runs)
  → verify HMAC signature                       app/security.py, app/main.py
  → fetch PR diff via GitHub API                app/github_client.py
  → drop lock/generated/binary files            app/diff_parser.py
  → redact secrets, split into chunks           app/security.py, app/diff_parser.py
  → ruff + bandit on changed .py files          app/static_checks.py
  → LLM review → strict JSON → Pydantic         app/reviewer.py, app/schemas.py
  → noise filter, line validation, dedupe       app/filters.py
  → post inline comments + summary              app/pipeline.py, app/formatting.py
  → save runs/findings/feedback                 app/db.py  →  Streamlit dashboard
```

## Quick start (5 minutes, no API key needed)

```bash
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
cp .env.example .env
pytest -q                                              # 38 tests
python -m app.cli review-diff eval/dataset/sql_injection/pr.diff
python -m eval.run_eval                                # evaluation on 7 seeded-bug PRs
```

The default `LLM_PROVIDER=mock` is an offline rule-based reviewer, so everything runs without
keys. It is also a useful **baseline** to compare real models against.

## Use a real LLM

In `.env`:

| Provider | Settings |
|---|---|
| Claude | `LLM_PROVIDER=anthropic`, `ANTHROPIC_API_KEY=...` |
| Gemini | `LLM_PROVIDER=gemini`, `GEMINI_API_KEY=...` |
| Groq | `LLM_PROVIDER=groq`, `GROQ_API_KEY=...` |

Model names change often. Defaults are in `app/config.py`; override with `LLM_MODEL=...`.

## Phase 1: review a real PR from the command line (PAT)

1. Create a GitHub fine-grained token with **Pull requests: Read & write** and **Contents: Read**,
   put it in `GITHUB_TOKEN`.
2. Try without posting anything, then post for real:

```bash
python -m app.cli review owner/repo 12 --dry-run
python -m app.cli review owner/repo 12
```

## Phase 2: webhook server

```bash
uvicorn app.main:create_app --factory --reload --port 8000
```

1. Set `GITHUB_WEBHOOK_SECRET` in `.env` (any long random string). Requests without a valid
   `X-Hub-Signature-256` are rejected, and the server refuses to run unsigned unless you
   explicitly set `ALLOW_UNSIGNED_WEBHOOKS=true` (local debugging only).
2. Expose it: `ngrok http 8000` (or deploy, see below).
3. In the repo: *Settings → Webhooks → Add webhook* → URL `https://<host>/webhook`,
   content type `application/json`, same secret, event **Pull requests**.

Endpoints: `POST /webhook`, `GET /health`, `GET /stats`, `POST /feedback/sync` (needs
`ADMIN_TOKEN`).

### GitHub App (production auth)

Create a GitHub App with permissions *Pull requests: R/W*, *Contents: Read*, *Metadata: Read*,
subscribe to *Pull request* events, point its webhook to `/webhook`, generate a private key, then set
`GITHUB_APP_ID` and `GITHUB_APP_PRIVATE_KEY`. The bot signs a JWT and exchanges it for a
short-lived installation token per webhook (`app/github_client.py`).

### No server? Use a GitHub Action

Copy `docs/github-action-example.yml` into the repo you want reviewed.

## Reactions → feedback

GitHub does not send a webhook for reactions, so run `python -m app.cli sync-feedback`
(cron) or call `POST /feedback/sync`. It stores 👍/👎 per comment; the dashboard shows the
**helpful rate**.

## Dashboard

```bash
pip install -r requirements-dashboard.txt
streamlit run dashboard/app.py
```

## Evaluation

`eval/dataset/<case>/{pr.diff, expected.json}` are seeded-bug PRs (SQL injection, hard-coded
secrets, `eval`, bare except, disabled TLS verification, a clean change, a prompt-injection
attempt). `python -m eval.run_eval [--provider anthropic] [--prompt v1]` reports precision,
recall and F1 into `eval/results.md`. Use it to compare prompt versions (`prompts/v1.md` vs
`v2.md`) and models. The bundled mock scores perfectly because the dataset is small and its
rules were written for it, so **treat that as a smoke test, not a benchmark**. Add harder cases
(real bug-fix PRs) before quoting numbers.

## Docker

```bash
docker compose up --build        # API on :8000, dashboard on :8501, SQLite in ./data
```

For PostgreSQL set `DATABASE_URL=postgresql://user:pass@host:5432/db`.
Deploy the `api` image to Render / Railway / Fly.io (free tiers change, check current terms).

## Design decisions worth knowing

| Concern | What the code does |
|---|---|
| **Fake webhooks** | HMAC-SHA256 check with constant-time compare; unsigned requests rejected |
| **Duplicate comments** | `UNIQUE(repo, pr, head_sha)` makes redelivered webhooks no-ops; new commits skip findings already posted (fingerprint ignores line numbers) |
| **Wrong line numbers** | Diff is rendered with new-file line numbers; every LLM finding is validated against lines actually in the diff, snapped if close, otherwise moved to the summary. If GitHub still returns 422, the review falls back to a summary listing the findings |
| **Prompt injection** | Diff/PR text is wrapped in delimiters the text cannot close, labelled untrusted, the model is told to report (not obey) instructions, and the summary warns when injection is suspected |
| **Secrets** | Keys/passwords/tokens are redacted before anything is sent to the LLM; the `[REDACTED_SECRET]` marker still lets the model flag a hard-coded credential |
| **Token limits** | Diffs are chunked by size, huge hunks are split, noisy files are skipped, files capped by `MAX_FILES` |
| **Flaky LLM JSON** | Fence/chatter tolerant extraction, per-finding validation (one bad item doesn't discard the rest), retries with the parse error fed back |
| **Noise** | Min confidence, style comments dropped, dedupe (LLM vs ruff/bandit), severity-sorted cap of `MAX_COMMENTS` |
| **Long reviews** | Webhook returns 202 immediately; the review runs in a background task |

## Known limits / ideas

* Background tasks run in-process; use a real queue (RQ/Celery) for heavy traffic.
* Chunks are reviewed independently (no cross-file reasoning). Static analysis covers Python only.
* Chunk size is measured in characters, not tokens.
* Ideas: semgrep rules, resolve outdated comments on new pushes, per-repo config file, fine-tune
  thresholds from the feedback data.
