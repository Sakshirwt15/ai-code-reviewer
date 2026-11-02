# Interview notes

**Problem.** Senior engineers spend a lot of review time on small, mechanical issues (missing null
checks, SQL injection, hard-coded passwords). An AI first pass lets humans focus on design and
correctness.

**30-second pitch.** A GitHub webhook triggers a FastAPI service that fetches the PR diff, filters
noise, chunks it, combines ruff/bandit with an LLM review that returns strict JSON, validates and
filters the findings, posts inline comments, and records everything for a dashboard and
evaluation.

**Why static analysis + LLM?** Static tools give precise, grounded hits; the LLM adds reasoning
about intent and context. Passing tool output as hints reduces hallucination.

**Hard problems I solved.**
1. *Line mapping*: GitHub only accepts comments on lines visible in the diff. I track new-file line
   numbers, show them to the model, and validate every finding.
2. *Idempotency*: webhooks are redelivered; a unique (repo, PR, SHA) row prevents duplicates.
3. *Security of the bot itself*: HMAC verification, secret redaction, prompt-injection handling.
4. *Quality, not volume*: confidence threshold, dedupe, cap, drop style nits.
5. *Measuring it*: offline eval (precision/recall/F1) + live 👍/👎 helpful rate.

**Trade-offs to mention.** In-process background tasks vs a queue; character-based chunking vs a
tokenizer; per-chunk review loses cross-file context; mock baseline is optimistic.

**Metrics to quote (only real ones).** Run `python -m eval.run_eval --provider <yours>` and the
dashboard's helpful rate after real usage. Do not quote numbers you did not measure.
