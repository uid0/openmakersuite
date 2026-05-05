"""``manage.py check_permission_matrix`` — verify or regenerate the matrix.

Reports drift between ``backend/config/api_permission_matrix.yaml`` and the
actual ``permission_classes`` for every URL-routed DRF view. With
``--write``, regenerates the YAML from the live URL conf so a developer can
review the diff in their PR.

The CI test ``config/tests/test_permission_matrix.py`` calls the same
introspection helpers, so a green ``manage.py check_permission_matrix`` run
locally means CI will be green too.
"""

from __future__ import annotations

import sys

from django.core.management.base import BaseCommand

import yaml

from config.permission_matrix import MATRIX_PATH, diff, introspect_endpoints, load_matrix


def _emit_yaml(actual) -> str:
    """Render the introspected snapshot as a YAML document with a stable key
    order so regenerated output is review-friendly."""
    grouped: dict[str, dict] = {}
    for key, snap in actual.items():
        view_entry = grouped.setdefault(key.view_path, {})
        if key.action is None:
            view_entry["permission_classes"] = list(snap.permission_classes)
        else:
            actions = view_entry.setdefault("actions", {})
            actions[key.action] = {"permission_classes": list(snap.permission_classes)}
    sorted_views = {
        view_path: {
            **(
                {"permission_classes": entry["permission_classes"]}
                if "permission_classes" in entry
                else {}
            ),
            **({"actions": dict(sorted(entry["actions"].items()))} if "actions" in entry else {}),
        }
        for view_path, entry in sorted(grouped.items())
    }
    header = (
        "# Auto-generated snapshot of permission_classes for every DRF view.\n"
        "# Source of truth for docs/API_PERMISSION_MATRIX.md.\n"
        "# Regenerate with: python manage.py check_permission_matrix --write\n"
        "#\n"
        "# Tests in config/tests/test_permission_matrix.py fail when this file\n"
        "# drifts from the actual URL conf — update both the YAML and the\n"
        "# Markdown matrix in the same PR that changes permission_classes.\n"
    )
    return header + yaml.safe_dump(
        {"views": sorted_views}, sort_keys=False, default_flow_style=False
    )


class Command(BaseCommand):
    help = "Verify (or regenerate) the API permission matrix snapshot."

    def add_arguments(self, parser):
        parser.add_argument(
            "--write",
            action="store_true",
            help="Regenerate api_permission_matrix.yaml from the live URL conf.",
        )

    def handle(self, *args, **options):
        actual = introspect_endpoints()
        if options["write"]:
            MATRIX_PATH.write_text(_emit_yaml(actual))
            self.stdout.write(self.style.SUCCESS(f"Wrote {len(actual)} entries to {MATRIX_PATH}"))
            return

        try:
            expected = load_matrix()
        except FileNotFoundError:
            self.stderr.write(
                self.style.ERROR(f"{MATRIX_PATH} not found. Run with --write to generate it.")
            )
            sys.exit(2)

        missing, stale, mismatched = diff(actual, expected)
        if not (missing or stale or mismatched):
            self.stdout.write(self.style.SUCCESS(f"OK — {len(actual)} endpoints match the matrix."))
            return

        if missing:
            self.stderr.write("Endpoints missing from YAML:")
            for line in missing:
                self.stderr.write(line)
        if stale:
            self.stderr.write("Stale YAML entries (view no longer routed):")
            for line in stale:
                self.stderr.write(line)
        if mismatched:
            self.stderr.write("permission_classes drift:")
            for line in mismatched:
                self.stderr.write(line)
        self.stderr.write(
            "\nRun `python manage.py check_permission_matrix --write` and review "
            "the diff. Update docs/API_PERMISSION_MATRIX.md to match."
        )
        sys.exit(1)
