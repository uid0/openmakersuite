"""Data-migration test for facilities.0002_migrate_asset_requirements (#880).

Drives the migration graph with ``MigrationExecutor``: rewinds to just after
``facilities.0001`` (which restores the old Asset columns), seeds pre-migration
data with the historical models, runs the copy migration forward, and asserts
the values landed on ``AssetSiteRequirements`` and that the legacy free-text
``circuit`` string was consolidated correctly.

Uses ``transaction=True`` (migrations manage their own transactions) and always
restores the graph to HEAD in ``finally`` so later tests see a normal schema.
"""

from django.db import connection
from django.db.migrations.executor import MigrationExecutor

import pytest

START = [("facilities", "0001_initial")]
END = [
    ("facilities", "0002_migrate_asset_requirements"),
    ("inventory", "0083_remove_asset_breaker_remove_asset_circuit_and_more"),
]


def _migrate(targets):
    executor = MigrationExecutor(connection)
    executor.migrate(targets)
    executor.loader.build_graph()
    return executor


@pytest.mark.django_db(transaction=True)
def test_migration_copies_requirements_and_consolidates_circuit():
    executor = _migrate(START)
    old_apps = executor.loader.project_state(START).apps
    try:
        Location = old_apps.get_model("inventory", "Location")
        Asset = old_apps.get_model("inventory", "Asset")
        PowerPanel = old_apps.get_model("electrical_circuits", "PowerPanel")
        PowerBreaker = old_apps.get_model("electrical_circuits", "PowerBreaker")
        PowerCircuit = old_apps.get_model("electrical_circuits", "PowerCircuit")
        Disconnect = old_apps.get_model("electrical_circuits", "Disconnect")

        loc = Location.objects.create(name="Shop")
        panel = PowerPanel.objects.create(location=loc, name="P1")
        breaker = PowerBreaker.objects.create(panel=panel, position="1", amperage=20)
        circuit_obj = PowerCircuit.objects.create(breaker=breaker)
        disc = Disconnect.objects.create(circuit=circuit_obj, label="D1", disconnect_type="unfused")

        # A: breaker + disconnect + both bools set → full profile row. It also
        # has a legacy circuit string, but because it HAS a breaker the string
        # is redundant and dropped (not folded).
        a = Asset.objects.create(
            name="A",
            asset_tag="MIG-A",
            breaker=breaker,
            disconnect=disc,
            needs_compressed_air=True,
            needs_ventilation=True,
            circuit="ignored-because-breaker",
        )
        # B: only a free-text circuit, no breaker + blank breaker_location →
        # circuit folded into breaker_location, no profile row created.
        b = Asset.objects.create(name="B", asset_tag="MIG-B", circuit="Bench C-3")
        # C: nothing interesting → no profile row.
        c = Asset.objects.create(name="C", asset_tag="MIG-C")
        # D: circuit AND an explicit breaker_location → circuit dropped, the
        # breaker_location is kept as-is.
        d = Asset.objects.create(
            name="D",
            asset_tag="MIG-D",
            circuit="drop-me",
            breaker_location="Panel A/9",
        )

        executor2 = _migrate(END)
        new_apps = executor2.loader.project_state(END).apps
        ASR = new_apps.get_model("facilities", "AssetSiteRequirements")
        AssetNew = new_apps.get_model("inventory", "Asset")

        # A → profile carries breaker/disconnect/bools; circuit dropped, its
        # breaker_location stays blank (a real breaker already covers it).
        profile_a = ASR.objects.get(asset_id=a.id)
        assert profile_a.breaker_id == breaker.id
        assert profile_a.disconnect_id == disc.id
        assert profile_a.needs_compressed_air is True
        assert profile_a.needs_ventilation is True
        assert AssetNew.objects.get(id=a.id).breaker_location == ""

        # B → circuit folded into breaker_location, no profile row.
        assert not ASR.objects.filter(asset_id=b.id).exists()
        assert AssetNew.objects.get(id=b.id).breaker_location == "Bench C-3"

        # C → untouched.
        assert not ASR.objects.filter(asset_id=c.id).exists()

        # D → circuit dropped; explicit breaker_location preserved.
        assert not ASR.objects.filter(asset_id=d.id).exists()
        assert AssetNew.objects.get(id=d.id).breaker_location == "Panel A/9"
    finally:
        _migrate(executor.loader.graph.leaf_nodes())
