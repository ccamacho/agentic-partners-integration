# Helm Chart for Partner Agent Integration Framework

Deploys the full Partner Agent system to Kubernetes/OpenShift.

## Services

| Service | Image | Port |
|---------|-------|------|
| PostgreSQL (pgvector) | `pgvector/pgvector:pg16` | 5432 |
| ChromaDB | `chromadb/chroma:latest` | 8000 |
| RAG API | `ghcr.io/ccamacho/agentic-partners-integration/rag-api` | 8080 |
| Agent Service | `ghcr.io/ccamacho/agentic-partners-integration/agent-service` | 8080 |
| Request Manager | `ghcr.io/ccamacho/agentic-partners-integration/request-manager` | 8080 |
| Web UI | `ghcr.io/ccamacho/agentic-partners-integration/web-ui` | 80 |

## Prerequisites

- Kubernetes 1.24+ or OpenShift 4.12+
- Helm 3.8+
- LLM API key (Google Gemini recommended, or OpenAI/Ollama)

## Quick Start

### 1. Create namespace

```bash
kubectl create namespace partner-agent
```

### 2. Install the chart

```bash
helm install partner-agent ./helm \
  --namespace partner-agent \
  --set llm.googleApiKey='your-api-key-here'
```

### 3. Verify

```bash
kubectl get pods -n partner-agent
```

### 4. Access the UI

```bash
kubectl port-forward -n partner-agent svc/partner-agent-web-ui 3000:3000
# Open http://localhost:3000
```

## Configuration

### LLM Backend

**Gemini (default):**

```bash
helm install partner-agent ./helm \
  --namespace partner-agent \
  --set llm.backend=gemini \
  --set llm.googleApiKey='your-key' \
  --set llm.geminiModel='gemini-2.5-flash'
```

**OpenAI:**

```bash
helm install partner-agent ./helm \
  --namespace partner-agent \
  --set llm.backend=openai \
  --set llm.openaiApiKey='sk-your-key' \
  --set llm.openaiModel='gpt-4'
```

**Ollama:**

```bash
helm install partner-agent ./helm \
  --namespace partner-agent \
  --set llm.backend=ollama \
  --set llm.ollamaBaseUrl='http://ollama:11434' \
  --set llm.ollamaModel='llama3.1'
```

### Key Values

```yaml
image:
  registry: ghcr.io/ccamacho/agentic-partners-integration
  tag: "latest"

requestManager:
  replicas: 1
  uvicornWorkers: 4

agentService:
  replicas: 1
  uvicornWorkers: 4

ragApi:
  replicas: 1

webUi:
  replicas: 1

postgresql:
  image: pgvector/pgvector:pg16
  storage: 5Gi

chromadb:
  image: chromadb/chroma:latest
  storage: 5Gi

networkPolicies:
  enabled: true
  platform: "openshift"  # or "kind" or "none"
```

### Custom Values File

```bash
helm install partner-agent ./helm \
  --namespace partner-agent \
  -f custom-values.yaml
```

## Scaling

```bash
# Manual scaling
kubectl scale deployment partner-agent-request-manager -n partner-agent --replicas=3
kubectl scale deployment partner-agent-agent-service -n partner-agent --replicas=3

# Or enable HPA via values
helm upgrade partner-agent ./helm \
  --namespace partner-agent \
  --set requestManager.autoscaling.enabled=true
```

## Upgrading

```bash
helm upgrade partner-agent ./helm \
  --namespace partner-agent \
  --set image.tag=v1.2.3
```

## Uninstalling

```bash
helm uninstall partner-agent --namespace partner-agent
kubectl delete namespace partner-agent
```

## Related Documentation

- **Main README**: `../README.md` -- Project overview, architecture, and quick start
