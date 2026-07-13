"""Reusable DRF serializer fields shared across apps.

Currently home to :class:`TypedTargetField`, the serializer counterpart to
:class:`inventory.models.typed_target.TypedTargetModel` (#884).
"""

from rest_framework import serializers


class TypedTargetField(serializers.Field):
    """Read-only field rendering ``{"target_type", "target_id"}`` for a
    :class:`~inventory.models.typed_target.TypedTargetModel` instance.

    Added **additively** beside a serializer's existing flat FK fields — it
    surfaces the model's typed-target accessor as a compact, ``scanner``-aligned
    ``{target_type, target_id}`` pair without removing any legacy field.

    Bound with ``source="*"`` (the default) so the whole model instance is
    passed to :meth:`to_representation`. The default reads the ``target`` /
    ``target_type`` accessor pair; pass ``object_attr`` / ``type_attr`` for the
    domain-named variants (``scanned_target`` / ``scanned_target_type``,
    ``origin`` / ``origin_type``)::

        target = TypedTargetField()
        scanned_target = TypedTargetField(
            object_attr="scanned_target", type_attr="scanned_target_type"
        )
    """

    def __init__(self, *, object_attr="target", type_attr="target_type", **kwargs):
        self.object_attr = object_attr
        self.type_attr = type_attr
        kwargs["read_only"] = True
        kwargs.setdefault("source", "*")
        super().__init__(**kwargs)

    def to_representation(self, instance):
        target = getattr(instance, self.object_attr)
        return {
            "target_type": getattr(instance, self.type_attr),
            "target_id": None if target is None else target.pk,
        }
