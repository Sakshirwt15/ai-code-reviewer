"""Thin LLM clients (plain HTTP, no heavy SDKs) + an offline rule-based mock."""
from __future__ import annotations

import json
import re
import time
from typing import Protocol

import httpx

from .config import ConfigError, Settings
from .security import looks_like_injection


class LLMError(RuntimeError):
    """The LLM provider failed (network, auth, rate limit, bad response)."""


class LLMClient(Protocol):
    name: str
    model: str

    def complete(self, system: str, user: str, max_tokens: int = 4096) -> str: ...


def _post_json(url: str, headers: dict[str, str], payload: dict, timeout: float = 300.0,
               attempts: int = 3) -> dict:
    last = ""
    for attempt in range(attempts):
        try:
            resp = httpx.post(url, headers=headers, json=payload, timeout=timeout)
        except httpx.HTTPError as exc:
            last = f"network error: {exc}"
        else:
            if resp.status_code < 400:
                try:
                    return resp.json()
                except ValueError as exc:
                    raise LLMError(f"non-JSON response: {exc}") from exc
            last = f"HTTP {resp.status_code}: {resp.text[:300]}"
            if resp.status_code not in (408, 409, 429, 500, 502, 503, 504, 529):
                break  # auth / bad request: retrying will not help
        time.sleep(10 *2 ** attempt)
    raise LLMError(last)


class AnthropicClient:
    name = "anthropic"

    def __init__(self, api_key: str, model: str):
        self.api_key, self.model = api_key, model

    def complete(self, system: str, user: str, max_tokens: int = 4096) -> str:
        data = _post_json(
            "https://api.anthropic.com/v1/messages",
            {"x-api-key": self.api_key, "anthropic-version": "2023-06-01",
             "content-type": "application/json"},
            {"model": self.model, "max_tokens": max_tokens, "system": system,
             "messages": [{"role": "user", "content": user}]},
        )
        try:
            return "".join(b.get("text", "") for b in data["content"] if b.get("type") == "text")
        except (KeyError, TypeError) as exc:
            raise LLMError(f"unexpected Anthropic response: {str(data)[:200]}") from exc


class GeminiClient:
    name = "gemini"

    def __init__(self, api_key: str, model: str):
        self.api_key, self.model = api_key, model

    def complete(self, system: str, user: str, max_tokens: int = 4096) -> str:
        data = _post_json(
            f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent",
            {"x-goog-api-key": self.api_key, "content-type": "application/json"},
            {"systemInstruction": {"parts": [{"text": system}]},
             "contents": [{"role": "user", "parts": [{"text": user}]}],
             "generationConfig": {"temperature": 0, "maxOutputTokens": max_tokens,
                                  "responseMimeType": "application/json"}},
        )
        try:
            parts = data["candidates"][0]["content"]["parts"]
            return "".join(p.get("text", "") for p in parts)
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"unexpected Gemini response: {str(data)[:200]}") from exc


class GroqClient:
    """Groq exposes an OpenAI-compatible API."""

    name = "groq"

    def __init__(self, api_key: str, model: str):
        self.api_key, self.model = api_key, model

    def complete(self, system: str, user: str, max_tokens: int = 4096) -> str:
        data = _post_json(
            "https://api.groq.com/openai/v1/chat/completions",
            {"Authorization": f"Bearer {self.api_key}", "content-type": "application/json"},
            {"model": self.model, "temperature": 0, "max_tokens": max_tokens,
             "response_format": {"type": "json_object"},
             "messages": [{"role": "system", "content": system},
                          {"role": "user", "content": user}]},
        )
        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"unexpected Groq response: {str(data)[:200]}") from exc


# --------------------------------------------------------------------------------------
# Mock: deterministic regex "reviewer". Lets you run the whole pipeline, the tests and the
# eval harness with no API key. Also a nice baseline to compare real models against.
# --------------------------------------------------------------------------------------
_FILE_RE = re.compile(r"^### File: (.+?) \(\w+\)$")
_ADD_RE = re.compile(r"^\s*(\d+) \+ (.*)$")
_MOCK_RULES: list[tuple[re.Pattern[str], str, str, str, str, str, float]] = [
    (re.compile(r"\b(eval|exec)\s*\("), "high", "security",
     "`eval`/`exec` on dynamic input allows arbitrary code execution.",
     "Use `ast.literal_eval` or a proper parser instead.", "", 0.9),
    (re.compile(r"\b(SELECT|INSERT|UPDATE|DELETE)\b.*(\{|%\s*\(|\.format\(|\"\s*\+\s*\w)", re.I),
     "high", "security",
     "SQL query is built with string interpolation, which is vulnerable to SQL injection.",
     "Use parameterized queries (placeholders) and pass values separately.", "", 0.9),
    (re.compile(r"(?i)\w*(password|passwd|secret|api[_-]?key|token)\w*\s*=\s*[\"'][^\"']{4,}[\"']"),
     "high", "security",
     "A credential looks hard-coded in source control.",
     "Load it from an environment variable or a secrets manager and rotate the leaked value.",
     "", 0.9),
    (re.compile(r"^\s*except\s*:"), "medium", "bug",
     "Bare `except:` swallows every error, including KeyboardInterrupt, and hides failures.",
     "Catch specific exceptions and log or re-raise.", "", 0.8),
    (re.compile(r"verify\s*=\s*False"), "medium", "security",
     "TLS certificate verification is disabled, which allows man-in-the-middle attacks.",
     "Remove `verify=False` or point `verify` at a CA bundle.", "", 0.85),
    (re.compile(r"\b(md5|sha1)\s*\(", re.I), "medium", "security",
     "MD5/SHA-1 are weak hashes and unsuitable for security purposes.",
     "Use SHA-256 or a password hashing function such as bcrypt/argon2.", "", 0.75),
]


class MockClient:
    name = "mock"
    model = "mock-rules-v1"

    def complete(self, system: str, user: str, max_tokens: int = 4096) -> str:
        if "Merge these partial summaries" in system:
            return "Reviewed the changes across several chunks."
        start, end = user.find("<diff>"), user.rfind("</diff>")
        body = user[start + 6:end] if start != -1 and end != -1 else user
        findings, path, files = [], None, set()
        for raw in body.splitlines():
            fm = _FILE_RE.match(raw)
            if fm:
                path = fm.group(1)
                files.add(path)
                continue
            am = _ADD_RE.match(raw)
            if not am or path is None:
                continue
            if looks_like_injection(am.group(2)):
                findings.append({"file": path, "line": int(am.group(1)), "severity": "medium",
                                 "category": "security",
                                 "message": "Comment contains instructions aimed at AI reviewers (prompt injection attempt).",
                                 "suggestion": "Remove it; code comments must not steer the reviewer.",
                                 "confidence": 0.8})
                continue
            if am.group(2).lstrip().startswith(("#", "//")):
                continue
            for rx, sev, cat, msg, sugg, _, conf in _MOCK_RULES:
                if rx.search(am.group(2)):
                    findings.append({"file": path, "line": int(am.group(1)), "severity": sev,
                                     "category": cat, "message": msg, "suggestion": sugg,
                                     "confidence": conf})
                    break
        summary = f"Rule-based review of {len(files)} file(s): {len(findings)} potential issue(s)."
        return json.dumps({"summary": summary, "findings": findings})


def make_llm_client(settings: Settings) -> LLMClient:
    provider = settings.llm_provider
    if provider == "mock":
        return MockClient()
    classes = {"anthropic": AnthropicClient, "gemini": GeminiClient, "groq": GroqClient}
    if provider not in classes:
        raise ConfigError(f"Unknown LLM_PROVIDER '{provider}' (use anthropic|gemini|groq|mock)")
    if not settings.llm_api_key:
        raise ConfigError(f"Missing API key for provider '{provider}'")
    return classes[provider](settings.llm_api_key, settings.model_name)
