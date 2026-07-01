"""Pytest fixtures. Tests run against a real Postgres (schema-faithful).

Point HST_TEST_DATABASE_URL at a throwaway Postgres database. Defaults to a
local cluster on port 5433 (see the project README's test setup).
"""

from __future__ import annotations

import os

os.environ.setdefault(
    "DATABASE_URL",
    os.environ.get(
        "HST_TEST_DATABASE_URL",
        "postgresql+psycopg://postgres@127.0.0.1:5433/hiring_tracker_test",
    ),
)
os.environ.setdefault("HST_LIVE", "0")

import pytest  # noqa: E402
from sqlalchemy import text  # noqa: E402

from hiring_tracker import db  # noqa: E402
from hiring_tracker.config import get_settings  # noqa: E402
from hiring_tracker.models import Base  # noqa: E402

_TABLES = (
    "posting_events",
    "raw_payloads",
    "job_postings",
    "snapshots",
    "metrics_daily",
    "title_map",
    "companies",
)


@pytest.fixture(scope="session")
def engine():
    get_settings.cache_clear()
    db.reset_engine()
    e = db.get_engine()
    Base.metadata.drop_all(e)
    Base.metadata.create_all(e)
    yield e
    db.reset_engine()


@pytest.fixture
def session(engine):
    with engine.begin() as c:
        c.execute(
            text(f"truncate {', '.join(_TABLES)} restart identity cascade")
        )
    s = db.get_session_factory()()
    try:
        yield s
    finally:
        s.close()
