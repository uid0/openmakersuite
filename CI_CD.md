# CI/CD

This file used to carry a hand-maintained inventory of the CI pipeline: its
job list, its language versions, its tool list, its coverage thresholds. All of
it had drifted from `.github/workflows/ci.yml` — wrong job count, wrong Python
and Node versions, the wrong frontend test runner, a dependency scanner CI had
replaced, and instructions to create a `.pre-commit-config.yaml` that is
already in the repo root. Nothing checked any of it, so it was true once.

The inventory is gone rather than corrected, and it should not come back. What
CI does is not a thing to restate; it is a thing to read. The same applies to
anything added here later: if a sentence would have to be edited when a
workflow changes, it belongs in the workflow, not in this file.

## What CI enforces

`.github/workflows/ci.yml` is the source of truth, and is the only accurate
answer to "what runs, in what order, on what versions". Read the job list at
the top of the file. The other workflows beside it (`docker-build.yml`,
`release.yml`, `semgrep.yml`) own their own areas the same way.

To see what a specific run did rather than what the workflow says it would do:

```bash
gh run list --workflow=ci.yml
gh run view <run-id> --log-failed
```

## Running the same checks locally

`CLAUDE.md` and `AGENTS.md` carry the local command set (`pytest`,
`npm test`, `pre-commit run --all-files`) and the bar for calling work done.
Follow those rather than a second copy here; the copy is what went stale.

The one thing worth saying that is not a command: reproducing a CI failure
locally means matching CI's environment variables, which are spelled out in the
`env:` block of the failing job. Copy them from there, not from memory.

## Backup and restore

`deploy/BACKUP_RESTORE.md` is the operational playbook — database, media,
config and secrets, for both the Compose and Kubernetes deploys, with the
restore ORDER that matters. `scripts/backup-*.sh` and `scripts/restore-*.sh`
are the scripts it drives; each one's header comment documents its own flags,
defaults and exit codes. Neither is summarised here, for the reason above.
