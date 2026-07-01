"""initial schema (Section 6)

Revision ID: 0001_initial
Revises:
Create Date: 2026-07-01
"""
from pathlib import Path

from alembic import op

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None

_DDL = (Path(__file__).resolve().parent.parent / "ddl_0001_initial.sql").read_text()


def upgrade() -> None:
    op.execute(_DDL)


def downgrade() -> None:
    op.execute(
        """
        drop table if exists metrics_daily;
        drop table if exists title_map;
        drop table if exists posting_events;
        drop table if exists job_postings;
        drop table if exists raw_payloads;
        drop table if exists snapshots;
        drop table if exists companies;
        """
    )
