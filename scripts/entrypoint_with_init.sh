#!/bin/bash
# Container entrypoint that initializes data before starting the service
# This is for use in production/k8s deployments

set -e

SERVICE_NAME="${SERVICE_NAME:-unknown}"
MODULE_NAME="${MODULE_NAME:-app.main}"

echo "🚀 Starting ${SERVICE_NAME} with data initialization"

# If this is request-manager, initialize users
if [ "$SERVICE_NAME" = "request-manager" ]; then
    echo "📋 Initializing users (request-manager)..."
    
    # Wait a bit for database to be ready
    sleep 5
    
    # Run user setup (idempotent - safe to run multiple times)
    if [ -f "/app/scripts/setup_aaa_users.py" ]; then
        python3 /app/scripts/setup_aaa_users.py 2>&1 | grep -E "✓|Created|Updated|ERROR" || true
        
        # Fix Carlos permissions for UI
        python3 << 'PYEOF'
import asyncio, os, sys
sys.path.insert(0, "/app/shared-models/src")
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from shared_models.aaa_service import AAAService

async def fix():
    try:
        engine = create_async_engine(os.getenv("DATABASE_URL"), echo=False)
        async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with async_session() as db:
            await AAAService.update_user_permissions(
                db, 
                user_email="carlos@example.com", 
                allowed_agents=["software-support"]
            )
            print("✓ Configured UI users")
    except Exception as e:
        print(f"⚠️  UI user config: {e}")

asyncio.run(fix())
PYEOF
    fi
    
    echo "✅ User initialization complete"
fi

# Start the main service
echo "🚀 Starting service: ${MODULE_NAME}"
exec python3 -m uvicorn $MODULE_NAME:app --host 0.0.0.0 --port 8080
