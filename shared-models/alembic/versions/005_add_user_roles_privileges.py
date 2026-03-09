"""Add user roles and privileges for AAA

Revision ID: 005
Revises: 004
Create Date: 2026-03-05 15:00:00

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '005'
down_revision = '004'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create role enum
    role_enum = postgresql.ENUM(
        'admin', 'manager', 'engineer', 'support_staff', 'user',
        name='user_role',
        create_type=True
    )
    role_enum.create(op.get_bind(), checkfirst=True)

    # Add role column to users table
    op.add_column('users',
        sa.Column('role',
                  sa.Enum('admin', 'manager', 'engineer', 'support_staff', 'user',
                         name='user_role'),
                  nullable=False,
                  server_default='user')
    )

    # Add privileges JSON column
    op.add_column('users',
        sa.Column('privileges',
                  postgresql.JSONB(astext_type=sa.Text()),
                  nullable=False,
                  server_default='{}')
    )

    # Add agent access control JSON column
    op.add_column('users',
        sa.Column('allowed_agents',
                  postgresql.JSONB(astext_type=sa.Text()),
                  nullable=False,
                  server_default='[]')
    )

    # Add status column for user account management
    op.add_column('users',
        sa.Column('status',
                  sa.String(20),
                  nullable=False,
                  server_default='active')
    )

    # Add organization/department for grouping
    op.add_column('users',
        sa.Column('organization',
                  sa.String(255),
                  nullable=True)
    )

    op.add_column('users',
        sa.Column('department',
                  sa.String(255),
                  nullable=True)
    )

    # Create index on role for faster queries
    op.create_index('idx_users_role', 'users', ['role'])
    op.create_index('idx_users_status', 'users', ['status'])
    op.create_index('idx_users_organization', 'users', ['organization'])


def downgrade() -> None:
    # Drop indexes
    op.drop_index('idx_users_organization', table_name='users')
    op.drop_index('idx_users_status', table_name='users')
    op.drop_index('idx_users_role', table_name='users')

    # Drop columns
    op.drop_column('users', 'department')
    op.drop_column('users', 'organization')
    op.drop_column('users', 'status')
    op.drop_column('users', 'allowed_agents')
    op.drop_column('users', 'privileges')
    op.drop_column('users', 'role')

    # Drop enum type
    sa.Enum(name='user_role').drop(op.get_bind(), checkfirst=True)
