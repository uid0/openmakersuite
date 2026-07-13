"""Shared "typed target" abstraction for the parallel-nullable-FK pattern (#884).

Several models across the codebase model *"this row points at one of several
target types"* as a set of parallel nullable FKs (an ``asset`` XOR ``location``
XOR ``inventory_item`` FK, say) rather than a ContentType generic relation. That
choice is deliberate — it keeps the per-target FK, its ``on_delete`` behaviour
and a DB ``CheckConstraint`` (see :class:`~forgekey.models.IndicatorBinding`,
the canonical 3-layer exactly-one). The downside is the *accessor* logic
(``which target is set?`` / ``give me the one object``) gets hand-written and
duplicated as ``if asset / elif location / elif item`` branches wherever the row
is consumed.

:class:`TypedTargetModel` is the single home for that accessor logic. A subclass
declares its slots in :attr:`TypedTargetModel.TARGET_FIELDS` and gets:

* :attr:`~TypedTargetModel.target` — the single non-null related object (or
  ``None``).
* :attr:`~TypedTargetModel.target_type` — a ``scanner.resolvers``-aligned token
  (``"inventory_item"`` / ``"asset"`` / ``"location"`` / …) for whichever slot
  is set, so wire payloads speak the same vocabulary as the scan dispatcher.
* a generic :meth:`~TypedTargetModel.clean` enforcing the declared
  :attr:`~TypedTargetModel.TARGET_MODE` (``"exactly_one"`` / ``"at_most_one"``).
* :meth:`~TypedTargetModel.exactly_one_constraint`, a one-liner helper that
  emits the matching ``Meta`` ``CheckConstraint``.

The mixin contributes **no database fields** (only a plain-tuple class attribute,
properties and methods), so adopting it on an existing concrete model is a
state-only change — no migration is generated unless the model *also* opts into
a new ``CheckConstraint`` via the helper.

Modelled on :class:`inventory.models.ownership.OwnableModel` (abstract-mixin
structure) and :class:`forgekey.models.IndicatorBinding` (the 3-layer
exactly-one shape).
"""

from dataclasses import dataclass
from typing import Optional, Sequence

from django.core.exceptions import ValidationError
from django.db import models

# Sentinel distinguishing "attribute is absent" from "attribute is None" when
# probing for a FK's ``<field>_id`` shadow attribute.
_UNSET = object()


@dataclass(frozen=True)
class TargetField:
    """Declares one typed-target slot on a :class:`TypedTargetModel` subclass.

    ``token``
        The ``scanner.resolvers``-aligned string returned by
        :attr:`TypedTargetModel.target_type` when this slot is the active one
        (e.g. ``"asset"``, ``"location"``, ``"inventory_item"``, ``"freeform"``).
    ``field``
        The model attribute inspected for presence. An FK is probed via its
        ``<field>_id`` shadow (so no row is fetched just to test presence); a
        non-FK fallback (a freeform ``CharField``) is tested for truthiness.
    ``value``
        Optional dotted attribute path to the related object exposed as
        :attr:`TypedTargetModel.target`. Defaults to ``field`` itself; set it
        when the object lives one hop away (e.g. a PO line's ``item_supplier``
        FK whose *target* is ``item_supplier.item``).
    ``has_object``
        ``False`` for slots that carry a type but no related row — a freeform
        text description. ``target`` returns ``None`` for such a slot while
        ``target_type`` still reports its token.
    """

    token: str
    field: str
    value: Optional[str] = None
    has_object: bool = True


def count_present_targets(values: Sequence) -> int:
    """Count how many of ``values`` are truthy.

    The single definition of the "how many targets are set?" rule, shared by
    :meth:`TypedTargetModel.clean` (over a model's FK slots) and the scan-input
    serializers (over raw request ids) so the exactly-one invariant can never
    drift between the two layers.
    """
    return sum(1 for value in values if value)


class TypedTargetModel(models.Model):
    """Abstract mixin contributing typed-target accessors + optional validation.

    Subclasses declare :attr:`TARGET_FIELDS` (ordered — the first present slot
    wins for :attr:`target` / :attr:`target_type`) and, optionally,
    :attr:`TARGET_MODE` to opt into cardinality validation in :meth:`clean`.
    """

    #: Ordered slots for this model. Override on the subclass.
    TARGET_FIELDS: Sequence[TargetField] = ()

    #: ``"exactly_one"`` | ``"at_most_one"`` | ``None``. ``None`` (the default)
    #: means :meth:`clean` performs no cardinality check — for models whose
    #: slots are legitimately all-null (``SET_NULL`` audit rows) or zero-or-one
    #: (optional provenance FKs). Opt in explicitly where the invariant holds.
    TARGET_MODE: Optional[str] = None

    class Meta:
        abstract = True

    def _present_target_fields(self) -> list:
        """Return the declared slots that are currently set, in declared order."""
        present = []
        for spec in self.TARGET_FIELDS:
            fk_id = getattr(self, f"{spec.field}_id", _UNSET)
            if fk_id is not _UNSET:
                is_present = bool(fk_id)
            else:
                is_present = bool(getattr(self, spec.field, None))
            if is_present:
                present.append(spec)
        return present

    @property
    def target(self):
        """The single active related object, or ``None``.

        Returns the first present slot's object (resolving ``TargetField.value``
        when the object lives one hop away). ``None`` when nothing is set or the
        active slot carries no related row (a freeform slot).
        """
        present = self._present_target_fields()
        if not present:
            return None
        spec = present[0]
        if not spec.has_object:
            return None
        obj = self
        for attr in (spec.value or spec.field).split("."):
            obj = getattr(obj, attr, None)
            if obj is None:
                return None
        return obj

    @property
    def target_type(self) -> Optional[str]:
        """The ``scanner.resolvers`` token for the active slot, or ``None``."""
        present = self._present_target_fields()
        return present[0].token if present else None

    def clean(self):
        """Enforce :attr:`TARGET_MODE` across the declared slots.

        A no-op unless :attr:`TARGET_MODE` is set, so mixin adopters that only
        want the read accessors inherit a harmless ``clean``.
        """
        super().clean()
        if not self.TARGET_MODE:
            return
        # Each present slot is a truthy TargetField, so counting truthy entries
        # of the present list is its length — via the shared primitive.
        count = count_present_targets(self._present_target_fields())
        labels = ", ".join(spec.field for spec in self.TARGET_FIELDS)
        if self.TARGET_MODE == "exactly_one" and count != 1:
            raise ValidationError(f"Exactly one of {labels} must be set.")
        if self.TARGET_MODE == "at_most_one" and count > 1:
            raise ValidationError(f"At most one of {labels} may be set.")

    @staticmethod
    def exactly_one_constraint(name: str, fields: Sequence[TargetField]) -> models.CheckConstraint:
        """Build the ``Meta`` ``CheckConstraint`` mirroring an exactly-one rule.

        Emits ``(a NOT NULL AND b NULL AND …) OR (b NOT NULL AND a NULL AND …) …``
        over the FK columns behind ``fields`` — the DB last-line-of-defence that
        matches the ``exactly_one`` :attr:`TARGET_MODE`. Usable directly in a
        subclass ``Meta.constraints`` as a single line.
        """
        condition = None
        field_names = [spec.field for spec in fields]
        for name_present in field_names:
            term = models.Q(**{f"{name_present}__isnull": False})
            for other in field_names:
                if other != name_present:
                    term &= models.Q(**{f"{other}__isnull": True})
            condition = term if condition is None else condition | term
        return models.CheckConstraint(name=name, condition=condition)
