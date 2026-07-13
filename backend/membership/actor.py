"""Actor-identity convention for OpenMakerSuite writes (#888).

OMS records "who did this" for actions that may be taken by three kinds of
actor, and we want ONE uniform shape that covers all three:

* **open / anonymous** — a walk-up kiosk scan or a public QR form. No auth; the
  actor is at best a free-text name the person typed (very often nothing).
* **authenticated** — a signed-in :class:`membership.User`; we have a real FK.
* **system** — an automated / background action (Celery beat, an inbound
  webhook). No user row; represented by a fixed label in the ``*_name`` string.

The convention
--------------
Each actor *role* on a model is stored as a **pair** of columns:

``<role>_user``
    ``ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
    null=True, blank=True)`` — the auth/system link. ``null`` means the actor
    was anonymous, a system process, or (for a backfilled legacy row) simply
    unrecoverable. ``SET_NULL`` so deleting a user never destroys the audit row.

``<role>_name``
    a ``CharField`` — the anon-supplied or display name, and the human-readable
    fallback whenever ``<role>_user`` is null. On a model that ALREADY carries a
    legacy free-text "who" column (e.g. ``FixtureRefillRequest.requested_by``),
    that existing column IS the ``<role>_name`` half: keep it, keep its *name*
    (the serializer / frontend read it), and just ADD the ``<role>_user`` FK
    beside it. Do not rename the legacy column — that would break the API.

Single-actor models use the bare role ``actor`` → ``actor_user`` /
``actor_name``. Multi-role models use a distinct prefix per role, e.g.
``requested_user`` / ``requested_by`` and ``resolved_user`` / ``resolved_by``.

Reads collapse the pair to one string through :func:`actor_display`: the
signed-in user's ``handle`` (falling back to ``username``) when the FK is set,
otherwise the stored name, otherwise ``"Anonymous"``. This generalises the
``handle or username`` precedent already open-coded at ``inventory/views.py``
(``resolve_problem``) and ``checklists/views.py``, and mirrors the audit-table
``actor`` FK convention used in the forgekey / reorder_queue / donations /
customization / notifications models.

Adopting the convention on a write path
---------------------------------------
* set ``<role>_user = request.user`` when ``request.user.is_authenticated``,
  else leave it null — and do NOT gate the endpoint on auth if it was open
  before (keep ``AllowAny`` where it was ``AllowAny``);
* set ``<role>_name`` to the authenticated actor's ``handle or username``, or
  the anon-supplied name (unchanged behaviour) otherwise;
* for a system action, leave ``<role>_user`` null and set ``<role>_name`` to a
  fixed label such as :data:`SYSTEM_ACTOR`.

Follow-up migration targets
---------------------------
Legacy free-text "who" columns that should grow a paired ``<role>_user`` FK by
the same recipe as ``FixtureRefillRequest`` (this module's flagship):

* ``reorder_queue.ReorderRequest.requested_by``
* ``checklists.ChecklistCompletion.user`` / ``user_name`` (already an FK + a
  string — the closest to the target shape; adopt rename-free)
* ``inventory.AssetProblem.reported_by`` / ``resolved_by``
* ``inventory.LocationProblem.reported_by`` / ``resolved_by``
* ``location_checkins`` actor FKs
* ``donations`` actor FKs
"""

from __future__ import annotations

#: Display fallback when no user and no name are known (anonymous actor).
ANONYMOUS_ACTOR = "Anonymous"

#: Conventional ``<role>_name`` label for an automated / background actor.
SYSTEM_ACTOR = "System"


def actor_display(user, name=""):
    """Collapse an actor ``(user, name)`` pair to a single display string.

    * an authenticated ``user`` → ``user.handle or user.username``;
    * otherwise the stored ``name`` (anon-supplied / system label) if truthy;
    * otherwise :data:`ANONYMOUS_ACTOR` (``"Anonymous"``).

    ``user`` may be a :class:`membership.User`, ``None``, or an unauthenticated
    ``AnonymousUser`` — the latter two both fall through to ``name``. ``name``
    may be ``None`` or ``""``. The function never raises for a null actor, which
    is the common case for the open (anonymous) write paths this convention
    exists to support.
    """
    if user is None or not getattr(user, "is_authenticated", False):
        return name or ANONYMOUS_ACTOR
    return getattr(user, "handle", None) or user.username
