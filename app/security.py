"""Security helpers: webhook signatures, secret redaction, prompt-injection hygiene."""
from __future__ import annotations

import hashlib
import hmac
import re


def verify_signature(secret: str, body: bytes, signature_header: str | None) -> bool:
    """Verify GitHub's X-Hub-Signature-256 header (HMAC-SHA256 of the raw body)."""
    if not secret or not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature_header)


# --- Secret redaction (applied BEFORE any code is sent to the LLM) ---------------------
# We keep the *shape* ("PASSWORD = '[REDACTED_SECRET]'") so the model can still tell the
# author hard-coded a credential, but it never sees the real value.
_SECRET_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"), "[REDACTED_PRIVATE_KEY]"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "[REDACTED_AWS_KEY]"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b"), "[REDACTED_GITHUB_TOKEN]"),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"), "[REDACTED_GITHUB_TOKEN]"),
    (re.compile(r"\bsk-[A-Za-z0-9_\-]{20,}\b"), "[REDACTED_API_KEY]"),
    (re.compile(r"\bxox[baprs]-[A-Za-z0-9\-]{10,}\b"), "[REDACTED_SLACK_TOKEN]"),
    (re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b"), "[REDACTED_GOOGLE_KEY]"),
    (
        re.compile(
            r"(?i)\b(\w*(?:password|passwd|pwd|secret|api[_-]?key|token|access[_-]?key"
            r"|private[_-]?key)\w*)(\s*[:=]\s*)([\"'])([^\"'\n]{4,})\3"
        ),
        r"\1\2\3[REDACTED_SECRET]\3",
    ),
    # body lines of a PEM block (long base64-only lines)
    (re.compile(r"^[A-Za-z0-9+/=]{48,}$"), "[REDACTED_BASE64]"),
]


def redact_line(text: str) -> str:
    for pattern, replacement in _SECRET_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def redact_text(text: str) -> str:
    return "\n".join(redact_line(line) for line in text.splitlines())


# --- Prompt injection --------------------------------------------------------------------
_INJECTION_PATTERNS = [
    re.compile(r"ignore\s+(all\s+|any\s+)?(the\s+)?(previous|prior|above|earlier)\s+(instructions|rules|prompts?)", re.I),
    re.compile(r"disregard\s+.{0,40}(instructions|rules|prompt)", re.I),
    re.compile(r"(note|message)\s+to\s+(the\s+)?(ai|llm|bot|reviewer|assistant)", re.I),
    re.compile(r"\b(approve|lgtm)\b.{0,30}\b(this|the)\s+(pr|pull request|change)", re.I),
    re.compile(r"you\s+are\s+now\s+(a|an|the)\b", re.I),
    re.compile(r"(reveal|print|show)\s+(your\s+)?(system\s+)?prompt", re.I),
]


def looks_like_injection(text: str) -> bool:
    return any(p.search(text) for p in _INJECTION_PATTERNS)


_DELIMITER_RE = re.compile(r"<\s*(/?)\s*(diff|static_analysis)\s*>", re.I)


def neutralize(text: str) -> str:
    """Stop untrusted text from closing our <diff>/<static_analysis> delimiters."""
    return _DELIMITER_RE.sub(lambda m: f"&lt;{m.group(1)}{m.group(2)}&gt;", text)
