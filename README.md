# Partner Agent Integration Framework

**AI-powered support routing system built on four pillars: AAA (Authentication, Authorization, Accounting), RAG-backed specialist agents, A2A (Agent-to-Agent) communication, and a PatternFly chat UI.**

Users sign in, describe their issue, and the system routes them to the right specialist agent (software support or network support) based on their permissions. Agents query a knowledge base of historical support tickets via RAG to provide grounded, context-aware responses. Every request is logged with full accounting.

## Quick Start

### Prerequisites

- Docker
- Google API Key (for Gemini LLM and embeddings)

### One-Command Setup

```bash
export GOOGLE_API_KEY=your-key-here
make setup
```

This builds containers, starts all services, runs migrations, creates users, ingests RAG knowledge, and launches the web UI. When it finishes:

- **Web UI:** http://localhost:3000
- **API:** http://localhost:8000
- **Agent Service:** http://localhost:8001
- **RAG API:** http://localhost:8003

### Test Users

| User | Password | Access |
|------|----------|--------|
| carlos@example.com | carlos123 | Software support only |
| luis@example.com | luis123 | Network support only |
| sharon@example.com | sharon123 | All agents (admin) |
| josh@example.com | josh123 | No agents (restricted) |

### Try It

1. Open http://localhost:3000
2. Sign in as `carlos@example.com` / `carlos123`
3. Type: "My app crashes with error 500" -- Routes to software-support agent with RAG context
4. Type: "VPN not connecting" -- Denied (Carlos lacks network-support access)
5. Sign in as `sharon@example.com` -- Both queries work (admin access)

### Run Tests

```bash
make test   # E2E tests covering all four pillars
```

---

## The Four Pillars

### 1. AAA -- Authentication, Authorization, and Accounting

The system implements a complete AAA framework. Every user is authenticated, every agent access is authorized, and every request is fully accounted for.

#### Authentication

Users authenticate via email + password. The request-manager issues JWT tokens with configurable expiry.

```
User → POST /auth/login {email, password}
     ← {token: "eyJ...", expires_in: 300}
     → POST /adk/chat + Authorization: Bearer eyJ...
```

1. User submits credentials on the PatternFly login page
2. Request Manager validates against bcrypt-hashed passwords in PostgreSQL
3. Returns a signed JWT token (default: 5-minute expiry)
4. Frontend stores the token and sends it with every request
5. All `/adk/*` and `/auth/*` endpoints validate the JWT and resolve the user record from the database

Auth endpoints (`auth_endpoints.py`, prefix `/auth`):

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/auth/login` | POST | Authenticate with email + password, receive JWT token |
| `/auth/me` | GET | Return current user profile and permissions (requires JWT) |
| `/auth/refresh` | POST | Refresh an expiring JWT token (requires valid JWT) |

Rate limiting: Login attempts are limited per IP address via an in-memory sliding-window rate limiter.

#### Authorization

Each user has an `allowed_agents` list that controls which specialist agents they can reach. Enforcement happens at three layers:

| Layer | Where | How |
|-------|-------|-----|
| LLM prompt | Agent Service | Routing-agent's system prompt lists only the user's permitted agents; LLM won't route to others |
| Hard gate | Request Manager | `communication_strategy.py` checks `allowed_agents` before every specialist A2A call; blocks unauthorized routing regardless of LLM output |
| UI filtering | Chat UI | The UI fetches user permissions via `GET /auth/me` and displays only accessible agents |

User permissions in the database:

| User | `allowed_agents` | Effect |
|------|-------------------|--------|
| carlos@example.com | `["software-support"]` | Software support only |
| luis@example.com | `["network-support"]` | Network support only |
| sharon@example.com | `["*"]` | Admin -- all agents |
| josh@example.com | `[]` | No agents -- all routing denied |

#### Accounting

Every request is logged in the `request_logs` table with a complete audit trail:

```
request_logs table:
  request_id         — unique request identifier
  session_id         — conversation session (FK to request_sessions)
  request_type       — "message"
  request_content    — the user's message text
  agent_id           — which agent handled the request (e.g., "software-support")
  response_content   — the agent's full response text
  response_metadata  — routing decisions, metadata from the agent
  processing_time_ms — end-to-end processing time in milliseconds
  completed_at       — when the response was received
  pod_name           — which pod/container handled the request
  created_at         — when the request was received
```

The accounting write-back happens in `communication_strategy.py` after each A2A call completes. The `_complete_request_log()` method updates the `RequestLog` row with the response data, agent identity, and timing. Example from a live test run:

```
agent_id         | processing_time_ms | completed_at
-----------------+--------------------+-------------------------------
software-support |              11050 | 2026-03-06 12:14:58.515425+00
network-support  |              17569 | 2026-03-06 12:19:22.847123+00
routing-agent    |               1218 | 2026-03-06 12:18:42.339221+00
```

Session-level accounting is stored in `request_sessions.conversation_context`, which records every message and response in the conversation as a JSON array.

The audit trail is queryable via the **Audit page** (`audit.html`), which calls `GET /adk/audit` and displays a table of all request logs.

---

### 2. RAG -- Retrieval-Augmented Generation

Specialist agents don't hallucinate answers -- they query a knowledge base of historical support tickets via RAG and ground their responses in real data.

#### Data flow

```
data/software_support_tickets.json  ─┐
data/network_support_tickets.json   ─┤
                                     │  ingest_knowledge.py
                                     │  (embeds via Gemini, stores in ChromaDB)
                                     ▼
                              ChromaDB collections
                              ├── software_support
                              └── network_support
                                     │
                                     │  /answer endpoint
                                     │  (query embedding → similarity search → LLM summary)
                                     ▼
                              RAG API response:
                              {response, sources: [{id, content, similarity}]}
```

#### How it works

1. **Ingestion** (`ingest_knowledge.py`): Reads JSON support tickets, embeds each ticket using the Gemini embeddings model (`models/gemini-embedding-001`), and stores vectors in ChromaDB collections.

2. **Query** (`rag_service.py`): When a specialist agent receives a user message, the agent-service calls `POST /answer` on the RAG API. The RAG API embeds the query, performs similarity search against ChromaDB, and returns the top matching tickets with similarity scores.

3. **Grounding** (`main.py`): The agent-service builds the LLM prompt by combining the agent's system message, conversation history, and the RAG results as context. The LLM generates a response that references specific ticket IDs and known solutions.

#### Components

| Component | Role | Port |
|-----------|------|------|
| ChromaDB | Vector database storing embedded support tickets | 8002 |
| RAG API | FastAPI service that embeds queries and searches ChromaDB | 8003 |
| Gemini Embeddings | `models/gemini-embedding-001` for vector generation | -- |

#### Synthetic data

The system ships with synthetic support tickets in `data/`:
- **software_support_tickets.json** -- Application crashes, error codes, performance issues
- **network_support_tickets.json** -- VPN, DNS, firewall, connectivity problems

Each ticket has an ID, description, resolution, and category.

---

### 3. A2A -- Agent-to-Agent Communication

All inter-agent communication uses exclusively HTTP-based A2A (Agent-to-Agent) calls. There is no message broker, no event bus, no shared memory -- agents talk directly over HTTP.

#### Communication pattern

```
Request Manager                          Agent Service
(orchestrator)                           (agents)
      │                                       │
      │  POST /api/v1/agents/routing-agent/invoke
      │  {session_id, user_id, message,       │
      │   transfer_context: {                 │
      │     allowed_agents: ["software-support"],
      │     conversation_history: [...]       │
      │   }}                                  │
      │──────────────────────────────────────▶│
      │                                       │  routing-agent classifies intent
      │  {content: "...",                     │  via LLM
      │   routing_decision: "software-support"}│
      │◀──────────────────────────────────────│
      │                                       │
      │  POST /api/v1/agents/software-support/invoke
      │  {session_id, user_id, message,       │
      │   transfer_context: {                 │
      │     allowed_agents: ["software-support"],
      │     conversation_history: [...],      │
      │     previous_agent: "routing-agent"   │
      │   }}                                  │
      │──────────────────────────────────────▶│
      │                                       │  specialist queries RAG,
      │  {content: "Based on similar cases..."}│  generates grounded response
      │◀──────────────────────────────────────│
```

#### How it works

1. **`COMMUNICATION_MODE=http`** (also accepts `a2a`) selects the `DirectHTTPStrategy` in `communication_strategy.py`.
2. **`EnhancedAgentClient`** (`agent_client_enhanced.py`) sends `POST /api/v1/agents/{agent_name}/invoke` to the agent-service.
3. **`transfer_context`** carries the user's `allowed_agents`, `conversation_history`, and `previous_agent` across each A2A call, so the receiving agent has full context.
4. **Two-hop routing:** The request-manager first invokes the routing-agent. If the response contains a `routing_decision`, the request-manager makes a second A2A call to the specialist agent. The user never calls specialist agents directly.
5. **Permission enforcement at every hop:** The request-manager checks `allowed_agents` before each specialist invocation. The routing-agent's prompt also restricts which agents it can route to based on `allowed_agents`.
6. **Accounting at every hop:** After each A2A call completes, `_complete_request_log()` records the responding agent, full response, and processing time in `request_logs`.

#### A2A endpoint contract

```
POST /api/v1/agents/{agent_name}/invoke

Request:
  session_id: str       — conversation session
  user_id: str          — user email
  message: str          — user message text
  transfer_context: {   — optional context
    allowed_agents: []  — user's permitted agents
    conversation_history: []  — prior messages
    previous_agent: str — which agent handled the last turn
  }

Response:
  content: str          — agent's text response
  routing_decision: str — (routing-agent only) which specialist to delegate to
  agent_name: str       — which agent produced the response
```

#### Why A2A instead of an event bus

- **Simplicity:** No broker infrastructure (Kafka, Knative) to deploy and manage.
- **Synchronous responses:** The user waits for a response -- async eventing adds complexity without benefit for a request/response interaction.
- **Observability:** Each A2A call is a simple HTTP request with full accounting. No message delivery guarantees to debug.
- **Horizontal scaling:** Agents are stateless HTTP services. Scale by adding replicas behind a load balancer.

---

### 4. PatternFly Web UI

The system uses a custom PatternFly-based chat UI instead of Google's ADK web UI. The ADK web UI does not support JWT authentication, token refresh, or role-based agent filtering -- all of which are required for the AAA framework.

#### Pages

| Page | File | Purpose |
|------|------|---------|
| Login | `login.html` | Email + password form with quick-login buttons for test users. Calls `POST /auth/login`, stores JWT in localStorage. |
| Chat | `chat.html` | PatternFly 6 chat interface. Sends `POST /adk/chat` with JWT. Displays agent responses with markdown. Handles token expiry with redirect to login. |
| Audit | `audit.html` | Request audit log. Calls `GET /adk/audit` and displays all request logs in a table with agent, timing, and response data. |
| Index | `index.html` | Landing page that redirects to login or chat based on auth state. |

#### Architecture

```
Browser                    nginx (port 3000)              Request Manager (port 8000)
   │                            │                                │
   │  GET /login.html           │                                │
   │───────────────────────────▶│ serves static files            │
   │◀───────────────────────────│                                │
   │                            │                                │
   │  POST /auth/login          │  proxy_pass /auth/ → :8080     │
   │───────────────────────────▶│───────────────────────────────▶│
   │◀───────────────────────────│◀───────────────────────────────│
   │  {token: "eyJ..."}        │                                │
   │                            │                                │
   │  POST /adk/chat            │  proxy_pass /adk/ → :8080      │
   │  + Bearer token            │                                │
   │───────────────────────────▶│───────────────────────────────▶│
   │◀───────────────────────────│◀───────────────────────────────│
   │  {response, agent, ...}   │                                │
```

The nginx container serves the static HTML/JS files and reverse-proxies `/adk/`, `/auth/`, and `/api/` requests to the request-manager. No build step, no Node.js runtime -- just static files served by nginx.

---

## Architecture Overview

### System Diagram

```
┌─────────────────────────────────────────────────────────────────────┐
│                         Web UI (port 3000)                          │
│                     PatternFly chat interface                       │
│                    nginx reverse proxy → :8080                      │
└────────────────────────────┬────────────────────────────────────────┘
                             │
                             │ POST /adk/chat
                             │ POST /auth/login
                             │ GET  /auth/me
                             ▼
┌─────────────────────────────────────────────────────────────────────┐
│                    Request Manager (port 8000)                      │
│                                                                     │
│  ┌──────────────┐  ┌──────────────────┐  ┌───────────────────────┐ │
│  │ Auth (JWT)   │  │ adk_endpoints.py │  │ communication_        │ │
│  │              │  │                  │  │ strategy.py           │ │
│  │ • /auth/login│  │ • /adk/chat      │  │                       │ │
│  │ • /auth/me   │  │ • /adk/audit     │  │ • invoke routing      │ │
│  │ • /auth/     │  │                  │  │ • detect ROUTE:       │ │
│  │   refresh    │  │                  │  │ • invoke specialist   │ │
│  └──────────────┘  └──────────────────┘  │ • check permissions   │ │
│                                           │ • write accounting    │ │
│                                           └───────────┬───────────┘ │
│                                                       │             │
└───────────────────────────────────────────────────────┼─────────────┘
                                                        │
                          POST /api/v1/agents/{name}/invoke  (A2A)
                                                        │
┌───────────────────────────────────────────────────────┼─────────────┐
│                    Agent Service (port 8001)           │             │
│                                                       ▼             │
│  ┌─────────────────────────────────────────────────────────────────┐│
│  │                    /invoke endpoint (main.py)                   ││
│  │                                                                 ││
│  │  if agent_name == "routing-agent":                              ││
│  │    • Build system prompt with user's allowed_agents             ││
│  │    • Include conversation history                               ││
│  │    • LLM classifies intent → ROUTE:<agent> or conversation     ││
│  │                                                                 ││
│  │  else (specialist agent):                                       ││
│  │    • Query RAG API with user's message                          ││
│  │    • Build prompt: system_message + history + RAG context       ││
│  │    • LLM generates grounded response                           ││
│  └────────────────────────────┬────────────────────────────────────┘│
│                               │                                     │
│  ┌────────────────────────────▼────────────────────────────────────┐│
│  │              LLM Client Factory                                 ││
│  │  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐          ││
│  │  │ GeminiClient │  │ OpenAIClient │  │ OllamaClient │          ││
│  │  │ (default)    │  │              │  │ (local)      │          ││
│  │  └──────────────┘  └──────────────┘  └──────────────┘          ││
│  └─────────────────────────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────────────────────┘
         │                                           │
         │ LLM API calls                             │ POST /answer
         ▼                                           ▼
┌──────────────────┐                    ┌──────────────────────────┐
│  Google Gemini   │                    │   RAG API (port 8003)    │
│  API             │                    │                          │
│  gemini-2.5-flash│                    │  • Embed query           │
└──────────────────┘                    │  • Search ChromaDB       │
                                        │  • Return top matches    │
                                        └────────────┬─────────────┘
                                                     │
                                        ┌────────────▼─────────────┐
                                        │   ChromaDB (port 8002)   │
                                        │                          │
                                        │  Vector database with    │
                                        │  embedded support tickets│
                                        └──────────────────────────┘

┌─────────────────────────────────────────────────────────────────────┐
│                    PostgreSQL + pgvector (port 5433)                 │
│                                                                     │
│  ┌──────────────┐ ┌──────────────┐ ┌──────────────┐ ┌────────────┐│
│  │    users     │ │  request_    │ │  request_    │ │  alembic_  ││
│  │              │ │  sessions    │ │  logs        │ │  version   ││
│  │ • email      │ │              │ │              │ │            ││
│  │ • password   │ │ • session_id │ │ • request_id │ │ • 006      ││
│  │ • role       │ │ • user_id    │ │ • agent_id   │ │            ││
│  │ • allowed_   │ │ • conversa-  │ │ • response   │ └────────────┘│
│  │   agents[]   │ │   tion_      │ │ • timing_ms  │              │
│  │ • status     │ │   context{}  │ │ • completed  │              │
│  └──────────────┘ └──────────────┘ └──────────────┘              │
└─────────────────────────────────────────────────────────────────────┘
```

### Services

| Service | Port | Role |
|---------|------|------|
| PostgreSQL (pgvector) | 5433 | User data, sessions, accounting logs |
| ChromaDB | 8002 | Vector database for RAG embeddings |
| RAG API | 8003 | Semantic search over support tickets |
| Agent Service | 8001 | LLM-based routing and specialist agents |
| Request Manager | 8000 | AAA enforcement, A2A orchestration, chat API |
| Web UI (nginx) | 3000 | PatternFly chat interface |

> **Note:** Ports above are for `make setup` (uses `scripts/setup.sh`). The `docker-compose.yaml` uses different host port mappings: PostgreSQL on 5432, ChromaDB on 8100, RAG API on 8080. Internal container ports remain the same.

### Request Flow

1. **User sends message** -- Web UI sends `POST /adk/chat` with JWT token and message text.
2. **Authentication** -- Request Manager validates JWT, resolves user from PostgreSQL, loads `allowed_agents`.
3. **A2A call: routing-agent** -- Request Manager invokes `POST /api/v1/agents/routing-agent/invoke` via A2A, passing `transfer_context` with `allowed_agents` and `conversation_history`.
4. **Routing decision** -- Routing-agent's LLM classifies intent. Returns `ROUTE:software-support` or a conversational response.
5. **Authorization check** -- If routing to a specialist, Request Manager checks `allowed_agents`. Blocked if unauthorized.
6. **A2A call: specialist agent** -- Request Manager invokes the specialist via A2A. Specialist queries RAG API, gets matching tickets, builds LLM prompt with RAG context, returns grounded response.
7. **Accounting** -- `_complete_request_log()` updates `request_logs` with `agent_id`, `response_content`, `processing_time_ms`, `completed_at`.
8. **Response** -- Request Manager stores conversation turn in `request_sessions.conversation_context`, returns response to the UI.

### Key Design Decisions

- **Single-turn routing:** The routing-agent classifies intent in one LLM call (no multi-turn state machine). Returns `ROUTE:<agent>` or a conversational response.
- **Mandatory RAG:** Specialist agents always query the RAG API. If RAG is unavailable, the request fails (no silent degradation).
- **Three-layer authorization:** LLM prompt + hard gate + UI filtering. The LLM can't bypass the hard gate.
- **Full accounting:** Every A2A call records which agent handled the request, the response, and processing time.
- **A2A exclusively:** No message brokers. Agents communicate via synchronous HTTP calls.
- **Pluggable LLM:** Backend configured via `LLM_BACKEND` env var. Supports Gemini (default in setup), OpenAI, and Ollama.

---

## Conversation Context

Each chat session maintains conversation history in `request_sessions.conversation_context.messages`:

```json
{
  "messages": [
    {"role": "user", "content": "My app crashes with error 500"},
    {"role": "assistant", "content": "...", "agent": "software-support"},
    {"role": "user", "content": "It happens when I click submit"},
    {"role": "assistant", "content": "...", "agent": "software-support"}
  ]
}
```

- **Sent to routing-agent:** Last 20 messages (for intent classification with context)
- **Sent to specialist agents:** Last 10 messages (for follow-up handling)
- **Max stored:** 40 messages (oldest trimmed)

---

## Agent Configuration

Agents are defined in `agent-service/config/agents/*.yaml` and loaded by `ResponsesAgentManager` at startup.

| Field | Purpose |
|-------|---------|
| `name` | Agent registration key. Must match the name used in `/invoke` URL. |
| `llm_backend` | Which LLM provider to use (gemini, openai, ollama). |
| `llm_model` | Model name passed to the provider. |
| `system_message` | System prompt prepended to every LLM call. |
| `sampling_params.strategy.type` | Sampling strategy (e.g., `top_p`). |
| `sampling_params.strategy.temperature` | Temperature for LLM calls. |
| `sampling_params.strategy.top_p` | Top-p (nucleus) sampling parameter. |

Example (`software-support-agent.yaml`):

```yaml
name: "software-support"
llm_backend: "gemini"
llm_model: "gemini-2.5-flash"
system_message: |
  You are a software support specialist...
sampling_params:
  strategy:
    type: "top_p"
    temperature: 0.7
    top_p: 0.95
```

Available agents:

| File | Agent Name | Role |
|------|-----------|------|
| `routing-agent.yaml` | `routing-agent` | Classifies user intent and routes to the correct specialist |
| `software-support-agent.yaml` | `software-support` | Resolves software issues using RAG-backed knowledge base |
| `network-support-agent.yaml` | `network-support` | Resolves network issues using RAG-backed knowledge base |

---

## Project Structure

```
├── agent-service/              # AI agent processing service
│   ├── config/agents/          # Agent YAML configs (routing, software, network)
│   └── src/agent_service/
│       ├── main.py             # FastAPI app, /invoke endpoint, routing + RAG logic
│       ├── langgraph/          # Agent manager, LLM integration, token counting
│       ├── llm/                # Pluggable LLM clients (Gemini, OpenAI, Ollama)
│       └── schemas.py          # Request/response models for /invoke
│
├── request-manager/            # AAA enforcement, A2A orchestration
│   └── src/request_manager/
│       ├── main.py             # FastAPI app, middleware, session cleanup
│       ├── auth_endpoints.py   # /auth/login, /auth/me, /auth/refresh (JWT auth)
│       ├── adk_endpoints.py    # /adk/chat, /adk/audit (chat + audit API)
│       ├── communication_strategy.py  # A2A invocation, routing loop, accounting
│       ├── agent_client_enhanced.py   # HTTP client for A2A calls
│       └── credential_service.py      # Request-scoped credential management
│
├── rag-service/                # RAG API (ChromaDB + Gemini embeddings)
│   ├── rag_service.py          # FastAPI service for /answer endpoint
│   └── ingest_knowledge.py     # Data ingestion script
│
├── pf-chat-ui/                 # PatternFly chat web UI
│   ├── static/
│   │   ├── index.html          # Landing page (redirects to login or chat)
│   │   ├── login.html          # Login page with JWT authentication
│   │   ├── chat.html           # Chat interface with PF6 components
│   │   └── audit.html          # Request audit log viewer
│   └── nginx.conf              # Reverse proxy to request-manager
│
├── shared-models/              # Shared library: DB models, migrations, auth, health, events
├── data/                       # Synthetic support tickets (JSON)
├── scripts/                    # Setup, build, test, and user management scripts
├── helm/                       # Helm chart for Kubernetes/OpenShift deployment
├── Makefile                    # Build, test, lint, and deploy targets
└── docker-compose.yaml         # Full stack compose file (alternative to make setup)
```

---

## Container Images

| Image | Containerfile | Base Image | Contents |
|-------|--------------|------------|----------|
| `partner-agent-service:latest` | `agent-service/Containerfile` | UBI9 Python 3.12 | Agent service + shared-models |
| `partner-request-manager:latest` | `request-manager/Containerfile` | UBI9 Python 3.12 | Request manager + shared-models |
| `partner-rag-api:latest` | `rag-service/Containerfile` | Python 3.11 slim | RAG API (ChromaDB client + Gemini embeddings) |
| `partner-web-ui:latest` | `pf-chat-ui/Containerfile` | nginx Alpine | Static PatternFly UI + nginx reverse proxy |

Agent service and request manager use a multi-stage build: `registry.access.redhat.com/ubi9/python-312` (builder) / `ubi9/python-312-minimal` (runtime).

---

## Database

PostgreSQL 16 with pgvector extension. Schema managed by Alembic (current version: 006).

### Core tables

| Table | Purpose |
|-------|---------|
| `users` | Authentication credentials, roles, `allowed_agents` (authorization) |
| `request_sessions` | Session state, `conversation_context` (JSON message history) |
| `request_logs` | Full accounting: request content, response content, agent_id, processing time, timestamps |
| `alembic_version` | Migration tracking |

### Additional tables

| Table | Purpose |
|-------|---------|
| `user_integration_configs` | Per-user integration configuration (WEB type) |
| `user_integration_mappings` | Maps users to external integration identifiers |
| `processed_events` | Tracks processed CloudEvents for idempotency |

LangGraph checkpoint tables (`checkpoints`, `checkpoint_blobs`, `checkpoint_writes`, `checkpoint_migrations`) are created during setup for agent conversation state.

---

## Configuration

### Environment Variables

#### LLM

| Variable | Default | Description |
|----------|---------|-------------|
| `LLM_BACKEND` | `openai` | LLM provider: `gemini`, `openai`, or `ollama`. Setup scripts set `gemini`. |
| `GOOGLE_API_KEY` | -- | Required when using Gemini backend |
| `GEMINI_MODEL` | `gemini-2.5-flash` | Model name for Gemini |
| `OPENAI_API_KEY` | -- | Required when using OpenAI backend |
| `OPENAI_MODEL` | -- | Model name for OpenAI (e.g., `gpt-4`) |
| `OLLAMA_BASE_URL` | -- | Ollama server URL (e.g., `http://localhost:11434`) |
| `OLLAMA_MODEL` | -- | Model name for Ollama |

#### Database

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_URL` | -- | PostgreSQL connection string (`postgresql+asyncpg://...`) |

#### A2A Communication

| Variable | Default | Description |
|----------|---------|-------------|
| `COMMUNICATION_MODE` | `http` | Communication strategy: `http`, `a2a`, or `direct` (all select `DirectHTTPStrategy`) |
| `AGENT_SERVICE_URL` | `http://agent-service:8080` | Agent service base URL |
| `RAG_API_ENDPOINT` | `http://rag-api:8080/answer` | RAG API answer endpoint URL |
| `AGENT_TIMEOUT` | `120` | Timeout in seconds for A2A calls |
| `STRUCTURED_CONTEXT_ENABLED` | `true` | Send structured `transfer_context` in A2A calls |

#### Authentication

| Variable | Default | Description |
|----------|---------|-------------|
| `JWT_EXPIRATION_MINUTES` | `5` | JWT token lifetime in minutes |

#### RAG Service

| Variable | Default | Description |
|----------|---------|-------------|
| `CHROMA_HOST` | `chromadb` | ChromaDB hostname |
| `CHROMA_PORT` | `8000` | ChromaDB port (internal) |
| `EMBEDDING_MODEL` | `models/gemini-embedding-001` | Embedding model for vector generation |
| `LLM_MODEL` | -- | LLM model used by RAG service for answer generation |

#### Operations

| Variable | Default | Description |
|----------|---------|-------------|
| `LOG_LEVEL` | `INFO` | Logging level for services |
| `SESSION_CLEANUP_INTERVAL_HOURS` | `24` | How often to run session cleanup |
| `INACTIVE_SESSION_RETENTION_DAYS` | `30` | Days to retain inactive sessions before cleanup |

### LLM Backends

| Backend | Env Vars | Notes |
|---------|----------|-------|
| Gemini | `GOOGLE_API_KEY`, `GEMINI_MODEL` | Used by default in setup. Uses Google AI API. |
| OpenAI | `OPENAI_API_KEY`, `OPENAI_MODEL` | GPT-4, GPT-3.5, etc. |
| Ollama | `OLLAMA_BASE_URL`, `OLLAMA_MODEL` | Local LLMs. No API key needed. |

---

## API Endpoints

### Authentication (`/auth`)

```bash
# Login and get JWT token
TOKEN=$(curl -s -X POST http://localhost:8000/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"carlos@example.com","password":"carlos123"}' | jq -r '.token')

# Check current user info and permissions
curl http://localhost:8000/auth/me \
  -H "Authorization: Bearer $TOKEN"

# Refresh an expiring token
curl -X POST http://localhost:8000/auth/refresh \
  -H "Authorization: Bearer $TOKEN"
```

### Chat (`/adk`)

```bash
# Send a message (requires JWT)
curl -X POST http://localhost:8000/adk/chat \
  -H 'Content-Type: application/json' \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"message": "My app crashes with error 500", "user": {"email": "carlos@example.com"}}'

# View audit log
curl http://localhost:8000/adk/audit \
  -H "Authorization: Bearer $TOKEN"
```

### A2A Agent Invocation (Internal)

```bash
# Direct agent invoke (used by request-manager internally via A2A)
curl -X POST http://localhost:8001/api/v1/agents/routing-agent/invoke \
  -H 'Content-Type: application/json' \
  -d '{"session_id": "s1", "user_id": "u1", "message": "Hello"}'
```

---

## Development

### Makefile Targets

The project uses a `Makefile` for common operations:

```bash
make help                  # Show all available targets

# Setup & Deploy
make setup                 # Build containers, start services, initialize data
make build                 # Build all container images
make stop                  # Stop all running containers
make clean                 # Stop and remove all containers, volumes, and network

# Testing
make test                  # Run end-to-end tests against running services
make test-unit             # Run unit tests for all packages
make test-shared-models    # Run shared-models unit tests
make test-request-manager  # Run request-manager unit tests
make test-agent-service    # Run agent-service unit tests

# Code Quality
make format                # Run isort and Black formatting
make lint                  # Run flake8, isort check, and mypy
make lint-shared-models    # Run mypy on shared-models
make lint-agent-service    # Run mypy on agent-service
make lint-request-manager  # Run mypy on request-manager

# Lockfile Management
make check-lockfiles       # Check if all uv.lock files are up-to-date
make update-lockfiles      # Update all uv.lock files

# Dependencies
make install               # Install all package dependencies locally (via uv)
make reinstall             # Force reinstall all dependencies

# Logs
make logs-request-manager  # Tail request-manager container logs
make logs-agent-service    # Tail agent-service container logs
make logs-rag-api          # Tail RAG API container logs
```

### Build Containers

```bash
# Build all service images (including web UI)
bash scripts/build_containers.sh

# Or individually
docker build -t partner-agent-service:latest -f agent-service/Containerfile .
docker build -t partner-request-manager:latest -f request-manager/Containerfile .
docker build -t partner-rag-api:latest -f rag-service/Containerfile .
docker build -t partner-web-ui:latest -f pf-chat-ui/Containerfile .
```

### Deployment Options

There are two ways to run the stack locally:

| Method | Command | Ports | Best For |
|--------|---------|-------|----------|
| Setup script | `make setup` | PG:5433, Chroma:8002, RAG:8003 | Production-like setup, first-time users |
| Docker Compose | `docker compose up` | PG:5432, Chroma:8100, RAG:8080 | Development, quick iteration |

Both methods expose the Web UI on port 3000, Request Manager on 8000, and Agent Service on 8001.

### Stop Everything

```bash
make stop                   # Stop via Makefile
# or
make clean                  # Stop and remove everything
# or
docker compose down -v      # If using docker-compose
```

### View Logs

```bash
make logs-request-manager   # Request manager
make logs-agent-service     # Agent service
make logs-rag-api           # RAG API
```

### Kubernetes Deployment

See [helm/README.md](helm/README.md) for Helm chart deployment to Kubernetes/OpenShift.

### Scripts Reference

| Script | Purpose |
|--------|---------|
| `scripts/setup.sh` | Full setup: build images, start containers, run migrations, create users, ingest data |
| `scripts/build_containers.sh` | Build all four container images |
| `scripts/test.sh` | End-to-end tests covering all four pillars |
| `scripts/setup_aaa_users.py` | Create test users with roles and permissions |
| `scripts/setup_production_users.py` | Create production users (used in Kubernetes) |
| `scripts/entrypoint_with_init.sh` | Kubernetes entrypoint: runs migrations + starts service |
