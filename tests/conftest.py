from pathlib import Path

import pytest

from app.config import Settings
from app.db import Database

DATASET = Path(__file__).resolve().parent.parent / "eval" / "dataset"


@pytest.fixture
def settings() -> Settings:
    return Settings(llm_provider="mock", enable_static_checks=False,
                    webhook_secret="s3cret", database_url="sqlite:///:memory:")


@pytest.fixture
def db() -> Database:
    return Database("sqlite:///:memory:")


@pytest.fixture
def sqli_diff() -> str:
    return (DATASET / "sql_injection" / "pr.diff").read_text()
