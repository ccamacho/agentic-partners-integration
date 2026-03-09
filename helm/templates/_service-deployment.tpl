{{/*
Generic service deployment template for request-manager and agent-service.
Usage: {{ include "partner-agent.serviceDeployment" (dict "serviceName" "request-manager" "serviceConfig" .Values.requestManager "imageKey" "requestManager" "context" .) }}
*/}}
{{- define "partner-agent.serviceDeployment" -}}
{{- $serviceName := .serviceName -}}
{{- $serviceConfig := .serviceConfig -}}
{{- $imageKey := .imageKey -}}
{{- $context := .context -}}
{{- $fullName := include "partner-agent.fullname" $context -}}
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: {{ $fullName }}-{{ $serviceName }}
  namespace: {{ $context.Release.Namespace }}
  labels:
    {{- include "partner-agent.labels" $context | nindent 4 }}
    app: {{ $fullName }}-{{ $serviceName }}
    component: {{ $serviceName }}
spec:
  replicas: {{ $serviceConfig.replicas | default 1 }}
  selector:
    matchLabels:
      {{- include "partner-agent.selectorLabels" $context | nindent 6 }}
      app: {{ $fullName }}-{{ $serviceName }}
  template:
    metadata:
      labels:
        {{- include "partner-agent.labels" $context | nindent 8 }}
        app: {{ $fullName }}-{{ $serviceName }}
        component: {{ $serviceName }}
    spec:
      serviceAccountName: {{ include "partner-agent.serviceAccountName" $context }}
      securityContext:
        runAsNonRoot: true
        seccompProfile:
          type: RuntimeDefault
      containers:
      - name: {{ $serviceName }}
        image: "{{ $context.Values.image.registry }}/{{ index $context.Values.image $imageKey }}:{{ $context.Values.image.tag | default $context.Chart.AppVersion }}"
        imagePullPolicy: {{ $context.Values.image.pullPolicy }}
        ports:
        - containerPort: 8080
          protocol: TCP
          name: http
        env:
        {{- if eq $serviceName "request-manager" }}
        {{- include "partner-agent.requestManagerEnvVars" $context | nindent 8 }}
        {{- else if eq $serviceName "agent-service" }}
        {{- include "partner-agent.agentServiceEnvVars" $context | nindent 8 }}
        {{- end }}
        {{- if $serviceConfig.uvicornWorkers }}
        - name: UVICORN_WORKERS
          value: {{ $serviceConfig.uvicornWorkers | quote }}
        {{- end }}
        {{- if $serviceConfig.resources }}
        resources:
          {{- toYaml $serviceConfig.resources | nindent 10 }}
        {{- end }}
        securityContext:
          allowPrivilegeEscalation: false
          capabilities:
            drop:
            - ALL
          runAsNonRoot: true
          seccompProfile:
            type: RuntimeDefault
        livenessProbe:
          httpGet:
            path: /health
            port: 8080
          initialDelaySeconds: 30
          periodSeconds: 10
          timeoutSeconds: 5
          failureThreshold: 3
        readinessProbe:
          httpGet:
            path: /health
            port: 8080
          initialDelaySeconds: 10
          periodSeconds: 5
          timeoutSeconds: 3
          failureThreshold: 3
        startupProbe:
          httpGet:
            path: /health
            port: 8080
          initialDelaySeconds: 5
          periodSeconds: 5
          timeoutSeconds: 5
          failureThreshold: 30
      restartPolicy: Always
      terminationGracePeriodSeconds: 30
---
apiVersion: v1
kind: Service
metadata:
  name: {{ $fullName }}-{{ $serviceName }}
  namespace: {{ $context.Release.Namespace }}
  labels:
    {{- include "partner-agent.labels" $context | nindent 4 }}
    app: {{ $fullName }}-{{ $serviceName }}
spec:
  type: ClusterIP
  selector:
    {{- include "partner-agent.selectorLabels" $context | nindent 4 }}
    app: {{ $fullName }}-{{ $serviceName }}
  ports:
  - name: http
    port: 80
    targetPort: 8080
    protocol: TCP
{{- if $serviceConfig.autoscaling.enabled }}
---
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: {{ $fullName }}-{{ $serviceName }}
  namespace: {{ $context.Release.Namespace }}
  labels:
    {{- include "partner-agent.labels" $context | nindent 4 }}
    app: {{ $fullName }}-{{ $serviceName }}
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: {{ $fullName }}-{{ $serviceName }}
  minReplicas: {{ $serviceConfig.autoscaling.minReplicas | default 1 }}
  maxReplicas: {{ $serviceConfig.autoscaling.maxReplicas | default 10 }}
  metrics:
  - type: Resource
    resource:
      name: cpu
      target:
        type: Utilization
        averageUtilization: {{ $serviceConfig.autoscaling.targetCPUUtilization | default 70 }}
  - type: Resource
    resource:
      name: memory
      target:
        type: Utilization
        averageUtilization: {{ $serviceConfig.autoscaling.targetMemoryUtilization | default 80 }}
{{- end }}
{{- end }}
