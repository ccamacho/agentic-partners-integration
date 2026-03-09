# Scripts - Essential Setup & Testing

## Main Scripts

### `setup.sh` - Complete Setup
**One command to setup everything**

```bash
export GOOGLE_API_KEY="your-key"
bash scripts/setup.sh
```

**What it does:**
- Builds all container images
- Starts PostgreSQL, ChromaDB
- Starts agent-service, request-manager, rag-api, pf-chat-ui
- **Auto-initializes all data** (users, passwords, RAG knowledge)

---

### `test.sh` - Complete Testing
**One command to test everything**

```bash
bash scripts/test.sh
```

**What it tests:**
- Health checks (all services)
- RAG queries
- Authentication
- Agent routing
- E2E flow

---

## Supporting Scripts

### `build_containers.sh`
Builds all container images (used by setup.sh)

### `setup_aaa_users.py`
Creates test users with passwords (used by setup.sh)

### `setup_production_users.py`
Creates production users with specific permissions (Carlos, Luis, Sharon, Josh)

### `entrypoint_with_init.sh`
Production entrypoint for K8s deployments (auto-initializes data)

---

## Quick Reference

```bash
# Complete setup + initialization
bash scripts/setup.sh

# Test everything
bash scripts/test.sh

# Just build containers
bash scripts/build_containers.sh
```
