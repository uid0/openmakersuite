# CI/CD

This document holds only what the workflow files cannot tell you. It does not
list jobs, steps, tool versions or secrets: each of those lives in exactly one
place, and a copy here would drift the first time that place changed.

## Where the inventory lives

| Question | Authoritative source |
|----------|----------------------|
| What runs on a PR or push, in what order, on what versions | `.github/workflows/ci.yml` |
| Which paths wake which jobs | the `changes` job's `filters:` block in `ci.yml` |
| What the lint jobs run, and how to run the same thing locally | `scripts/ci-lint.sh` (header comment; `backend` / `frontend` / a single check name) |
| Image publishing, releases, Semgrep scanning | the other files in `.github/workflows/` |
| Which secrets a workflow reads | `grep -n 'secrets\.' .github/workflows/*.yml` |

To list the current CI jobs without reading the whole file:

```bash
grep -n '^    name:' .github/workflows/ci.yml
```

## Why CI is shaped this way

**One required check, many jobs.** The `main` ruleset requires a single
status check, `✅ CI Complete` (the `ci-complete` job). Each validation job is a
merge gate only because `ci-complete` lists it in `needs:` *and* checks its
result. A new validation job added to `ci.yml` but not to both places runs, can
fail, and still does not block a merge.

**Path filters, with skipped counted as passed.** The expensive jobs (tests,
Docker, prod-stack boot, Helm/K8s rendering) only run when the paths they
depend on changed, so a doc-only PR does not pay for them. `ci-complete`
accepts `skipped` as a pass for that reason, and treats `failure` and
`cancelled` as a fail. The corollary: if a job is skipped when you expected it
to run, the path filter is the thing to fix, not the job. Editing `ci.yml`
itself matches every filter, so a workflow change runs everything.

**Lint is split from tests.** The lint jobs run in parallel with the test jobs
and time out quickly, so a formatting failure is reported in minutes instead of
after the test suite. They call `scripts/ci-lint.sh` rather than inlining
commands, so the local command and the no-mistakes lint step
(`.no-mistakes.yaml`) run exactly what CI runs.

**Some checks run twice on purpose.** A guard that lives in the backend test
suite can also appear in a frontend job, because a frontend-only PR skips
Backend Tests entirely. The comment above each such step names the hole it
closes; do not "deduplicate" one without reading it.

## What gates a merge

Merge rules for `main` live in a GitHub repository ruleset, not in the repo, so
nothing under `.github/` shows them. Check the live settings with:

```bash
gh api repos/uid0/openmakersuite/rulesets \
  --jq '.[] | select(.target == "branch") | .id' |
  xargs -I{} gh api repos/uid0/openmakersuite/rulesets/{} --jq '{name, rules}'
```

As of 2026-09-13 the default-branch ruleset requires: a pull request with all
review threads resolved, linear history, the `✅ CI Complete` status check, and
Semgrep code-scanning and code-quality results within the ruleset's alert
thresholds. A block from those shows up in the PR's code-scanning results, not
as a failed CI job.

It does **not** require branches to be up to date before merging. Any check
that only fires when both sides of a conflict are in the tree CI evaluates (the
"conflicting migration leaves" step is the explicit one; its comment in
`ci.yml` explains) can pass on a stale PR and fail on `main` after the merge.

## How release and deploy relate to CI

- **Releases** (`release.yml`) start when a CI run on `main` completes
  successfully, or by manual dispatch. It cuts a version tag and a signed
  GitHub release only if there are commits since the last release.
  `docs/RELEASE_AUTOMATION.md` covers versioning and signature verification.
- **Images** (`docker-build.yml`) are built on push as well as after CI
  completes, so a published image tag is not evidence that CI passed for that
  commit. Check the CI run for the image's `sha-*` commit before relying on it.
- **Nothing in GitHub Actions deploys a running server.** Production deploys
  are operator-run (`deploy.sh` for Compose, Helm or Kustomize for
  Kubernetes); `docs/deployment-paths.md` routes to each runbook. The CI jobs
  that boot the prod compose stack and render the Helm/K8s manifests are
  pre-merge validation of those deploy paths, not a deployment.

## When a job fails

```bash
gh run list --workflow=ci.yml --branch <branch>
gh run view <run-id> --log-failed      # only the failing steps' logs
gh run rerun <run-id> --failed         # re-run just the failed jobs
```

- **Lint job:** run `scripts/ci-lint.sh <check>` locally with the check name
  from the failing step; it uses the same pinned tool versions.
- **Anything with services or env:** reproducing locally means matching the
  failing job's `env:` block (job-level and step-level). Copy it from `ci.yml`,
  not from memory.
- **Artifacts:** jobs that produce reports, traces or rendered manifests upload
  them with `actions/upload-artifact`; the step names the artifact and whether
  it uploads always or only on failure. Download with
  `gh run download <run-id> -n <artifact-name>`.
- **Job skipped, or ran when it should not have:** read the `Detect Changes`
  job's log for that run to see which filters matched, then the filter
  definitions in `ci.yml`.

## Backup and restore

`deploy/BACKUP_RESTORE.md` is the operational playbook — database, media,
config and secrets, for both the Compose and Kubernetes deploys, with the
restore ORDER that matters. `scripts/backup-*.sh` and `scripts/restore-*.sh`
are the scripts it drives; each one's header comment documents its own flags,
defaults and exit codes. Neither is summarised here, for the reason at the top.
