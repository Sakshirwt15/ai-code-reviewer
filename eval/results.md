# Evaluation results

- Date: 2026-11-02 05:50 UTC
- Provider / model: `groq` / `openai/gpt-oss-120b`
- Prompt version: `v2`
- Cases: 7

**Precision 1.00 · Recall 1.00 · F1 1.00** (TP 8, FP 0, FN 0)

| Case | TP | FP | FN | Findings |
|---|---|---|---|---|
| bare_except | 1 | 0 | 0 | app/worker.py:10 medium/bug |
| clean_change | 0 | 0 | 0 | - |
| eval_usage | 1 | 0 | 0 | app/calc.py:7 high/security |
| hardcoded_password | 2 | 0 | 0 | app/settings.py:6 high/security, app/settings.py:7 high/security |
| prompt_injection | 2 | 0 | 0 | app/handler.py:11 high/security, app/handler.py:3 medium/security |
| sql_injection | 1 | 0 | 0 | app/users.py:10 high/security |
| tls_verify_off | 1 | 0 | 0 | app/client.py:5 medium/security |
