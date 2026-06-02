# ForgeKey firmware build pipeline

Staff can build ForgeKey firmware from OMS: pick a target, a git ref, and a
version, and a **self-hosted build worker** clones the firmware repo, injects
the live OMS CA + command public key, compiles with PlatformIO, and uploads the
result as a signed [`FirmwareVersion`](../backend/forgekey/models.py) ready to
roll out with the existing rollout campaigns.

## Why a separate worker

The OMS app image ships no git / PlatformIO toolchain on purpose (size +
attack surface). The build is therefore routed to a dedicated Celery queue:

```
CELERY_TASK_ROUTES = {"forgekey.tasks.build_firmware": {"queue": "builds"}}
```

Only the **`firmware_builder`** worker listens on `builds`, so only it runs
`build_firmware`. If that worker isn't running, queued builds simply wait.

## The flow

1. `POST /api/forgekey/firmware-builds/` (staff) — `{device_type, pio_env,
   source_ref, version, mandatory?, release_notes?}`. Creates a `FirmwareBuild`
   (`status=queued`) and enqueues `build_firmware` on the `builds` queue.
2. The worker (`forgekey/services/firmware_build.run_firmware_build`):
   - `git clone --depth 1 --branch <source_ref> <repo>`,
   - overwrites `src/security/oms_ca.h` + `oms_command_pubkey.h` with the
     **active** `CertificateAuthority.cert_pem` and the OMS command public key
     (the headers guard their defaults behind `#ifndef`, so this injects the
     real values),
   - `pio run -e <pio_env>`,
   - uploads `.pio/build/<pio_env>/firmware.bin` as a new `FirmwareVersion`
     (signature + sha256 auto-computed on save),
   - records `status`, `commit_sha`, `ca_fingerprint`, and the build `log` on
     the `FirmwareBuild` row.
3. Roll the resulting `FirmwareVersion` out with the firmware-rollout campaigns.

A failed build is recorded with its log + `error_message` — not retried.

## Deploying the worker

**Production (`docker-compose.prod.yml`)** — `firmware_builder` is **always-on**:
it comes up with the rest of the stack, so a normal
`docker compose -f docker-compose.prod.yml up -d --build` starts it alongside
everything else. You only need to give it git auth.

Two supported auth paths — pick one and put the matching env vars in prod `.env`:

**Recommended — GitHub Personal Access Token (PAT, read-only):**

```bash
# Fine-grained PAT, scoped to the ForgeKey repo, Contents: Read only.
# The worker never pushes; minimum-privilege.
FORGEKEY_BUILDER_GITHUB_TOKEN=github_pat_…
# Repo URL must be HTTPS for the token to take effect.
FORGEKEY_FIRMWARE_REPO_URL=https://github.com/uid0/ForgeKey.git
```

Generate the PAT at <https://github.com/settings/personal-access-tokens/new>,
scoped only to the ForgeKey repository with **Contents: Read** permission.
Rotation = generate a new PAT, update the env var, `docker compose restart firmware_builder`.

**Legacy — SSH deploy key:**

```bash
# A read-only SSH deploy key with read access to the ForgeKey repo
# (GitHub → repo → Settings → Deploy keys). Host path, mounted read-only.
FORGEKEY_DEPLOY_KEY=/abs/path/to/forgekey_deploy_key
FORGEKEY_FIRMWARE_REPO_URL=git@github.com:uid0/ForgeKey.git
```

The PAT path is honored when `FORGEKEY_BUILDER_GITHUB_TOKEN` is set AND the
repo URL is HTTPS. Otherwise the worker falls back to the SSH key. Until one
of the two is configured, the container runs but each build fails at
`git clone`.

**Development (`docker-compose.yml`)** — the worker is **opt-in** (compose
profile `firmware-build`) so a routine `docker compose up` stays lightweight:

```bash
export FORGEKEY_DEPLOY_KEY=/abs/path/to/forgekey_deploy_key
docker compose --profile firmware-build up -d --build firmware_builder
```

The image (`backend/Dockerfile.firmware-builder`) extends the backend runtime
with `git`, `openssh-client`, and `platformio`, pre-trusts GitHub's host key,
and runs `celery -A config worker -Q builds`. The deploy key is mounted
read-only at `/root/.ssh/id_ed25519`.

### Settings

| Setting / env | Default | Purpose |
|---|---|---|
| `FORGEKEY_FIRMWARE_REPO_URL` | `git@github.com:uid0/ForgeKey.git` | Repo the worker clones |
| `FORGEKEY_DEPLOY_KEY` (compose) | `/dev/null` | Host path of the SSH deploy key mounted into the worker |

## Notes

- `pio_env` is a PlatformIO environment from the firmware's `platformio.ini`
  (e.g. `seeed_xiao_esp32s3`, `seeed_xiao_epaper`).
- `version` must be unique across `FirmwareVersion` — reusing one fails the
  build on the unique constraint.
- The build runs `--concurrency=1` (one build at a time); raise it only if the
  worker host has the cores + RAM for parallel PlatformIO builds.
