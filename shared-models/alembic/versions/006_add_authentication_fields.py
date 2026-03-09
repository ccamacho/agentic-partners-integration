"""Add authentication fields to users table

Revision ID: 006
Revises: 005
Create Date: 2026-03-05 17:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import TIMESTAMP


# revision identifiers, used by Alembic.
revision: str = '006'
down_revision: Union[str, None] = '005'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add authentication fields to users table."""
    # Add password_hash field (nullable initially for existing users)
    op.add_column('users', sa.Column('password_hash', sa.String(255), nullable=True))

    # Add last_login timestamp
    op.add_column('users', sa.Column('last_login', TIMESTAMP(timezone=True), nullable=True))

    # Add is_active boolean (default True)
    op.add_column('users', sa.Column('is_active', sa.Boolean(), nullable=False, server_default='true'))

    # Add index on is_active for faster queries
    op.create_index('ix_users_is_active', 'users', ['is_active'])


def downgrade() -> None:
    """Remove authentication fields from users table."""
    op.drop_index('ix_users_is_active', 'users')
    op.drop_column('users', 'is_active')
    op.drop_column('users', 'last_login')
    op.drop_column('users', 'password_hash')
