# Google ADK Web UI Integration

This directory contains the integration for Google's Agent Development Kit (ADK) web interface.

## Overview

The ADK web UI provides a user-friendly interface for interacting with partner agents, with built-in support for:

- **Authentication** - JWT-based user authentication
- **Authorization** - Role-based agent access control
- **Conversation History** - Session management and context
- **Multi-Agent Support** - Routing to appropriate specialist agents

## Architecture

```
User → ADK Web UI → Request Manager → AAA Middleware → Routing Agent → Partner Agents
                         ↓
                    PostgreSQL (User Roles, Sessions)
```

## Setup

### 1. Deploy ADK Web UI

The PF Chat UI is deployed as a separate container that communicates with the request manager.

```bash
docker run -d \
  --name partner-pf-chat-ui \
  --network partner-agent-network \
  -p 3000:3000 \
  -e API_ENDPOINT=http://partner-request-manager:8080 \
  -e AUTH_ENABLED=true \
  -e JWT_ENABLED=true \
  partner-pf-chat-ui:latest
```

### 2. Configure Authentication

ADK web UI uses JWT tokens for authentication via the `/auth/login` endpoint.
Users authenticate with email and password, and receive a JWT token that is
stored in `localStorage` and sent as a `Bearer` token on subsequent requests.

### 3. Set Up User Roles

Create users with appropriate roles and agent access:

```python
# Create admin user
await AAAService.get_or_create_user(
    db,
    email="admin@example.com",
    role=UserRole.ADMIN,
    organization="Engineering",
    department="Platform"
)

# Create support staff with limited access
await AAAService.get_or_create_user(
    db,
    email="support@example.com",
    role=UserRole.SUPPORT_STAFF,
    organization="Support",
    department="Technical Support"
)

# Update specific user permissions
await AAAService.update_user_permissions(
    db,
    user_email="engineer@example.com",
    role=UserRole.ENGINEER,
    allowed_agents=["software-support", "network-support"]
)
```

## User Roles and Agent Access

### Role Hierarchy

| Role | Access Level | Default Agents | Description |
|------|--------------|----------------|-------------|
| **admin** | Full | All agents (`*`) | System administrators with unrestricted access |
| **manager** | Full | All agents (`*`) | Team managers with full agent access |
| **engineer** | Restricted | `software-support`, `network-support` | Engineers with technical support access |
| **support_staff** | Restricted | `software-support`, `network-support` | Support staff with both support agents |
| **user** | Restricted | `software-support` | End users with limited access |

### Custom Permissions

You can grant custom agent access to specific users:

```python
# Grant access to specific agents
await AAAService.update_user_permissions(
    db,
    user_email="specialist@example.com",
    allowed_agents=["network-support", "security-support", "database-support"]
)

# Use wildcards for pattern matching
await AAAService.update_user_permissions(
    db,
    user_email="tech-lead@example.com",
    allowed_agents=["*-support"]  # All agents ending with "-support"
)
```

## API Endpoints

### ADK-Compatible Endpoints

The request manager exposes ADK-compatible endpoints:

#### POST /adk/chat
Send a message to the agent system:

```json
{
  "message": "My application is crashing",
  "session_id": "optional-session-id",
  "user": {
    "email": "user@example.com"
  }
}
```

Response:
```json
{
  "response": "I can help with that. What is the product version?",
  "session_id": "abc123",
  "agent": "software-support",
  "user_context": {
    "role": "engineer",
    "allowed_agents": ["software-support", "network-support"]
  }
}
```

## Configuration

### Environment Variables

```bash
# ADK Web UI
ADK_WEB_PORT=3000
ADK_API_ENDPOINT=http://partner-request-manager:8080

# Authentication
AUTH_ENABLED=true
JWT_ENABLED=true
JWT_ISSUERS=["https://accounts.google.com"]
JWT_VERIFY_SIGNATURE=true

# Authorization
AAA_ENABLED=true
AAA_AUTO_CREATE_USERS=true
AAA_DEFAULT_ROLE=user

# Session Management
SESSION_TIMEOUT=3600
SESSION_STORAGE=postgresql
```

### Docker Compose

```yaml
# docker-compose.yaml
services:
  pf-chat-ui:
    image: partner-pf-chat-ui:latest
    container_name: partner-pf-chat-ui
    networks:
      - partner-agent-network
    ports:
      - "3000:3000"
    environment:
      - API_ENDPOINT=http://partner-request-manager:8080
      - AUTH_ENABLED=true
      - JWT_ENABLED=true
    depends_on:
      - request-manager

  # ... other services
```

## Usage

### 1. Access the Web UI

Navigate to `http://localhost:3000` in your browser.

### 2. Sign In

Click "Sign In" and authenticate using your configured identity provider (Google, etc.).

### 3. Start Conversation

The UI will automatically route you to available agents based on your role and permissions.

### 4. Multi-Turn Conversations

The system maintains conversation context across multiple messages:

```
You: My application crashes with error 500
Agent: I can help with that. What is the product version?
You: Version 2.1
Agent: What is the exact error message?
You: HTTP 500 Internal Server Error
Agent: [Queries RAG and provides solution]
```

## Testing

### Manual Testing

```bash
# Start all services including ADK web UI
docker-compose -f docker-compose.yaml up -d

# Create test users
python scripts/setup_aaa_users.py

# Open browser
open http://localhost:3000
```

### API Testing

```bash
# Get JWT token from your identity provider
export JWT_TOKEN="your-jwt-token"

# Test agent access
curl -X POST http://localhost:8000/adk/chat \
  -H "Authorization: Bearer $JWT_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "message": "My app crashes",
    "user": {"email": "test@example.com"}
  }'
```

## Troubleshooting

### Access Denied Errors

If you get 403 Forbidden errors:

1. Check user role: `SELECT role, allowed_agents FROM users WHERE primary_email = 'your-email';`
2. Verify agent exists: `curl http://localhost:8001/agents`
3. Update permissions: Use `AAAService.update_user_permissions()`

### JWT Validation Failures

1. Verify JWT_ISSUERS matches your identity provider
2. Check JWT token expiration
3. Ensure JWT_VERIFY_SIGNATURE is correctly configured

### Agent Not Available

If an agent doesn't appear in the list:

1. Check if agent is running: `curl http://localhost:8001/health`
2. Verify user has access: `await AAAService.get_user_allowed_agents(db, email)`
3. Check agent configuration: `cat agent-service/config/agents/<agent-name>.yaml`

## Security Best Practices

1. **Always enable JWT verification in production**
   ```bash
   JWT_VERIFY_SIGNATURE=true
   JWT_VERIFY_EXPIRATION=true
   ```

2. **Use HTTPS in production**
   ```bash
   ADK_WEB_URL=https://agents.yourcompany.com
   ```

3. **Regularly audit user permissions**
   ```sql
   SELECT primary_email, role, allowed_agents
   FROM users
   WHERE status = 'active';
   ```

4. **Monitor access attempts**
   ```sql
   SELECT * FROM request_logs
   WHERE response_metadata->>'error' = 'access_denied'
   ORDER BY created_at DESC;
   ```

## Next Steps

1. Configure your identity provider (Google, Okta, etc.)
2. Set up initial users and roles
3. Deploy ADK web UI
4. Test agent access with different user roles
5. Monitor usage and access patterns

For more information, see the main [README](../README.md).
