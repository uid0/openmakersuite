# Migrating OMS observability from Sentry to self-hosted Highlight

Tracking issue: **oms-fjs**.

This is the operator runbook for the cutover from hosted Sentry to a
self-hosted [highlight.io](https://highlight.io) stack. The Highlight
docker-compose stack (ClickHouse + Postgres + Kafka + OTel collector) is stood
up on a separate physical host and is **out of scope for this document** — the
runbook below assumes that host is already up and reachable from the OMS
backend container and from end-user browsers.

## What changed in the OMS code

| Area | Before | After |
| --- | --- | --- |
| Backend SDK | `sentry-sdk[django]` (`sentry_sdk.init`) | `highlight-io` (`highlight_io.H(...)`) |
| Frontend SDK | `@sentry/react` (`Sentry.init`, `Sentry.ErrorBoundary`) | `highlight.run` + `@highlight-run/react` (`H.init`, `<ErrorBoundary>`) |
| Source-map upload | Sentry CLI (`sentry-cli releases ...`) | `npx @highlight-run/sourcemap-uploader` |
| Release pipeline | dedicated `sentry-integration` job | source-maps tagged with `--appVersion=$GIT_HASH` at upload time; no separate release call |
| User feedback widget | Sentry's `captureUserFeedback` form inside `ErrorFallback` | dropped — Highlight has no SDK-level equivalent (its `ErrorBoundary` ships a built-in `<ReportDialog>` if you opt in) |

The **boot-ping diagnostic** from oms-78t is preserved: `initHighlight()` emits
`H.consumeError(new Error('OMS frontend boot ping'), 'boot', { ... })` after a
successful init so a deployed bundle that thinks it initialized but is
silently rejected by the collector still leaves a single artifact in the
dashboard.

## Cutover model: clean cut, not coexistence

The bead (oms-fjs AC-5) suggested an optional `OBSERVABILITY_BACKEND` flag for
running both SDKs side-by-side during a transition window. **We did not ship
this flag.** Reasons:

- It would force the codebase to keep `sentry-sdk` and `@sentry/react` as
  active dependencies, contradicting AC-1/AC-2's "remove" requirement.
- It would maintain two parallel error pipelines in `settings.py`,
  `index.tsx`, `services/api.ts`, `App.tsx`, and `ErrorFallback.tsx`. Every
  one of those touch points would need conditional logic + tests.
- The single-deploy cutover is operationally cheap: the only window without
  observability is the time between starting the deploy and the new bundle
  reaching browsers (a few minutes). Sentry events stop arriving when
  `SENTRY_DSN` is absent from the new env; Highlight events start arriving as
  soon as `HIGHLIGHT_PROJECT_ID` resolves on the new bundle.

If you need a rollback, use the **rollback procedure** below instead.

## Required env vars (new)

These replace the previous `SENTRY_*` and `REACT_APP_SENTRY_*` vars, which
should be **removed** from `.env` on the OMS host after the cutover.

### Backend (`backend/.env`)

| Variable | Purpose | Required? |
| --- | --- | --- |
| `HIGHLIGHT_PROJECT_ID` | Verbose project id from `app.highlight.io > Settings > General` (or your self-host equivalent). Empty/unset disables the SDK. | yes (to enable) |
| `HIGHLIGHT_OTLP_ENDPOINT` | gRPC OTLP endpoint of your self-hosted collector, e.g. `https://otel.highlight.example.org:4317`. Empty defaults to `https://otel.highlight.io:4317` (Highlight cloud). | yes for self-host |
| `HIGHLIGHT_ENVIRONMENT` | `production`, `staging`, … — distinguishes events in the dashboard. | recommended |
| `HIGHLIGHT_SERVICE_VERSION` | Deploy git SHA. Lines backend traces up with frontend session replays. The compose file populates this from `${GIT_HASH}`. | recommended |

### Frontend (build-time, baked into the bundle by CRA)

| Variable | Purpose | Required? |
| --- | --- | --- |
| `REACT_APP_HIGHLIGHT_PROJECT_ID` | Same project id as the backend. Empty disables the frontend SDK. | yes (to enable) |
| `REACT_APP_HIGHLIGHT_OTLP_ENDPOINT` | Same as backend for self-host. | yes for self-host |
| `REACT_APP_HIGHLIGHT_ENVIRONMENT` | Same semantics as backend. | recommended |
| `REACT_APP_GIT_HASH` | Already set by CI; reused as the Highlight `version`. | yes (already set) |

CRA inlines `REACT_APP_*` at build time (root cause of the prior Sentry
breakage in oms-78t). The CI job sources these from GitHub Actions secrets;
the docker-compose `args:` block forwards them to the build context. **Setting
them at runtime via `docker run -e` after the image is built has no effect.**

### CI / GitHub secrets

The source-map upload step in `.github/workflows/docker-build.yml` needs:

| Secret | Purpose |
| --- | --- |
| `HIGHLIGHT_API_KEY` | Org-level upload key from `Settings > General > API`. Distinct from the per-project `HIGHLIGHT_PROJECT_ID`. |
| `REACT_APP_HIGHLIGHT_PROJECT_ID` | Same value baked into the bundle — also needed at upload time so maps land in the right project. |
| `REACT_APP_HIGHLIGHT_OTLP_ENDPOINT` | Self-host collector URL for the prod build. |

Run `./scripts/setup-release-automation.sh` to populate these via `gh secret`,
or set them by hand in GitHub Settings > Secrets and variables > Actions.

The legacy Sentry secrets (`SENTRY_AUTH_TOKEN`, `SENTRY_ORG`, `SENTRY_PROJECT`,
`REACT_APP_SENTRY_DSN`) can be deleted from the repo once the Highlight
cutover is verified.

## Cutover procedure

This is a single-deploy cutover. Estimated total observability blackout: the
time between cutting the deploy and the new bundle reaching active browsers
(typically 1–5 minutes).

1. **Confirm the Highlight host is reachable.** From the OMS docker host:

   ```bash
   curl -sv -o /dev/null https://otel.highlight.example.org:4317/v1/traces \
     -H 'Content-Type: application/grpc' --max-time 5 || \
       echo "collector unreachable — fix this before continuing"
   ```

   The endpoint will reject the request with a non-200 (it expects gRPC, not
   curl) — what you're checking is that TCP+TLS handshake completes.

2. **Set GitHub secrets** (one-time): `HIGHLIGHT_API_KEY`,
   `REACT_APP_HIGHLIGHT_PROJECT_ID`, and (for self-host)
   `REACT_APP_HIGHLIGHT_OTLP_ENDPOINT`. See the table above.

3. **Update `backend/.env` on the OMS host:**

   - Add: `HIGHLIGHT_PROJECT_ID`, `HIGHLIGHT_OTLP_ENDPOINT`,
     `HIGHLIGHT_ENVIRONMENT=production`.
   - Remove: `SENTRY_DSN`, `SENTRY_ENVIRONMENT`, `SENTRY_RELEASE`. The
     validator in `scripts/validate-prod-env.sh` no longer checks for
     `SENTRY_DSN`; it now validates `HIGHLIGHT_OTLP_ENDPOINT` is `https://`.

4. **Run the validator:**

   ```bash
   ./scripts/validate-prod-env.sh /path/to/backend/.env
   ```

   It must exit 0.

5. **Deploy.** Standard `./deploy.sh` flow — pulls the new images that bake in
   the new `REACT_APP_HIGHLIGHT_*` build args.

6. **Verify in the Highlight dashboard** (see next section).

## Verification

After the deploy is up, confirm the full pipeline works **before** removing
the old Sentry secrets.

1. **Backend boot.** Tail the backend container logs and confirm the SDK
   initialized — there is no explicit "initialized" message from
   `highlight-io`, but you should see no `highlight_io` errors and no
   `OTLPSpanExporter` connection failures. A working init looks silent.

2. **Backend exception capture.** Trigger a known exception path (e.g. a
   Django admin URL with a deliberately bad query) or run a one-shot Celery
   task that raises. The exception should appear in the Highlight dashboard
   under **Errors** within ~30 seconds, with a full Python traceback.

3. **Frontend boot ping.** Open the deployed frontend in a fresh browser
   session. In the JS console you should see exactly one
   `[Highlight] initialized (env=…, release=…, project=…)` line, and the
   Highlight dashboard should show a session containing an error event with
   the message `OMS frontend boot ping`. **If no boot-ping arrives**, the
   bundle is silently failing to reach the collector — check the network
   panel for blocked OTLP requests (CSP / CORS / TLS issue on the Highlight
   host).

4. **Frontend session replay.** From the same browser session, click around
   for a few seconds, then trigger a deliberate error (the simplest path is
   to hit a 5xx from the API). Within ~1 minute, the dashboard's **Sessions**
   tab should show your session with the click stream and a session replay
   that resolves stack frames against the just-uploaded source maps.

5. **Source-map resolution.** Open the captured frontend error and confirm
   the stack frames show original TS line numbers (not minified bundle
   offsets). If frames are minified, the source-map upload step in
   docker-build.yml didn't run or the `--appVersion` doesn't match
   `REACT_APP_GIT_HASH` in the bundle. Re-run the workflow.

## Rollback

If something is wrong with the Highlight integration and you need errors back
in Sentry **before the next code revision can be deployed**, the rollback is
to redeploy the prior tagged image:

```bash
# On the OMS host
GIT_HASH=<prior-known-good-sha> ./deploy.sh
```

The prior bundle still contains the Sentry SDK and reads `SENTRY_DSN` from
the env. To re-enable error reporting on the rollback:

1. Restore `SENTRY_DSN`, `SENTRY_ENVIRONMENT`, `REACT_APP_SENTRY_DSN` to
   `backend/.env` (they were captured in the previous git revision of the
   file).
2. Restart: `docker compose -f docker-compose.prod.yml up -d`.

There is **no in-place rollback path** — the new image has no Sentry SDK, so
re-adding `SENTRY_DSN` to `.env` on the new image is a no-op.

A revert PR (`git revert <oms-fjs merge sha>`) is the supported path back to
Sentry if Highlight is found to be unsuitable; the revert restores
`backend/requirements.txt`, `frontend/package.json`, and all the SDK init
sites in one commit.

## Out of scope

- Standing up the Highlight self-host stack itself (separate ops task).
- CSP / network egress rules to permit the collector endpoint from end-user
  browsers (separate ops task — typically a `connect-src` entry).
- Migrating historical Sentry data into Highlight (no import path exists; the
  cutover is treated as a hard boundary for analytics purposes).
- Wiring ForgeKey device errors into Highlight (separate follow-up bead, see
  oms-fjs `## Coordination`).
