"""``manage.py validate_production`` — runtime production-safety baseline.

Walks the registered :mod:`config.validators` checks against the loaded
Django settings, prints a category-tagged report, and exits non-zero on
any fatal issue. Complementary to ``scripts/validate-prod-env.sh``:

  - The shell validator reads the ``.env`` file before Django starts.
    It catches placeholder values in the file itself.
  - This command reads ``django.conf.settings`` *after* Django has
    loaded them. It catches drift — a ``settings.py`` override that
    re-enables an unsafe default, an env-var clobber at container start,
    or a feature flag that flips a secure setting back to insecure.

Behavior in non-prod environments: ``DEBUG=True`` is the signal that
we're in dev / CI / a one-off container. The command skips enforcement
in that mode and prints a one-line note instead. Operators who want to
exercise the prod rules locally pass ``--strict`` to enforce regardless
of DEBUG.

Reports never include secret values. Reasons may reference a
placeholder *fragment* (the public part) but never the operator's
chosen string. Tests assert this contract.

Exit codes:
  0 — all checks pass, or skipped because DEBUG=True (without --strict)
  1 — one or more fatal issues; report lists every issue
"""

from __future__ import annotations

import sys
from typing import Iterable

from django.conf import settings as django_settings
from django.core.management.base import BaseCommand

from config.validators import CHECKS, Issue


class Command(BaseCommand):
    help = (
        "Validate the loaded Django settings against the production safety "
        "baseline. See backend/config/validators/ for the category list."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--strict",
            action="store_true",
            help=(
                "Run all checks even when DEBUG=True. Use this for "
                "CI-side validation of the prod-flavor settings."
            ),
        )
        parser.add_argument(
            "--quiet",
            action="store_true",
            help="Suppress the success summary; only print on failure.",
        )

    def handle(self, *args, **opts) -> None:
        strict = bool(opts.get("strict"))
        quiet = bool(opts.get("quiet"))

        if django_settings.DEBUG and not strict:
            self.stdout.write(
                "validate_production: DEBUG=True, skipping production checks "
                "(use --strict to run anyway)."
            )
            return

        issues = list(self._collect_issues())
        fails = [i for i in issues if i.severity == "fail"]
        warns = [i for i in issues if i.severity == "warn"]

        for issue in issues:
            self._print_issue(issue)

        if fails:
            self.stderr.write(
                self.style.ERROR(
                    f"validate_production: {len(fails)} fatal issue(s), "
                    f"{len(warns)} warning(s). Refusing to proceed."
                )
            )
            sys.exit(1)

        if warns and not quiet:
            self.stdout.write(
                self.style.WARNING(f"validate_production: 0 fatal issues, {len(warns)} warning(s).")
            )
        elif not quiet:
            self.stdout.write(
                self.style.SUCCESS("validate_production: all production safety checks passed.")
            )

    def _collect_issues(self) -> Iterable[Issue]:
        for check in CHECKS:
            # Each check is independent — never let one check's
            # exception block the rest from reporting.
            try:
                yield from check.run(django_settings)
            except Exception as exc:  # noqa: BLE001 — surface, don't swallow
                yield Issue(
                    category=check.category or check.__class__.__name__,
                    key="<check_error>",
                    reason=f"check raised {type(exc).__name__}: {exc}",
                )

    def _print_issue(self, issue: Issue) -> None:
        prefix = "FAIL" if issue.severity == "fail" else "WARN"
        line = f"[{prefix}] {issue.category}.{issue.key}: {issue.reason}"
        if issue.severity == "fail":
            self.stderr.write(self.style.ERROR(line))
        else:
            self.stderr.write(self.style.WARNING(line))
