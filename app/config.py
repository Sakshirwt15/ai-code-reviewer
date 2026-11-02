"""Central configuration, read from environment variables (see .env.example)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

try:  # optional convenience for local development
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover
    pass

ROOT_DIR = Path(__file__).resolve().parent.parent
PROMPTS_DIR = ROOT_DIR / "prompts"

# Model names change often: override with LLM_MODEL if one of these is retired.
DEFAULT_MODELS = {
    "anthropic": "claude-sonnet-5-5",
    "gemini": "gemini-3.8-flash",
    "groq": "llama-3.3-70b-versatile",
    "mock": "mock-rules-v1",
}


class ConfigError(RuntimeError):
    """Raised when a required setting is missing."""


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    return default if value is None else value.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    # --- GitHub ---
    github_token: str = ""  # PAT (MVP). Needs: pull requests (rw), contents (r)
    github_app_id: str = ""  # GitHub App (production)
    github_app_private_key: str = ""
    webhook_secret: str = ""
    allow_unsigned_webhooks: bool = False  # NEVER enable in production
    admin_token: str = ""  # protects /feedback/sync

    # --- LLM ---
    llm_provider: str = "mock"  # anthropic | gemini | groq | mock
    llm_model: str = ""
    anthropic_api_key: str = ""
    gemini_api_key: str = ""
    groq_api_key: str = ""
    prompt_version: str = "v2"
    max_retries: int = 2

    # --- Review behaviour ---
    max_comments: int = 10
    min_confidence: float = 0.6
    drop_style_comments: bool = True
    max_chunk_chars: int = 12000
    max_files: int = 40
    enable_static_checks: bool = True
    max_static_files: int = 20

    # --- Storage ---
    database_url: str = "sqlite:///data/reviews.db"

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            github_token=os.getenv("GITHUB_TOKEN", ""),
            github_app_id=os.getenv("GITHUB_APP_ID", ""),
            github_app_private_key=os.getenv("GITHUB_APP_PRIVATE_KEY", "").replace("\\n", "\n"),
            webhook_secret=os.getenv("GITHUB_WEBHOOK_SECRET", ""),
            allow_unsigned_webhooks=_env_bool("ALLOW_UNSIGNED_WEBHOOKS", False),
            admin_token=os.getenv("ADMIN_TOKEN", ""),
            llm_provider=os.getenv("LLM_PROVIDER", "mock").strip().lower(),
            llm_model=os.getenv("LLM_MODEL", ""),
            anthropic_api_key=os.getenv("ANTHROPIC_API_KEY", ""),
            gemini_api_key=os.getenv("GEMINI_API_KEY", ""),
            groq_api_key=os.getenv("GROQ_API_KEY", ""),
            prompt_version=os.getenv("PROMPT_VERSION", "v2"),
            max_retries=_env_int("LLM_MAX_RETRIES", 2),
            max_comments=_env_int("MAX_COMMENTS", 10),
            min_confidence=_env_float("MIN_CONFIDENCE", 0.6),
            drop_style_comments=_env_bool("DROP_STYLE_COMMENTS", True),
            max_chunk_chars=_env_int("MAX_CHUNK_CHARS", 12000),
            max_files=_env_int("MAX_FILES", 40),
            enable_static_checks=_env_bool("ENABLE_STATIC_CHECKS", True),
            max_static_files=_env_int("MAX_STATIC_FILES", 20),
            database_url=os.getenv("DATABASE_URL", "sqlite:///data/reviews.db"),
        )

    @property
    def model_name(self) -> str:
        return self.llm_model or DEFAULT_MODELS.get(self.llm_provider, "unknown")

    @property
    def llm_api_key(self) -> str:
        return {
            "anthropic": self.anthropic_api_key,
            "gemini": self.gemini_api_key,
            "groq": self.groq_api_key,
        }.get(self.llm_provider, "")
