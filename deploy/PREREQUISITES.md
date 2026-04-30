# Deployment Prerequisites — Linux

Tools required to deploy OpenMakerSuite on a Linux host. Three distro families
are covered:

- **Debian family** — Ubuntu 22.04+/24.04, Debian 12 (bookworm)+
- **Red Hat family** — Fedora 40+, RHEL/Alma/Rocky 9+
- **Arch family** — Arch Linux, EndeavourOS, Manjaro (rolling)

Pick the column that matches your distro and run the snippets in order. After
each section there's a `Verify` block that prints the installed version — every
deploy path expects those to succeed.

## What you need by deployment path

| Tool             | Docker Compose | Raw Kubernetes | Helm |
|------------------|:--------------:|:--------------:|:----:|
| Git              | ✅             | ✅             | ✅  |
| Docker engine    | ✅             | optional¹      | optional¹ |
| Docker Compose v2| ✅             | —              | —    |
| kubectl          | —              | ✅             | ✅  |
| Helm 3           | —              | —              | ✅  |
| Python 3.11+     | dev only²      | dev only²      | dev only² |
| Node.js 20 LTS³  | dev only²      | dev only²      | dev only² |

¹ Required only if you also build images locally instead of pulling published
ones from the registry. Pure cluster operators can skip Docker.
² The runtime images bundle Python and Node. Install them on the host only if
you are running `backend/` or `frontend/` outside containers (local
development, ad-hoc `manage.py` from a checkout, etc.).
³ The frontend image is built on `node:18-alpine`, but local tooling (Vite,
ESLint, npm scripts) is tested on Node 20 LTS. Pin to 20 unless you have a
specific reason to use 18.

> **Permissions:** Examples use `sudo` for system package installs. On
> single-user dev boxes you can drop `sudo` if you're root; on managed servers
> follow your org's privilege model.

## 1. Git

| Distro family | Command |
|---|---|
| Debian | `sudo apt-get update && sudo apt-get install -y git` |
| Red Hat | `sudo dnf install -y git` |
| Arch | `sudo pacman -S --needed git` |

Verify:

```bash
git --version    # any 2.x is fine
```

## 2. Docker engine + Compose plugin

Docker Compose v2 ships as a Docker CLI plugin (`docker compose ...`, no
hyphen). The legacy `docker-compose` (v1) Python script is deprecated and
**will not work** with the bundled `docker-compose.prod.yml`.

### Debian / Ubuntu

Use Docker's official APT repository (the distro `docker.io` package is older
and ships v1 of Compose):

```bash
sudo apt-get update
sudo apt-get install -y ca-certificates curl gnupg
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/$(. /etc/os-release; echo "$ID")/gpg \
  | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg
echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
  https://download.docker.com/linux/$(. /etc/os-release; echo "$ID") \
  $(. /etc/os-release; echo "$VERSION_CODENAME") stable" \
  | sudo tee /etc/apt/sources.list.d/docker.list >/dev/null
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io \
  docker-buildx-plugin docker-compose-plugin
```

### Fedora / RHEL / Alma / Rocky

```bash
sudo dnf -y install dnf-plugins-core
sudo dnf config-manager --add-repo https://download.docker.com/linux/fedora/docker-ce.repo
# RHEL/Alma/Rocky: swap the URL above for .../linux/centos/docker-ce.repo
sudo dnf install -y docker-ce docker-ce-cli containerd.io \
  docker-buildx-plugin docker-compose-plugin
```

### Arch

```bash
sudo pacman -S --needed docker docker-buildx docker-compose
```

(The Arch `docker-compose` package is the v2 plugin under the
`/usr/lib/docker/cli-plugins/` path — `docker compose` works out of the box.)

### Enable, start, and grant your user access

All three families:

```bash
sudo systemctl enable --now docker
sudo usermod -aG docker "$USER"
# log out and back in (or run `newgrp docker`) so group membership takes effect
```

Verify:

```bash
docker --version          # Docker version 24.x or newer
docker compose version    # Docker Compose version v2.x
docker run --rm hello-world
```

If `hello-world` fails with a permission error, your shell hasn't picked up the
`docker` group yet. Re-login or `newgrp docker`.

## 3. kubectl

You need a `kubectl` whose minor version is within ±1 of your cluster's
control-plane version. The snippets below install the latest stable client;
pin explicitly if your cluster is older.

### Debian / Ubuntu

```bash
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://pkgs.k8s.io/core:/stable:/v1.30/deb/Release.key \
  | sudo gpg --dearmor -o /etc/apt/keyrings/kubernetes-apt-keyring.gpg
echo 'deb [signed-by=/etc/apt/keyrings/kubernetes-apt-keyring.gpg] https://pkgs.k8s.io/core:/stable:/v1.30/deb/ /' \
  | sudo tee /etc/apt/sources.list.d/kubernetes.list >/dev/null
sudo apt-get update
sudo apt-get install -y kubectl
```

### Fedora / RHEL / Alma / Rocky

```bash
cat <<'EOF' | sudo tee /etc/yum.repos.d/kubernetes.repo >/dev/null
[kubernetes]
name=Kubernetes
baseurl=https://pkgs.k8s.io/core:/stable:/v1.30/rpm/
enabled=1
gpgcheck=1
gpgkey=https://pkgs.k8s.io/core:/stable:/v1.30/rpm/repodata/repomd.xml.key
EOF
sudo dnf install -y kubectl
```

### Arch

```bash
sudo pacman -S --needed kubectl
```

Verify:

```bash
kubectl version --client          # Client Version: v1.30.x (or newer)
```

> Bump the `v1.30` path in the repo URLs to match your cluster as new minors
> ship. The Kubernetes pkgs.k8s.io repos are versioned by minor on purpose.

## 4. Helm 3

Required only for the Helm deployment path (`deploy/helm/openmakersuite`).

| Distro family | Command |
|---|---|
| Debian | Use the upstream installer below. |
| Red Hat | `sudo dnf install -y helm` (Fedora) or use the upstream installer on RHEL/Alma/Rocky. |
| Arch | `sudo pacman -S --needed helm` |

Upstream one-liner (works on all distros, installs to `/usr/local/bin`):

```bash
curl -fsSL https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash
```

Verify:

```bash
helm version --short    # v3.14.x or newer
```

## 5. Python 3.11+

Only required for running `backend/` outside Docker (local dev, one-off
`manage.py` against a checkout). The published backend image already bundles
Python 3.11.

| Distro family | Command |
|---|---|
| Debian (Ubuntu 24.04 / Debian 12) | `sudo apt-get install -y python3 python3-venv python3-pip` |
| Debian (Ubuntu 22.04, no 3.11) | `sudo add-apt-repository -y ppa:deadsnakes/ppa && sudo apt-get update && sudo apt-get install -y python3.11 python3.11-venv` |
| Red Hat | `sudo dnf install -y python3.11 python3.11-pip` |
| Arch | `sudo pacman -S --needed python python-pip` |

Verify:

```bash
python3 --version    # Python 3.11.x or newer
```

If your distro's default `python3` is older, invoke 3.11 explicitly:

```bash
python3.11 -m venv backend/.venv
source backend/.venv/bin/activate
pip install -r backend/requirements.txt -r backend/requirements-dev.txt
```

## 6. Node.js 20 LTS

Only required for running `frontend/` outside Docker. The frontend image
itself builds on `node:18-alpine`; local tooling targets Node 20 LTS.

The cleanest cross-distro path is [`nvm`](https://github.com/nvm-sh/nvm),
which avoids fighting distro packages:

```bash
curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.39.7/install.sh | bash
exec "$SHELL"           # or: source ~/.nvm/nvm.sh
nvm install --lts=iron  # Node 20 LTS
nvm alias default lts/iron
```

If you'd rather use distro packages:

| Distro family | Command |
|---|---|
| Debian | NodeSource: `curl -fsSL https://deb.nodesource.com/setup_20.x \| sudo -E bash - && sudo apt-get install -y nodejs` |
| Red Hat | `sudo dnf module install -y nodejs:20/common` (Fedora 40+ uses `sudo dnf install -y nodejs`) |
| Arch | `sudo pacman -S --needed nodejs npm` |

Verify:

```bash
node --version    # v20.x.x
npm --version     # 10.x or newer
```

## 7. Optional: pre-commit (contributors only)

If you are committing back to the repo, install `pre-commit` so the same
formatters CI runs land on your changes locally:

```bash
pip install --user pre-commit
pre-commit install
```

`pre-commit run --all-files` should be clean before you push. CI rejects
diffs with `isort`, `black`, or `flake8` failures.

## End-to-end verification

After the relevant sections above, run the block matching your deploy path.
All commands should exit zero.

### Docker Compose path

```bash
git --version
docker --version
docker compose version
docker run --rm hello-world
```

### Raw Kubernetes path

```bash
git --version
kubectl version --client
kubectl cluster-info        # against your kubeconfig context
```

### Helm path

```bash
git --version
kubectl version --client
helm version --short
helm lint deploy/helm/openmakersuite   # from a repo checkout
```

If every command in your path's block succeeds, the host is ready. Continue
with [`docs/DEPLOYMENT.MD`](../docs/DEPLOYMENT.MD) for the deploy itself,
[`deploy/SMOKE_TESTS.md`](./SMOKE_TESTS.md) for post-deploy validation, and
[`deploy/BACKUP_RESTORE.md`](./BACKUP_RESTORE.md) for the backup/restore
playbook, and [`deploy/UPGRADE_ROLLBACK.md`](./UPGRADE_ROLLBACK.md) once you
start upgrading an existing install.
