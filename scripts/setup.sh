#!/bin/bash
# Complete setup - builds, starts, and initializes everything

set -e

echo "════════════════════════════════════════════════════════════"
echo "🚀 Partner Agent System - Complete Setup"
echo "════════════════════════════════════════════════════════════"
echo ""

# Get project root (parent of scripts/)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

# Auto-load .env if GOOGLE_API_KEY not set
if [ -z "$GOOGLE_API_KEY" ] && [ -f ".env" ]; then
    echo "Loading GOOGLE_API_KEY from .env..."
    source .env
fi

# Check prerequisites
if [ -z "$GOOGLE_API_KEY" ]; then
    echo "❌ ERROR: GOOGLE_API_KEY not set"
    echo ""
    echo "Set it first:"
    echo "  export GOOGLE_API_KEY=your-api-key"
    exit 1
fi

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

# ============================================
# 1. BUILD CONTAINERS (FRESH)
# ============================================
echo -e "${YELLOW}📦 Building fresh containers...${NC}"
bash scripts/build_containers.sh

# ============================================
# 2. START INFRASTRUCTURE
# ============================================
echo ""
echo -e "${YELLOW}🔧 Starting infrastructure...${NC}"

# Create network
docker network inspect partner-agent-network > /dev/null 2>&1 || \
    docker network create partner-agent-network

# Start PostgreSQL
docker rm -f partner-postgres-full 2>/dev/null || true
docker run -d \
    --name partner-postgres-full \
    --network partner-agent-network \
    -e POSTGRES_USER=user \
    -e POSTGRES_PASSWORD=pass \
    -e POSTGRES_DB=partner_agent \
    -p 5433:5432 \
    pgvector/pgvector:pg16
echo "  ✓ PostgreSQL started"

# Start ChromaDB
docker rm -f partner-chromadb-full 2>/dev/null || true
docker run -d \
    --name partner-chromadb-full \
    --network partner-agent-network \
    -p 8002:8000 \
    chromadb/chroma:latest
echo "  ✓ ChromaDB started"

# Wait for databases
echo "  ⏳ Waiting for PostgreSQL to be ready..."
for i in {1..30}; do
    if docker exec partner-postgres-full pg_isready -U user -d partner_agent >/dev/null 2>&1; then
        echo "  ✓ PostgreSQL ready"
        break
    fi
    sleep 1
    if [ $i -eq 30 ]; then
        echo "  ❌ PostgreSQL failed to start"
        exit 1
    fi
done

# ============================================
# 3. RUN DATABASE MIGRATIONS
# ============================================
echo ""
echo -e "${YELLOW}🗄️  Running database migrations...${NC}"
docker run --rm \
    --name partner-migrations-temp \
    --network partner-agent-network \
    -e DATABASE_URL=postgresql+asyncpg://user:pass@partner-postgres-full:5432/partner_agent \
    -w /app/shared-models \
    partner-request-manager:latest \
    python3 -m alembic upgrade head

echo "  ✓ Migrations complete"

# Create LangGraph checkpoint tables (required for state persistence)
echo "  Creating LangGraph checkpoint tables..."
docker exec partner-postgres-full psql -U user -d partner_agent -c "
CREATE TABLE IF NOT EXISTS checkpoints (
    thread_id TEXT NOT NULL,
    checkpoint_ns TEXT NOT NULL DEFAULT '',
    checkpoint_id TEXT NOT NULL,
    parent_checkpoint_id TEXT,
    type TEXT,
    checkpoint JSONB NOT NULL,
    metadata_ JSONB NOT NULL DEFAULT '{}',
    PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id)
);
CREATE TABLE IF NOT EXISTS checkpoint_blobs (
    thread_id TEXT NOT NULL,
    checkpoint_ns TEXT NOT NULL DEFAULT '',
    channel TEXT NOT NULL,
    version TEXT NOT NULL,
    type TEXT NOT NULL,
    blob BYTEA,
    PRIMARY KEY (thread_id, checkpoint_ns, channel, version)
);
CREATE TABLE IF NOT EXISTS checkpoint_writes (
    thread_id TEXT NOT NULL,
    checkpoint_ns TEXT NOT NULL DEFAULT '',
    checkpoint_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    idx INTEGER NOT NULL,
    channel TEXT NOT NULL,
    type TEXT,
    blob BYTEA NOT NULL,
    PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id, task_id, idx)
);
CREATE TABLE IF NOT EXISTS checkpoint_migrations (
    v INTEGER NOT NULL PRIMARY KEY
);
" > /dev/null 2>&1
echo "  ✓ LangGraph checkpoint tables created"

# ============================================
# 4. START SERVICES
# ============================================
echo ""
echo -e "${YELLOW}⚙️  Starting services...${NC}"

# Agent Service
docker rm -f partner-agent-service-full 2>/dev/null || true
docker run -d \
    --name partner-agent-service-full \
    --network partner-agent-network \
    -e DATABASE_URL=postgresql+asyncpg://user:pass@partner-postgres-full:5432/partner_agent \
    -e LLM_BACKEND=gemini \
    -e GOOGLE_API_KEY="${GOOGLE_API_KEY}" \
    -e GEMINI_MODEL=gemini-2.5-flash \
    -e LOG_LEVEL=INFO \
    -e EXPECTED_MIGRATION_VERSION=007 \
    -e RAG_API_ENDPOINT=http://partner-rag-api-full:8080/answer \
    -p 8001:8080 \
    partner-agent-service:latest
echo "  ✓ Agent service starting..."

# Request Manager
docker rm -f partner-request-manager-full 2>/dev/null || true
docker run -d \
    --name partner-request-manager-full \
    --network partner-agent-network \
    -e DATABASE_URL=postgresql+asyncpg://user:pass@partner-postgres-full:5432/partner_agent \
    -e LLM_BACKEND=gemini \
    -e GOOGLE_API_KEY="${GOOGLE_API_KEY}" \
    -e GEMINI_MODEL=gemini-2.5-flash \
    -e AGENT_SERVICE_URL=http://partner-agent-service-full:8080 \
    -e AGENT_TIMEOUT=120 \
    -e LOG_LEVEL=INFO \
    -e STRUCTURED_CONTEXT_ENABLED=true \
    -e JWT_EXPIRATION_MINUTES=5 \
    -e EXPECTED_MIGRATION_VERSION=007 \
    -p 8000:8080 \
    partner-request-manager:latest
echo "  ✓ Request manager starting (JWT tokens expire in 5 minutes)..."

# RAG API (must have GOOGLE_API_KEY for embeddings)
docker rm -f partner-rag-api-full 2>/dev/null || true
docker run -d \
    --name partner-rag-api-full \
    --network partner-agent-network \
    -e "GOOGLE_API_KEY=${GOOGLE_API_KEY}" \
    -e CHROMA_HOST=partner-chromadb-full \
    -e CHROMA_PORT=8000 \
    -e EMBEDDING_MODEL=models/gemini-embedding-001 \
    -e LLM_MODEL=gemini-2.5-flash \
    -p 8003:8080 \
    partner-rag-api:latest
echo "  ✓ RAG API starting..."

# Wait for services
echo "  ⏳ Waiting for services to be ready..."
for i in {1..30}; do
    if curl -s http://localhost:8001/health > /dev/null 2>&1 && \
       curl -s http://localhost:8000/health > /dev/null 2>&1; then
        break
    fi
    sleep 2
done

# ============================================
# 5. INITIALIZE DATA
# ============================================
echo ""
echo -e "${YELLOW}💾 Initializing data...${NC}"

# Copy scripts
docker cp scripts/setup_aaa_users.py partner-request-manager-full:/tmp/
docker cp rag-service/ingest_knowledge.py partner-rag-api-full:/app/
docker cp data partner-rag-api-full:/app/ 2>/dev/null || true

# Create users
echo "  👥 Creating users..."
docker exec partner-request-manager-full python /tmp/setup_aaa_users.py > /dev/null 2>&1 || true

# Fix Carlos (UI requires software-only) and setup Josh (no agents)
docker exec partner-request-manager-full python -c "
import asyncio, sys
sys.path.insert(0, '/app/shared-models/src')
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from shared_models.aaa_service import AAAService
from shared_models.auth_service import AuthService
from shared_models.models import UserRole

async def fix():
    engine = create_async_engine('postgresql+asyncpg://user:pass@partner-postgres-full:5432/partner_agent', echo=False)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as db:
        await AAAService.update_user_permissions(db, user_email='carlos@example.com', allowed_agents=['software-support'])
        # Create Josh with no agent access
        await AAAService.get_or_create_user(db, email='josh@example.com', role=UserRole.USER, organization='Customer', department='Intern')
        await AAAService.update_user_permissions(db, user_email='josh@example.com', allowed_agents=[])
        await AuthService.set_user_password(db, 'josh@example.com', 'josh123')

asyncio.run(fix())
" 2>/dev/null || true

echo "  ✓ Users created"

# Ingest RAG knowledge
echo "  📚 Ingesting RAG knowledge..."
docker exec -e GOOGLE_API_KEY="${GOOGLE_API_KEY}" partner-rag-api-full python /app/ingest_knowledge.py > /dev/null 2>&1 || true
echo "  ✓ RAG knowledge ingested"

# Start PF Chat UI
echo "  🌐 Starting PF Chat UI..."
docker rm -f partner-pf-chat-ui 2>/dev/null || true
docker run -d \
    --name partner-pf-chat-ui \
    --network partner-agent-network \
    -p 3000:8080 \
    partner-pf-chat-ui:latest
echo "  ✓ PF Chat UI started"

# ============================================
# 6. DONE
# ============================================
echo ""
echo "════════════════════════════════════════════════════════════"
echo -e "${GREEN}✅ Setup Complete!${NC}"
echo "════════════════════════════════════════════════════════════"
echo ""
echo "🌐 Services Running:"
echo "  • Web UI:     http://localhost:3000"
echo "  • API:        http://localhost:8000"
echo "  • Agent:      http://localhost:8001"
echo "  • RAG API:    http://localhost:8003"
echo ""
echo "🔐 Login Credentials:"
echo "  • carlos@example.com / carlos123 (Software Support)"
echo "  • luis@example.com / luis123 (Network Support)"
echo "  • sharon@example.com / sharon123 (Admin)"
echo "  • josh@example.com / josh123 (No Access)"
echo ""
echo "🧪 Next Steps:"
echo "  • Test: bash scripts/test.sh"
echo "  • Logs: docker logs -f partner-request-manager-full"
echo "  • Stop: docker stop partner-{postgres,chromadb,agent-service,request-manager,rag-api,pf-chat-ui}-full"
echo ""
