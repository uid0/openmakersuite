"""First-party migration namespace for django-hordak.

Wired via ``settings.MIGRATION_MODULES = {"hordak": "config.hordak_migrations"}``
so OMS can OWN one reconciliation migration on top of hordak's shipped graph.

Why this exists
---------------
hordak 2.0.0 ships migrations whose ``Leg`` money columns were frozen against a
different django-money / py-moneyed / babel than this repo pins (see
``backend/requirements.txt``): its shipped state is ``max_digits=20`` with a
2024 full-currency ``choices`` list, whereas the installed models render
``max_digits=13`` (``HORDAK_MAX_DIGITS``) with a USD-only ``choices`` list
(``CURRENCIES=("USD",)``). That mismatch is INHERENT to installing hordak in
this environment — it exists no matter which currency we pick — and would fail
CI's ``makemigrations --check`` drift gate the moment hordak is added to
``INSTALLED_APPS``. We cannot commit a migration into the pip-installed hordak
package, so instead we extend this package's ``__path__`` to include hordak's
own migrations directory. Django then discovers hordak's shipped ``0001..0054``
from site-packages AND our local ``0055_*`` reconciliation migration from here.

The recorded app label stays ``hordak`` — only the on-disk source is
redirected — so ``django_migrations`` rows are unchanged. hordak's shipped
migrations use absolute imports only and load their ``.sql`` sidecars via
``Path(__file__).parent`` (which still resolves to hordak's own directory
because those modules are physically loaded from there), so the redirect is
safe. hordak is pinned (``==2.0.0``); a version bump must revisit this shim.
"""

import os

import hordak.migrations

# hordak's shipped migrations (0001..0054, from site-packages) FIRST so they
# are discovered, then this directory for our local reconciliation migration.
__path__ = list(hordak.migrations.__path__) + [os.path.dirname(__file__)]
