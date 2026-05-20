"""
Remove the cable / cordset abstraction (Cable, PowerPort, HardwiredConnection).

Operators reported the cable/port data model as too painful to keep current
(see the GUPP bead description). Breakers now associate directly with
outlets, assets, and lights; for assets the upstream Disconnect is kept for
lock-out/tag-out via the new ``Asset.disconnect`` FK. Everything else
modelled by these tables is gone.

This is a destructive schema migration — there is no reverse — because the
removed models stored derived/redundant data (outlet ↔ asset edges are now
reconstructable via ``Asset.breaker``).
"""

from __future__ import annotations

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("electrical_circuits", "0010_disconnect_hardwiredconnection"),
        ("loto", "0004_repoint_derived_from_to_breaker"),
    ]

    operations = [
        migrations.DeleteModel(name="HardwiredConnection"),
        migrations.DeleteModel(name="Cable"),
        migrations.DeleteModel(name="PowerPort"),
    ]
