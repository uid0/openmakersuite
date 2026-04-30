{{/*
Common helpers for the openmakersuite chart. Anything that's reused by more
than one resource lives here.
*/}}

{{- define "openmakersuite.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "openmakersuite.fullname" -}}
{{- if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- $name := default .Chart.Name .Values.nameOverride -}}
{{- if contains $name .Release.Name -}}
{{- .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{- define "openmakersuite.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "openmakersuite.labels" -}}
helm.sh/chart: {{ include "openmakersuite.chart" . }}
{{ include "openmakersuite.selectorLabels" . }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/part-of: openmakersuite
{{- end -}}

{{- define "openmakersuite.selectorLabels" -}}
app.kubernetes.io/name: {{ include "openmakersuite.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{- define "openmakersuite.componentLabels" -}}
{{ include "openmakersuite.labels" . }}
app.kubernetes.io/component: {{ .component }}
{{- end -}}

{{- define "openmakersuite.componentSelectorLabels" -}}
{{ include "openmakersuite.selectorLabels" . }}
app.kubernetes.io/component: {{ .component }}
{{- end -}}

{{- define "openmakersuite.serviceAccountName" -}}
{{- if .Values.serviceAccount.create -}}
{{- default (include "openmakersuite.fullname" .) .Values.serviceAccount.name -}}
{{- else -}}
{{- default "default" .Values.serviceAccount.name -}}
{{- end -}}
{{- end -}}

{{/*
Resolve the image reference for a component. Falls back through the
component's image stanza, the chart-wide image registry, and the chart
appVersion when no tag is set. Argument: dict "component" "backend".
*/}}
{{- define "openmakersuite.image" -}}
{{- $top := .top -}}
{{- $comp := .component -}}
{{- $img := index $top.Values $comp "image" -}}
{{- $registry := default "" $top.Values.image.registry -}}
{{- $repo := default (printf "openmakersuite/%s" $comp) $img.repository -}}
{{- if eq $repo "" -}}
{{- $repo = printf "openmakersuite/%s" $comp -}}
{{- end -}}
{{- $tag := default $top.Chart.AppVersion $img.tag -}}
{{- if $registry -}}
{{- printf "%s/%s:%s" $registry $repo $tag -}}
{{- else -}}
{{- printf "%s:%s" $repo $tag -}}
{{- end -}}
{{- end -}}

{{/*
Workers that share the backend image (celery, flower, migrations) call this
helper so a single backend image override propagates everywhere. The component
can still override `repository`/`tag` to ship a different image.
*/}}
{{- define "openmakersuite.workerImage" -}}
{{- $top := .top -}}
{{- $comp := .component -}}
{{- $img := index $top.Values $comp "image" -}}
{{- $registry := default "" $top.Values.image.registry -}}
{{- $repo := default $top.Values.backend.image.repository $img.repository -}}
{{- if eq $repo "" -}}
{{- $repo = $top.Values.backend.image.repository -}}
{{- end -}}
{{- $tag := default (default $top.Chart.AppVersion $top.Values.backend.image.tag) $img.tag -}}
{{- if $registry -}}
{{- printf "%s/%s:%s" $registry $repo $tag -}}
{{- else -}}
{{- printf "%s:%s" $repo $tag -}}
{{- end -}}
{{- end -}}

{{- define "openmakersuite.imagePullSecrets" -}}
{{- with .Values.image.pullSecrets -}}
imagePullSecrets:
{{ toYaml . }}
{{- end -}}
{{- end -}}

{{/*
Names for chart-managed resources. Centralised so renaming is a one-line change.
*/}}
{{- define "openmakersuite.configMapName" -}}
{{ include "openmakersuite.fullname" . }}-env
{{- end -}}

{{- define "openmakersuite.secretName" -}}
{{- if .Values.secrets.existingSecret -}}
{{ .Values.secrets.existingSecret }}
{{- else -}}
{{ include "openmakersuite.fullname" . }}-secrets
{{- end -}}
{{- end -}}

{{- define "openmakersuite.postgresSecretName" -}}
{{- if .Values.postgresql.auth.existingSecret -}}
{{ .Values.postgresql.auth.existingSecret }}
{{- else -}}
{{ include "openmakersuite.fullname" . }}-postgresql
{{- end -}}
{{- end -}}

{{- define "openmakersuite.postgresSecretPasswordKey" -}}
{{- if .Values.postgresql.auth.existingSecret -}}
{{ .Values.postgresql.auth.existingSecretPasswordKey }}
{{- else -}}
postgres-password
{{- end -}}
{{- end -}}

{{- define "openmakersuite.databaseSecretName" -}}
{{- if .Values.externalDatabase.existingSecret -}}
{{ .Values.externalDatabase.existingSecret }}
{{- else -}}
{{ include "openmakersuite.fullname" . }}-database
{{- end -}}
{{- end -}}

{{- define "openmakersuite.databaseSecretUrlKey" -}}
{{- if .Values.externalDatabase.existingSecret -}}
{{ .Values.externalDatabase.existingSecretUrlKey }}
{{- else -}}
database-url
{{- end -}}
{{- end -}}

{{- define "openmakersuite.redisSecretName" -}}
{{- if .Values.externalRedis.existingSecret -}}
{{ .Values.externalRedis.existingSecret }}
{{- else -}}
{{ include "openmakersuite.fullname" . }}-redis
{{- end -}}
{{- end -}}

{{- define "openmakersuite.redisSecretUrlKey" -}}
{{- if .Values.externalRedis.existingSecret -}}
{{ .Values.externalRedis.existingSecretUrlKey }}
{{- else -}}
redis-url
{{- end -}}
{{- end -}}

{{/*
Service hostnames for the bundled stateful services.
*/}}
{{- define "openmakersuite.postgresHost" -}}
{{ include "openmakersuite.fullname" . }}-postgresql
{{- end -}}

{{- define "openmakersuite.redisHost" -}}
{{ include "openmakersuite.fullname" . }}-redis
{{- end -}}

{{- define "openmakersuite.backendServiceName" -}}
{{ include "openmakersuite.fullname" . }}-backend
{{- end -}}

{{- define "openmakersuite.frontendServiceName" -}}
{{ include "openmakersuite.fullname" . }}-frontend
{{- end -}}

{{- define "openmakersuite.flowerServiceName" -}}
{{ include "openmakersuite.fullname" . }}-flower
{{- end -}}

{{/*
Ensures we have a SECRET_KEY even when the operator forgot to set one.
Helm's `lookup` keeps a previously generated value across upgrades.
*/}}
{{- define "openmakersuite.secretKey" -}}
{{- if .Values.secrets.values.SECRET_KEY -}}
{{ .Values.secrets.values.SECRET_KEY }}
{{- else -}}
{{- $existing := lookup "v1" "Secret" .Release.Namespace (include "openmakersuite.secretName" .) -}}
{{- if and $existing (index $existing.data "SECRET_KEY") -}}
{{ index $existing.data "SECRET_KEY" | b64dec }}
{{- else -}}
{{ randAlphaNum 50 }}
{{- end -}}
{{- end -}}
{{- end -}}

{{- define "openmakersuite.postgresPassword" -}}
{{- if .Values.postgresql.auth.password -}}
{{ .Values.postgresql.auth.password }}
{{- else -}}
{{- $existing := lookup "v1" "Secret" .Release.Namespace (include "openmakersuite.postgresSecretName" .) -}}
{{- if and $existing (index $existing.data "postgres-password") -}}
{{ index $existing.data "postgres-password" | b64dec }}
{{- else -}}
{{ randAlphaNum 32 }}
{{- end -}}
{{- end -}}
{{- end -}}

{{/*
Inline env vars that resolve from the chart-managed Secrets containing
DATABASE_URL, REDIS_URL, and the Django SECRET_KEY. Always rendered, regardless
of install mode (bundled vs external Postgres/Redis), because the Secret name
is selected upstream by `openmakersuite.databaseSecretName` / `redisSecretName`.
*/}}
{{- define "openmakersuite.appEnvVars" -}}
- name: DATABASE_URL
  valueFrom:
    secretKeyRef:
      name: {{ include "openmakersuite.databaseSecretName" . }}
      key: {{ include "openmakersuite.databaseSecretUrlKey" . }}
- name: REDIS_URL
  valueFrom:
    secretKeyRef:
      name: {{ include "openmakersuite.redisSecretName" . }}
      key: {{ include "openmakersuite.redisSecretUrlKey" . }}
- name: CELERY_BROKER_URL
  valueFrom:
    secretKeyRef:
      name: {{ include "openmakersuite.redisSecretName" . }}
      key: {{ include "openmakersuite.redisSecretUrlKey" . }}
- name: SECRET_KEY
  valueFrom:
    secretKeyRef:
      name: {{ include "openmakersuite.secretName" . }}
      key: SECRET_KEY
{{- end -}}

{{/*
envFrom entries that pull the non-secret app config map and the optional
sensitive Secret (rendered as `optional: true` so a missing/empty Secret
doesn't break startup).
*/}}
{{- define "openmakersuite.appEnvFrom" -}}
- configMapRef:
    name: {{ include "openmakersuite.configMapName" . }}
- secretRef:
    name: {{ include "openmakersuite.secretName" . }}
    optional: true
{{- end -}}
