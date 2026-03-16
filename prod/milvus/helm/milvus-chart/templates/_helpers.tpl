{{/*
Expand the name of the chart.
*/}}
{{- define "milvus-tenant.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name based on org_id.
*/}}
{{- define "milvus-tenant.fullname" -}}
{{- .Values.org_id | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create namespace name: milvus-{org_id}
*/}}
{{- define "milvus-tenant.namespace" -}}
{{- printf "milvus-%s" .Values.org_id | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create chart name and version as used by the chart label.
*/}}
{{- define "milvus-tenant.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Common labels
*/}}
{{- define "milvus-tenant.labels" -}}
helm.sh/chart: {{ include "milvus-tenant.chart" . }}
{{ include "milvus-tenant.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
org_id: {{ .Values.org_id }}
{{- end }}

{{/*
Selector labels
*/}}
{{- define "milvus-tenant.selectorLabels" -}}
app.kubernetes.io/name: milvus
app.kubernetes.io/instance: {{ .Values.org_id }}
{{- end }}

{{/*
Milvus service name: {org_id}-milvus
*/}}
{{- define "milvus-tenant.serviceName" -}}
{{- printf "%s-milvus" .Values.org_id }}
{{- end }}

{{/*
gRPC host: {org_id}.milvusdb.usecortex.ai
*/}}
{{- define "milvus-tenant.grpcHost" -}}
{{- printf "%s.%s" .Values.org_id .Values.ingress.domain }}
{{- end }}

{{/*
WebUI host: {org_id}-webui.milvusdb.usecortex.ai
*/}}
{{- define "milvus-tenant.webuiHost" -}}
{{- printf "%s-webui.%s" .Values.org_id .Values.ingress.domain }}
{{- end }}

{{/*
Attu host: {org_id}-attu.milvusdb.usecortex.ai
*/}}
{{- define "milvus-tenant.attuHost" -}}
{{- printf "%s-attu.%s" .Values.org_id .Values.ingress.domain }}
{{- end }}

{{/*
Storage class name
*/}}
{{- define "milvus-tenant.storageClassName" -}}
{{- if .Values.storageClass.create }}
{{- printf "milvus-storage-%s" .Values.org_id }}
{{- else }}
{{- .Values.storageClass.name }}
{{- end }}
{{- end }}

{{/*
TLS secret name
*/}}
{{- define "milvus-tenant.tlsSecretName" -}}
{{- printf "milvus-wildcard-tls-%s" .Values.org_id }}
{{- end }}
