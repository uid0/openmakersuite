"""``epaper_preview`` writes its PNG somewhere another local user can't redirect.

The command used to default ``--out`` to a predictable ``/tmp`` path and
write it with a symlink-following ``open()``. It now defaults to the working
directory and refuses a symlink at the target.
"""

from __future__ import annotations

import os

from django.core.management import call_command
from django.core.management.base import CommandError

import pytest

pytestmark = pytest.mark.django_db

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def test_default_out_is_working_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    call_command("epaper_preview")

    written = tmp_path / "epaper_preview.png"
    assert written.read_bytes().startswith(PNG_MAGIC)


def test_existing_regular_file_is_overwritten(tmp_path, monkeypatch):
    """Re-running the design loop replaces the previous preview."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "epaper_preview.png").write_bytes(b"stale")

    call_command("epaper_preview")

    assert (tmp_path / "epaper_preview.png").read_bytes().startswith(PNG_MAGIC)


def test_symlink_at_target_is_refused(tmp_path):
    victim = tmp_path / "victim.txt"
    victim.write_bytes(b"keep me")
    link = tmp_path / "epaper_preview.png"
    os.symlink(victim, link)

    with pytest.raises(CommandError, match="Cannot write"):
        call_command("epaper_preview", "--out", str(link))

    assert victim.read_bytes() == b"keep me"
    assert link.is_symlink()
