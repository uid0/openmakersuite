# API-first deployment automation

## Context
OpenMakerSuite should be straightforward for makerspaces to adopt whether they want a local development setup, a single-host Docker Compose deployment, or a Kubernetes deployment managed by Helm. The project should document deployment in an open, distro-friendly way and treat the backend API as the contract the frontend and integrations consume.

## Scope
- In: API-first deployment documentation, Docker Compose deployment flow, Kubernetes manifests, Helm chart, environment configuration examples, CI/CD delivery from `main`, and self-hosting guidance for common Linux distributions.
- Out: Replacing Django or React, adding a required paid hosting provider, changing product workflows unrelated to deployment, and requiring one specific Kubernetes distribution.

## Criteria

### AC-1: Deployment documentation offers clear paths
- **Given** a new makerspace operator opens the project documentation
- **When** they look for deployment instructions
- **Then** they can choose between local development, Docker Compose self-hosting, raw Kubernetes manifests, and Helm-based Kubernetes deployment without needing to infer which path applies to them

### AC-2: Documentation supports popular Linux distributions
- **Given** an operator uses Ubuntu/Debian, Fedora/RHEL-compatible Linux, or Arch-compatible Linux
- **When** they follow the prerequisite documentation
- **Then** they see distro-appropriate package manager commands or notes for installing Docker, Docker Compose, kubectl, Helm, Git, Node, Python, and other required tooling

### AC-3: Configuration is environment-driven
- **Given** an operator deploys with Docker Compose, Kubernetes manifests, or Helm
- **When** they configure required values
- **Then** secrets, hostnames, database settings, Redis settings, email/webhook settings, Sentry settings, CORS/CSRF origins, and public API URLs are provided through documented environment variables, Kubernetes Secrets, or Helm values rather than hardcoded files

### AC-4: Frontend consumes documented public API URLs
- **Given** the frontend is built for any supported deployment target
- **When** it performs application actions
- **Then** it calls documented public backend API endpoints through configurable API base URLs and does not rely on private container names, localhost-only assumptions, or server-side template coupling

### AC-5: Docker Compose production deployment is hands-off
- **Given** an operator has Docker and Docker Compose installed
- **When** they follow the production Docker Compose deployment instructions with required environment values
- **Then** the database, Redis, backend, frontend, static/media handling, migrations, and health checks come up with a single documented command sequence

### AC-6: Docker Compose deployment supports hands-on operation
- **Given** an operator wants to inspect or customize deployment steps
- **When** they use the documented manual Docker Compose path
- **Then** they can separately run build, migration, static asset, admin-user, backup, restore, log, upgrade, and rollback commands

### AC-7: Kubernetes manifests deploy the full application
- **Given** an operator has an existing Kubernetes cluster
- **When** they apply the raw Kubernetes manifests with documented substitutions or overlays
- **Then** the backend, frontend, worker processes, PostgreSQL or external database connection, Redis or external Redis connection, services, ingress, config maps, secrets, persistent volumes, migrations, and health probes are represented

### AC-8: Helm chart deploys the full application
- **Given** an operator has Helm installed
- **When** they install the OpenMakerSuite chart with a documented values file
- **Then** Helm renders deployable resources for backend, frontend, Celery worker, optional Flower, PostgreSQL or external database, Redis or external Redis, ingress, persistence, secrets, migrations, and health probes

### AC-9: Helm values support common deployment choices
- **Given** an operator customizes `values.yaml`
- **When** they choose internal or external database/Redis, ingress on/off, TLS on/off, persistence sizes, replica counts, resource requests/limits, image tags, and optional observability settings
- **Then** the rendered chart reflects those choices without editing templates directly

### AC-10: Main branch publishes deployable artifacts
- **Given** a commit is merged to `main`
- **When** CI/CD completes successfully
- **Then** backend and frontend container images are built, tagged with the git SHA and a stable channel tag, pushed to a documented registry, and the Helm chart package or OCI chart artifact is published

### AC-11: CI/CD does not publish broken releases
- **Given** CI/CD is preparing deployment artifacts
- **When** linting, unit tests, coverage gates, frontend build, Docker image build, manifest validation, Helm linting, or template rendering fails
- **Then** no images or charts are published for that commit

### AC-12: Kubernetes and Helm are validated in CI
- **Given** a pull request changes deployment files
- **When** CI runs
- **Then** Kubernetes manifests are schema-validated, the Helm chart passes `helm lint`, and representative `helm template` outputs are validated for default, external database, external Redis, ingress, and persistence configurations

### AC-13: Deployment docs include upgrade and rollback
- **Given** an existing operator is upgrading OpenMakerSuite
- **When** they follow the upgrade documentation
- **Then** they can back up data, pull or select a new version, run migrations, verify health, and roll back to the previous image/chart version if verification fails

### AC-14: Deployment docs include backup and restore
- **Given** an operator wants to protect production data
- **When** they follow the backup and restore documentation
- **Then** they can back up and restore PostgreSQL data, media uploads, environment configuration, and Kubernetes persistent volumes or external storage references

### AC-15: Deployment docs include smoke tests
- **Given** an operator completes any supported deployment path
- **When** they run the documented smoke checks
- **Then** they can verify frontend availability, backend health, API documentation availability, admin access, database connectivity, Redis connectivity, static/media serving, and an unauthenticated public makerspace workflow

### AC-16: Documentation remains open and provider-neutral
- **Given** a reader reviews the deployment guide
- **When** hosted services or managed Kubernetes examples are mentioned
- **Then** the guide presents them as optional examples and keeps the primary instructions usable on self-hosted Linux systems without requiring a specific cloud vendor
