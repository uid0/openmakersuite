"""Shared shape for production-safety checks.

A check looks at the LIVE Django settings (post-decouple, post-cast) and
emits zero or more :class:`Issue` instances. The
``validate_production`` command aggregates them, decides the exit code,
and prints a category-tagged report.

Why the live settings vs. the `.env` file: the shell
``scripts/validate-prod-env.sh`` validator already covers the file. The
runtime check catches drift between the file and what Django actually
loaded — a misconfigured ``settings.py`` override, an env-var clobber at
container start, or a feature flag that re-enables an unsafe default
after load.

An Issue must NEVER include the offending value (no SECRET_KEY snippets,
no host strings) — only the category, the setting name, and a
human-readable reason. Reasons may reference a placeholder *fragment*
that matched (those are public; the secret is what the operator chose
beyond the fragment).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Literal

Severity = Literal["fail", "warn"]


@dataclass(frozen=True)
class Issue:
    """One safety-baseline violation surfaced by a check."""

    category: str
    key: str
    reason: str
    severity: Severity = "fail"


class SafetyCheck:
    """Base class for category checks.

    Subclasses set ``category`` and implement ``run`` to yield issues.
    The command iterates each registered check exactly once and treats
    every check as independent — one failing check never short-circuits
    the rest, so a single command run surfaces *all* problems instead of
    making the operator fix-and-rerun N times.
    """

    category: str = ""

    def run(self, settings) -> Iterable[Issue]:
        raise NotImplementedError
