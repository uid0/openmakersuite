# CI/CD

## What CI enforces

`.github/workflows/ci.yml` is the source of truth, and is the only accurate
answer to "what runs, in what order, on what versions". Other workflows in
`.github/workflows/` own their own areas the same way.

To see what a specific run did rather than what the workflow says it would do:

```bash
gh run list --workflow=ci.yml
gh run view <run-id> --log-failed
```

## Running the same checks locally

`CLAUDE.md` and `AGENTS.md` own the local command set and the bar for calling
work done.

The one thing worth saying that is not a command: reproducing a CI failure
locally means matching CI's environment variables, which are spelled out in the
`env:` block of the failing job. Copy them from there, not from memory.

## Backup and restore

`deploy/BACKUP_RESTORE.md` is the operational playbook — database, media,
config and secrets, for both the Compose and Kubernetes deploys, with the
restore ORDER that matters. `scripts/backup-*.sh` and `scripts/restore-*.sh`
are the scripts it drives; each one's header comment documents its own flags,
defaults and exit codes. Neither is summarised here, for the reason above.
