{{/*
Common chart helpers.
*/}}

{{- define "oms.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "oms.fullname" -}}
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

{{- define "oms.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "oms.labels" -}}
helm.sh/chart: {{ include "oms.chart" . }}
{{ include "oms.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- with .Values.commonLabels }}
{{ toYaml . }}
{{- end }}
{{- end -}}

{{- define "oms.selectorLabels" -}}
app.kubernetes.io/name: {{ include "oms.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{- define "oms.componentLabels" -}}
{{ include "oms.labels" . }}
app.kubernetes.io/component: {{ .component }}
{{- end -}}

{{- define "oms.componentSelectorLabels" -}}
{{ include "oms.selectorLabels" . }}
app.kubernetes.io/component: {{ .component }}
{{- end -}}

{{- define "oms.commonAnnotations" -}}
{{- with .Values.commonAnnotations }}
{{ toYaml . }}
{{- end }}
{{- end -}}

{{- define "oms.serviceAccountName" -}}
{{- if .Values.serviceAccount.create -}}
{{- default (include "oms.fullname" .) .Values.serviceAccount.name -}}
{{- else -}}
{{- default "default" .Values.serviceAccount.name -}}
{{- end -}}
{{- end -}}

{{/*
Resolve an image reference. Pass:
  dict "image" .Values.foo.image "registry" .Values.imageRegistry "tag" .Chart.AppVersion
*/}}
{{- define "oms.image" -}}
{{- $registry := default .registry .image.registry -}}
{{- $repo := .image.repository -}}
{{- $tag := default .tag .image.tag -}}
{{- if $registry -}}
{{- printf "%s/%s:%s" $registry $repo $tag -}}
{{- else -}}
{{- printf "%s:%s" $repo $tag -}}
{{- end -}}
{{- end -}}

{{- define "oms.imagePullSecrets" -}}
{{- with .Values.imagePullSecrets }}
imagePullSecrets:
{{- toYaml . | nindent 2 }}
{{- end -}}
{{- end -}}

{{- define "oms.secretName" -}}
{{- if .Values.secrets.create -}}
{{- include "oms.fullname" . -}}
{{- else -}}
{{- required "secrets.existingSecret is required when secrets.create=false" .Values.secrets.existingSecret -}}
{{- end -}}
{{- end -}}

{{- define "oms.postgresHost" -}}
{{- if .Values.postgres.internal -}}
{{- printf "%s-postgres" (include "oms.fullname" .) -}}
{{- else -}}
{{- required "postgres.external.host is required when postgres.internal=false (or set postgres.external.url)" .Values.postgres.external.host -}}
{{- end -}}
{{- end -}}

{{- define "oms.postgresPort" -}}
{{- if .Values.postgres.internal -}}
{{- .Values.postgres.service.port -}}
{{- else -}}
{{- .Values.postgres.external.port -}}
{{- end -}}
{{- end -}}

{{- define "oms.databaseUrl" -}}
{{- if and (not .Values.postgres.internal) .Values.postgres.external.url -}}
{{- .Values.postgres.external.url -}}
{{- else -}}
{{- $host := include "oms.postgresHost" . -}}
{{- $port := include "oms.postgresPort" . -}}
{{- printf "postgresql://%s:$(POSTGRES_PASSWORD)@%s:%v/%s" .Values.postgres.auth.username $host $port .Values.postgres.auth.database -}}
{{- end -}}
{{- end -}}

{{- define "oms.redisHost" -}}
{{- if .Values.redis.internal -}}
{{- printf "%s-redis" (include "oms.fullname" .) -}}
{{- else -}}
{{- required "redis.external.host is required when redis.internal=false (or set redis.external.url)" .Values.redis.external.host -}}
{{- end -}}
{{- end -}}

{{- define "oms.redisPort" -}}
{{- if .Values.redis.internal -}}
{{- .Values.redis.service.port -}}
{{- else -}}
{{- .Values.redis.external.port -}}
{{- end -}}
{{- end -}}

{{- define "oms.redisUrl" -}}
{{- if and (not .Values.redis.internal) .Values.redis.external.url -}}
{{- .Values.redis.external.url -}}
{{- else -}}
{{- printf "redis://%s:%v/0" (include "oms.redisHost" .) (include "oms.redisPort" .) -}}
{{- end -}}
{{- end -}}

{{- define "oms.emqxHost" -}}
{{- if .Values.emqx.internal -}}
{{- printf "%s-emqx" (include "oms.fullname" .) -}}
{{- else -}}
{{- required "emqx.external.host is required when emqx.internal=false" .Values.emqx.external.host -}}
{{- end -}}
{{- end -}}

{{- define "oms.emqxApiUrl" -}}
{{- if and (not .Values.emqx.internal) .Values.emqx.external.apiUrl -}}
{{- .Values.emqx.external.apiUrl -}}
{{- else -}}
{{- printf "http://%s:%v/api/v5" (include "oms.emqxHost" .) .Values.emqx.service.dashboardPort -}}
{{- end -}}
{{- end -}}

{{- define "oms.frontendUrl" -}}
{{- default (printf "https://%s" .Values.config.domain) .Values.config.frontendUrl -}}
{{- end -}}

{{- define "oms.allowedHosts" -}}
{{- default .Values.config.domain .Values.config.allowedHosts -}}
{{- end -}}

{{- define "oms.csrfTrustedOrigins" -}}
{{- default (printf "https://%s" .Values.config.domain) .Values.config.csrfTrustedOrigins -}}
{{- end -}}

{{- define "oms.corsAllowedOrigins" -}}
{{- default (printf "https://%s" .Values.config.domain) .Values.config.corsAllowedOrigins -}}
{{- end -}}

{{/*
Backend container env shared between Deployment, Job, and Celery.
Pass the root `.` context.
*/}}
{{- define "oms.backendEnv" -}}
- name: DEBUG
  value: "0"
- name: SECRET_KEY
  valueFrom:
    secretKeyRef:
      name: {{ include "oms.secretName" . }}
      key: django-secret-key
- name: POSTGRES_DB
  value: {{ .Values.postgres.auth.database | quote }}
- name: POSTGRES_USER
  value: {{ .Values.postgres.auth.username | quote }}
- name: POSTGRES_PASSWORD
  valueFrom:
    secretKeyRef:
      name: {{ include "oms.secretName" . }}
      key: postgres-password
- name: DATABASE_URL
  value: {{ include "oms.databaseUrl" . | quote }}
- name: REDIS_URL
  value: {{ include "oms.redisUrl" . | quote }}
- name: CELERY_BROKER_URL
  value: {{ include "oms.redisUrl" . | quote }}
- name: ALLOWED_HOSTS
  value: {{ include "oms.allowedHosts" . | quote }}
- name: CSRF_TRUSTED_ORIGINS
  value: {{ include "oms.csrfTrustedOrigins" . | quote }}
- name: CORS_ALLOWED_ORIGINS
  value: {{ include "oms.corsAllowedOrigins" . | quote }}
- name: FRONTEND_URL
  value: {{ include "oms.frontendUrl" . | quote }}
- name: SENTRY_ENVIRONMENT
  value: {{ .Values.config.sentryEnvironment | quote }}
- name: SENTRY_DSN
  valueFrom:
    secretKeyRef:
      name: {{ include "oms.secretName" . }}
      key: sentry-dsn
      optional: true
- name: MQTT_BROKER_HOST
  value: {{ include "oms.emqxHost" . | quote }}
- name: MQTT_BROKER_PORT
  value: {{ .Values.emqx.service.mqttPort | quote }}
- name: MQTT_BROKER_USERNAME
  valueFrom:
    secretKeyRef:
      name: {{ include "oms.secretName" . }}
      key: mqtt-broker-username
      optional: true
- name: MQTT_BROKER_PASSWORD
  valueFrom:
    secretKeyRef:
      name: {{ include "oms.secretName" . }}
      key: mqtt-broker-password
      optional: true
- name: EMQX_API_URL
  value: {{ include "oms.emqxApiUrl" . | quote }}
- name: EMQX_API_KEY
  valueFrom:
    secretKeyRef:
      name: {{ include "oms.secretName" . }}
      key: emqx-api-key
      optional: true
- name: EMQX_API_SECRET
  valueFrom:
    secretKeyRef:
      name: {{ include "oms.secretName" . }}
      key: emqx-api-secret
      optional: true
- name: FORGEKEY_FIRMWARE_SIGNING_KEY
  valueFrom:
    secretKeyRef:
      name: {{ include "oms.secretName" . }}
      key: forgekey-firmware-signing-key
      optional: true
{{- with .Values.config.extraEnv }}
{{ toYaml . }}
{{- end }}
{{- end -}}
