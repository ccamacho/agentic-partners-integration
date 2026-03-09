#!/bin/bash
# Build RAG service container

# Use parent directory as build context to access data/
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

echo "Building RAG service..."
docker build -t partner-rag-api:latest -f "$PROJECT_ROOT/rag-service/Containerfile" "$PROJECT_ROOT"

echo "✅ RAG service built"
