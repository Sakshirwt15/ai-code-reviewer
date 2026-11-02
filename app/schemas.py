"""Pydantic models: the contract between the LLM, the filters and GitHub."""
from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator

Severity = Literal["high", "medium", "low"]
Category = Literal["bug", "security", "performance", "style", "tests", "other"]
Source = Literal["llm", "ruff", "bandit"]

_SEVERITY_ALIASES = {
    "critical": "high", "major": "high", "error": "high",
    "warning": "medium", "moderate": "medium",
    "minor": "low", "info": "low", "nit": "low",
}
_CATEGORY_ALIASES = {
    "vulnerability": "security", "test": "tests", "testing": "tests", "perf": "performance",
    "readability": "style", "maintainability": "style", "naming": "style",
    "logic": "bug", "error": "bug", "correctness": "bug",
}
_CATEGORIES = {"bug", "security", "performance", "style", "tests", "other"}


class Finding(BaseModel):
    """One review comment, anchored to a line in the NEW version of a file."""

    file: str
    line: int = Field(ge=1)
    severity: Severity
    category: Category
    message: str = Field(min_length=3)
    suggestion: str | None = None
    confidence: float = Field(default=0.7, ge=0.0, le=1.0)
    source: Source = "llm"

    @field_validator("file", mode="before")
    @classmethod
    def _strip_file(cls, v: object) -> str:
        return str(v).strip()

    @field_validator("line", mode="before")
    @classmethod
    def _line(cls, v: object) -> int:
        match = re.search(r"\d+", str(v))  # tolerate "12", 12.0, "12-15"
        if not match:
            raise ValueError("line must contain a number")
        return int(match.group())

    @field_validator("severity", mode="before")
    @classmethod
    def _severity(cls, v: object) -> str:
        s = str(v).strip().lower()
        return _SEVERITY_ALIASES.get(s, s)

    @field_validator("category", mode="before")
    @classmethod
    def _category(cls, v: object) -> str:
        s = str(v).strip().lower()
        if s in _CATEGORIES:
            return s
        return _CATEGORY_ALIASES.get(s, "other")

    @field_validator("confidence", mode="before")
    @classmethod
    def _confidence(cls, v: object) -> float:
        try:
            f = float(str(v).strip().rstrip("%"))
        except ValueError:
            return 0.7
        if f > 1:  # model answered 85 instead of 0.85
            f /= 100
        return max(0.0, min(1.0, f))

    @field_validator("suggestion", mode="before")
    @classmethod
    def _suggestion(cls, v: object) -> str | None:
        if v is None:
            return None
        s = str(v).strip()
        return s or None


class ReviewResult(BaseModel):
    summary: str = ""
    findings: list[Finding] = Field(default_factory=list)
    dropped_invalid: int = 0  # findings the LLM produced that failed validation
