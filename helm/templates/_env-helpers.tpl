{{/*
Database environment variables
*/}}
{{- define "partner-agent.dbEnvVars" }}
- name: POSTGRES_HOST
  valueFrom:
    secretKeyRef:
      name: {{ include "partner-agent.fullname" . }}-db-credentials
      key: host
- name: POSTGRES_PORT
  valueFrom:
    secretKeyRef:
      name: {{ include "partner-agent.fullname" . }}-db-credentials
      key: port
- name: POSTGRES_DB
  valueFrom:
    secretKeyRef:
      name: {{ include "partner-agent.fullname" . }}-db-credentials
      key: dbname
- name: POSTGRES_USER
  valueFrom:
    secretKeyRef:
      name: {{ include "partner-agent.fullname" . }}-db-credentials
      key: user
- name: POSTGRES_PASSWORD
  valueFrom:
    secretKeyRef:
      name: {{ include "partner-agent.fullname" . }}-db-credentials
      key: password
- name: DATABASE_URL
  valueFrom:
    secretKeyRef:
      name: {{ include "partner-agent.fullname" . }}-db-credentials
      key: url
{{- end }}

{{/*
Common environment variables for all services
*/}}
{{- define "partner-agent.commonEnvVars" }}
- name: LOG_LEVEL
  value: {{ .Values.logLevel | default "INFO" | quote }}
- name: EXPECTED_MIGRATION_VERSION
  value: {{ .Values.database.expectedMigrationVersion | default "007" | quote }}
- name: PORT
  value: "8080"
- name: HOST
  value: "0.0.0.0"
{{- end }}

{{/*
LLM environment variables
*/}}
{{- define "partner-agent.llmEnvVars" }}
- name: LLM_BACKEND
  valueFrom:
    secretKeyRef:
      name: {{ include "partner-agent.fullname" . }}-llm-credentials
      key: llm-backend
- name: GOOGLE_API_KEY
  valueFrom:
    secretKeyRef:
      name: {{ include "partner-agent.fullname" . }}-llm-credentials
      key: google-api-key
      optional: true
- name: GEMINI_MODEL
  valueFrom:
    secretKeyRef:
      name: {{ include "partner-agent.fullname" . }}-llm-credentials
      key: gemini-model
      optional: true
- name: OPENAI_API_KEY
  valueFrom:
    secretKeyRef:
      name: {{ include "partner-agent.fullname" . }}-llm-credentials
      key: openai-api-key
      optional: true
- name: OPENAI_MODEL
  valueFrom:
    secretKeyRef:
      name: {{ include "partner-agent.fullname" . }}-llm-credentials
      key: openai-model
      optional: true
- name: OLLAMA_BASE_URL
  valueFrom:
    secretKeyRef:
      name: {{ include "partner-agent.fullname" . }}-llm-credentials
      key: ollama-base-url
      optional: true
- name: OLLAMA_MODEL
  valueFrom:
    secretKeyRef:
      name: {{ include "partner-agent.fullname" . }}-llm-credentials
      key: ollama-model
      optional: true
{{- end }}

{{/*
Request Manager environment variables
*/}}
{{- define "partner-agent.requestManagerEnvVars" }}
{{- include "partner-agent.dbEnvVars" . }}
{{- include "partner-agent.commonEnvVars" . }}
{{- include "partner-agent.llmEnvVars" . }}
- name: COMMUNICATION_MODE
  value: "http"
- name: AGENT_SERVICE_URL
  value: "http://{{ include "partner-agent.fullname" . }}-agent-service:80"
- name: AGENT_TIMEOUT
  value: "120"
- name: AAA_ENABLED
  value: "true"
- name: AAA_AUTO_CREATE_USERS
  value: "true"
- name: JWT_ENABLED
  value: "true"
- name: JWT_VERIFY_SIGNATURE
  value: "false"
{{- end }}

{{/*
Agent Service environment variables
*/}}
{{- define "partner-agent.agentServiceEnvVars" }}
{{- include "partner-agent.dbEnvVars" . }}
{{- include "partner-agent.commonEnvVars" . }}
{{- include "partner-agent.llmEnvVars" . }}
- name: COMMUNICATION_MODE
  value: "http"
- name: RAG_API_ENDPOINT
  value: "http://{{ include "partner-agent.fullname" . }}-rag-api:80/answer"
{{- end }}
