"""optional pgvector layer (Phase 4)

Revision ID: 0002_pgvector
Revises: 0001_initial
Create Date: 2026-07-01

pgvector is optional (Section 6 / Phase 4). To keep `alembic upgrade head` working
on any Postgres, this migration is a no-op unless HST_ENABLE_PGVECTOR is truthy.
Enable it only where the server actually has the `vector` extension available
(e.g. Supabase, or a local build of pgvector):

    HST_ENABLE_PGVECTOR=1 alembic upgrade head
"""
import os
from pathlib import Path

from alembic import op

revision = "0002_pgvector"
down_revision = "0001_initial"
branch_labels = None
depends_on = None

_DDL = (Path(__file__).resolve().parent.parent / "ddl_0002_pgvector.sql").read_text()


def _enabled() -> bool:
    return os.environ.get("HST_ENABLE_PGVECTOR", "").strip().lower() in {"1", "true", "yes", "on"}


def upgrade() -> None:
    if _enabled():
        op.execute(_DDL)
    else:
        print("0002_pgvector: skipped (set HST_ENABLE_PGVECTOR=1 to enable)")


def downgrade() -> None:
    if _enabled():
        op.execute("drop table if exists posting_embeddings;")
