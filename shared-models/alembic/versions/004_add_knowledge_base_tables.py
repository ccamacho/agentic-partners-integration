"""Add knowledge base tables for pgvector

Adds:
- pgvector extension for vector similarity search
- knowledge_documents table for RAG document storage
- Indexes for efficient similarity search

Revision ID: 004
Revises: 003
Create Date: 2026-03-04 12:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "004"
down_revision: Union[str, None] = "003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade database schema."""
    # Enable pgvector extension
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # Create knowledge_documents table
    op.create_table(
        "knowledge_documents",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("knowledge_base", sa.String(255), nullable=False),
        sa.Column("document_id", sa.String(255), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("metadata", postgresql.JSONB(), nullable=True),
        sa.Column(
            "embedding",
            postgresql.ARRAY(sa.Float()),
            nullable=True,
        ),  # Using ARRAY for compatibility
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
            nullable=False,
        ),
    )

    # Create index for knowledge base lookups
    op.create_index(
        "idx_knowledge_base_name",
        "knowledge_documents",
        ["knowledge_base"],
        unique=False,
    )

    # Create index for document_id lookups
    op.create_index(
        "idx_document_id",
        "knowledge_documents",
        ["document_id"],
        unique=False,
    )

    # Create unique composite index for knowledge_base + document_id
    # This prevents duplicate documents and enables ON CONFLICT during ingestion
    op.create_index(
        "idx_kb_document",
        "knowledge_documents",
        ["knowledge_base", "document_id"],
        unique=True,
    )

    # Note: IVFFlat index for vector similarity search will be created
    # after documents are loaded (requires data for clustering)
    # Can be created manually with:
    # CREATE INDEX idx_knowledge_documents_embedding ON knowledge_documents
    # USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);


def downgrade() -> None:
    """Downgrade database schema."""
    # Drop indexes
    op.drop_index("idx_kb_document", table_name="knowledge_documents")
    op.drop_index("idx_document_id", table_name="knowledge_documents")
    op.drop_index("idx_knowledge_base_name", table_name="knowledge_documents")

    # Drop table
    op.drop_table("knowledge_documents")

    # Drop pgvector extension (be careful - other tables might use it)
    op.execute("DROP EXTENSION IF EXISTS vector")
