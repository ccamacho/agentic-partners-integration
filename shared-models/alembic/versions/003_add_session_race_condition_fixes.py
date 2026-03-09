"""Add race condition fixes for session management

Adds:
- Version field for optimistic locking
- Unique constraint on (user_id, integration_type) where status='ACTIVE'
- Index for the unique constraint

Revision ID: 003
Revises: 002
Create Date: 2024-12-19 12:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "003"
down_revision: Union[str, None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade database schema."""
    # Add version column for optimistic locking
    op.add_column(
        "request_sessions",
        sa.Column("version", sa.Integer(), nullable=False, server_default="0"),
    )

    # Create unique partial index to prevent multiple active sessions per user/integration
    # This uses PostgreSQL's partial index feature (WHERE clause)
    op.execute(
        """
        CREATE UNIQUE INDEX idx_one_active_session_per_user_integration
        ON request_sessions (user_id, integration_type)
        WHERE status = 'ACTIVE'
        """
    )

    # Add index on version for faster lookups during optimistic locking
    op.create_index(
        "ix_request_sessions_version",
        "request_sessions",
        ["version"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade database schema."""
    # Drop index
    op.drop_index("ix_request_sessions_version", table_name="request_sessions")

    # Drop unique constraint
    op.execute("DROP INDEX IF EXISTS idx_one_active_session_per_user_integration")

    # Drop version column
    op.drop_column("request_sessions", "version")
