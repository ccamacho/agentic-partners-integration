#!/bin/bash

# Build script for agent-service and request-manager containers
# Uses docker for local development

set -e

# Configuration
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

IMAGE_TAG="${IMAGE_TAG:-latest}"
USE_PIP_INSTALL="${USE_PIP_INSTALL:-false}"

echo "Building containers with docker..."
echo "  Project root: $PROJECT_ROOT"
echo "  Image tag: $IMAGE_TAG"
echo "  Use pip install: $USE_PIP_INSTALL"
echo ""

# Regenerate lock files to ensure they match pyproject.toml
if command -v uv &> /dev/null && [ "$USE_PIP_INSTALL" != "true" ]; then
    echo "Regenerating uv.lock files..."
    (cd "$PROJECT_ROOT/agent-service" && uv lock --quiet)
    (cd "$PROJECT_ROOT/request-manager" && uv lock --quiet)
    echo "  Lock files up to date"
    echo ""
fi

# Build agent-service
echo "Building agent-service..."
docker build \
    -t partner-agent-service:${IMAGE_TAG} \
    -f agent-service/Containerfile \
    --build-arg SERVICE_NAME=agent-service \
    --build-arg MODULE_NAME=agent_service.main \
    --build-arg USE_PIP_INSTALL=${USE_PIP_INSTALL} \
    .

echo ""
echo "Building request-manager..."
docker build \
    -t partner-request-manager:${IMAGE_TAG} \
    -f request-manager/Containerfile \
    --build-arg SERVICE_NAME=request-manager \
    --build-arg MODULE_NAME=request_manager.main \
    --build-arg USE_PIP_INSTALL=${USE_PIP_INSTALL} \
    .

echo ""
echo "Building Web UI..."
docker build \
    -t partner-web-ui:${IMAGE_TAG} \
    -f pf-chat-ui/Containerfile \
    pf-chat-ui

echo ""
echo "Building RAG service..."
bash "$PROJECT_ROOT/rag-service/build.sh"

echo ""
echo "Build complete!"
echo ""
echo "Images created:"
docker images | grep "partner-"
echo ""
echo "To run services, use:"
echo "  bash scripts/setup.sh"
echo ""
