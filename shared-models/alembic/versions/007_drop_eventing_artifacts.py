"""Drop CloudEvents/eventing artifacts (processed_events table, cloudevent columns)

Revision ID: 007
Revises: 006
Create Date: 2026-03-06 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "007"
down_revision: Union[str, None] = "006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_index("ix_processed_events_event_id", table_name="processed_events")
    op.drop_index("ix_processed_events_request_id", table_name="processed_events")
    op.drop_index("ix_processed_events_created_at", table_name="processed_events")
    op.drop_table("processed_events")

    op.drop_column("request_logs", "cloudevent_id")
    op.drop_column("request_logs", "cloudevent_type")


def downgrade() -> None:
    op.add_column(
        "request_logs",
        sa.Column("cloudevent_id", sa.String(36), nullable=True),
    )
    op.add_column(
        "request_logs",
        sa.Column("cloudevent_type", sa.String(100), nullable=True),
    )

    op.create_table(
        "processed_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("event_id", sa.String(255), nullable=False, unique=True),
        sa.Column("event_type", sa.String(255), nullable=False),
        sa.Column("event_source", sa.String(255), nullable=False),
        sa.Column("request_id", sa.String(255), nullable=True),
        sa.Column("session_id", sa.String(255), nullable=True),
        sa.Column("processed_by", sa.String(100), nullable=False),
        sa.Column("processing_result", sa.String(50), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_processed_events_event_id", "processed_events", ["event_id"])
    op.create_index("ix_processed_events_request_id", "processed_events", ["request_id"])
    op.create_index("ix_processed_events_created_at", "processed_events", ["created_at"])
